"""Authenticated API, deterministic inbox and optional wearable/push integrations."""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import ipaddress
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
from threading import Lock
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import CompanionAgent
from . import policy
from .store import EventSuppressedError, Store, uid

logger=logging.getLogger(__name__)


class AuthRateLimiter:
    """Bounded single-process abuse protection; multi-replica deployments need an edge/shared limiter."""
    def __init__(self, window_seconds, limits):
        self.window_seconds=window_seconds
        self.limits=limits
        self.buckets=OrderedDict()
        self.lock=Lock()

    def check(self, route, address, now):
        key=(route,address)
        stamp=now.timestamp()
        with self.lock:
            # Entries are capped even when arbitrary peers hit the public listener.
            queue=self.buckets.pop(key,deque())
            while queue and queue[0]<=stamp-self.window_seconds:
                queue.popleft()
            if len(queue)>=self.limits[route]:
                self.buckets[key]=queue
                return max(1,int(queue[0]+self.window_seconds-stamp)+1)
            queue.append(stamp)
            self.buckets[key]=queue
            while len(self.buckets)>10_000:
                self.buckets.popitem(last=False)
        return None


def _proxy_client(request, trusted_networks):
    """Only remove explicitly trusted proxy hops; arbitrary XFF is not an identity."""
    peer=request.client.host if request.client else 'unknown'
    def trusted(address):
        try:
            value=ipaddress.ip_address(address)
            return any(value in network for network in trusted_networks)
        except ValueError:
            return False
    if not trusted(peer):
        return peer
    try:
        hops=[str(ipaddress.ip_address(x.strip())) for x in request.headers.get('x-forwarded-for','').split(',') if x.strip()]
    except ValueError:
        return peer
    # Walk from our known socket peer toward the original client. A forged
    # leftmost value never overrides an untrusted hop closer to the proxy.
    for address in reversed(hops):
        if not trusted(address):
            return address
    return peer


def zone_valid(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError):
        raise ValueError('请选择有效的 IANA 时区')
    return value


class Payload(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class Register(Payload):
    name:str=Field(min_length=1,max_length=80)
    email:str=Field(min_length=3,max_length=200)
    password:str=Field(min_length=10,max_length=256)
    timezone:str='Asia/Shanghai'
    consent_health_data:Literal[True]

    @field_validator('timezone')
    @classmethod
    def timezone_valid(cls,value):
        return zone_valid(value)

    @field_validator('email')
    @classmethod
    def email_valid(cls,value):
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):
            raise ValueError('请输入有效邮箱')
        return value.lower()


class Login(Payload):
    email:str=Field(min_length=3,max_length=200)
    password:str=Field(min_length=1,max_length=256)


class EventInput(Payload):
    kind:Literal['steps','sleep','workout','meal','water','mood']
    value:float|None=Field(ge=0,le=1_000_000,allow_inf_nan=False)
    unit:str=Field(min_length=1,max_length=24)
    start_at:AwareDatetime
    end_at:AwareDatetime|None=None
    source:str=Field(min_length=1,max_length=80)
    external_id:str|None=Field(default=None,max_length=250)
    metadata:dict=Field(default_factory=dict)

    @model_validator(mode='after')
    def bounds(self):
        if self.value is None and (self.kind!='meal' or not str(self.metadata.get('note','')).strip()):
            raise ValueError('缺少量化值时，仅能保存带文字说明的饮食记录')
        if self.end_at and self.end_at<self.start_at:
            raise ValueError('结束时间不能早于开始时间')
        if len(str(self.metadata))>8000:
            raise ValueError('记录备注过长')
        if 'summary_date' in self.metadata:
            if not isinstance(self.metadata['summary_date'],str):
                raise ValueError('汇总日期须为 ISO 日期字符串')
            date.fromisoformat(self.metadata['summary_date'])
        if 'is_complete_day' in self.metadata and not isinstance(self.metadata['is_complete_day'],bool):
            raise ValueError('完整记录日标记必须是布尔值')
        allowed={'steps':{'count','steps'},'sleep':{'h','hour','hours','min','minute','minutes','s','second','seconds'},
                 'workout':{'h','hour','hours','min','minute','minutes','s','second','seconds'},
                 'water':{'ml','l','liter','litre'},'mood':{'score','rating'},'meal':{'entry','count','kcal','meal'}}
        if self.unit.lower() not in allowed[self.kind]:
            raise ValueError('该记录类型的单位不受支持')
        if self.kind=='mood' and not 1<=self.value<=5:
            raise ValueError('心情分数须为 1 到 5')
        return self


class MemoryInput(Payload):
    text:str=Field(min_length=1,max_length=2000)
    category:Literal['preference','goal','fact','pattern']='preference'


class MemoryEdit(Payload):
    text:str=Field(min_length=1,max_length=2000)


class ChatInput(Payload):
    message:str=Field(min_length=1,max_length=3000)


class PreferencesInput(Payload):
    enabled:bool|None=None
    model_consent:bool|None=None
    quiet_start:str|None=None
    quiet_end:str|None=None
    daily_limit:int|None=Field(default=None,ge=0,le=10)
    reminder_categories:list[Literal['sleep','activity','meal']]|None=None
    timezone:str|None=None

    @field_validator('timezone')
    @classmethod
    def timezone_valid(cls,value):
        return zone_valid(value) if value is not None else value

    @field_validator('quiet_start','quiet_end')
    @classmethod
    def time_valid(cls,value):
        if value is not None and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
            raise ValueError('免打扰时间应为 HH:MM')
        return value


class Feedback(Payload):
    action:Literal['done','snooze','dismiss','disable']
    minutes:int=Field(default=30,ge=5,le=1440)


class DeviceInput(Payload):
    token:str=Field(min_length=20,max_length=4096)
    platform:Literal['android','ios']


def create_app(db_path=None,start_scheduler=False,clock=None,now=None,memory_adapter=None,wearables_client=None,web_dir=None):
    """Factory: production enables the scheduler explicitly; tests inject a deterministic clock."""
    public_mode=os.getenv('COMPANION_PUBLIC_MODE','false').lower()=='true'
    allowed_hosts=[value.strip() for value in os.getenv('COMPANION_ALLOWED_HOSTS','').split(',') if value.strip()]
    if public_mode and (not allowed_hosts or any('*' in value or '://' in value or '/' in value for value in allowed_hosts)):
        raise ValueError('Public mode requires explicit COMPANION_ALLOWED_HOSTS without wildcards or URLs')
    trusted_networks=[ipaddress.ip_network(value.strip(),strict=False) for value in os.getenv('COMPANION_TRUSTED_PROXY_IPS','').split(',') if value.strip()]
    rate_enabled=os.getenv('COMPANION_RATE_LIMIT_ENABLED','true' if public_mode else 'false').lower()=='true'
    def bounded_setting(name, default, minimum=1, maximum=3600):
        value=int(os.getenv(name,str(default)))
        if not minimum<=value<=maximum:
            raise ValueError(f'{name} must be between {minimum} and {maximum}')
        return value
    limiter=AuthRateLimiter(bounded_setting('COMPANION_AUTH_RATE_WINDOW_SECONDS',60),{
        '/api/auth/login':bounded_setting('COMPANION_LOGIN_RATE_LIMIT',10),
        '/api/auth/register':bounded_setting('COMPANION_REGISTER_RATE_LIMIT',3),
        '/api/demo':bounded_setting('COMPANION_DEMO_RATE_LIMIT',2),
    })
    public_demo_enabled=os.getenv('COMPANION_PUBLIC_DEMO_ENABLED','false').lower()=='true'
    demo_account_cap=bounded_setting('COMPANION_PUBLIC_DEMO_MAX_ACCOUNTS',100,maximum=1000)
    store=Store(db_path or os.getenv('COMPANION_DB_PATH','data/companion.sqlite3'))
    time_source=clock or (lambda: now) if now is not None else clock
    time_source=time_source or (lambda:datetime.now(timezone.utc))
    def current_time():
        value=time_source()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError('Application clock must be timezone-aware')
        return value.astimezone(timezone.utc)

    agent=CompanionAgent()
    upstream_locks={}
    def upstream_lock(user_id):
        if user_id not in upstream_locks:
            upstream_locks[user_id]=asyncio.Lock()
        return upstream_locks[user_id]
    if memory_adapter is None:
        try:
            from .memory_adapter import MemoryAdapter
            memory_adapter=MemoryAdapter.from_env()
        except ImportError:
            memory_adapter=None

    def user_data(user):
        data=dict(user)
        data['timezone']=store.singleton('preferences',user['id']).get('timezone',user['timezone'])
        return data

    def memory_sync(user_id):
        if not memory_adapter or not memory_adapter.enabled:
            return 'disabled'
        prefs=store.singleton('preferences',user_id)
        if not prefs.get('model_consent',False):
            if not prefs.get('_memory_index_used'):
                return 'consent_required'
            try:
                memory_adapter.delete_user(user_id)
                prefs['_memory_index_used']=False
                store.singleton('preferences',user_id,prefs)
                return 'cleared'
            except Exception:
                logger.warning('Semantic memory erasure after consent revocation failed')
                return 'failed'
        try:
            # Mark before the request: partial external writes must still be erased on a later retry.
            prefs['_memory_index_used']=True
            store.singleton('preferences',user_id,prefs)
            memory_adapter.sync(user_id,store.list('memories',user_id))
            return 'synced'
        except Exception:
            logger.warning('Semantic memory synchronization failed for a user')
            return 'failed'

    def evaluate_user(user):
        now_value=current_time()
        user_id=user['id']
        # The entire selection/creation is serialized so concurrent API and background runs cannot over-spend the budget.
        with store.transaction():
            prefs=store.singleton('preferences',user_id)
            events=store.list('events',user_id)
            memories=store.list('memories',user_id)
            result=agent.trends(events,now_value,prefs['timezone'],memories)
            valid_ids={e['id'] for e in events}|{m['id'] for m in memories}
            existing=store.list('reminders',user_id)
            for item in existing:
                if item['status'] not in {'pending','snoozed'}:
                    continue
                if item['delivery_status']=='sending' and now_value-policy.timestamp(item.get('sending_at',item['created_at']))>=timedelta(minutes=5):
                    item['delivery_status']='inbox'
                    item['delivery_note']='上次发送中断，已恢复待发送；收件箱记录始终保留'
                    store.put('reminders',user_id,item)
                matching=next((c for c in result['candidates'] if c['category']==item['category']),None)
                if not prefs['enabled'] or item['category'] not in prefs['reminder_categories'] or not prefs['daily_limit']:
                    item.update(status='cancelled',delivery_status='cancelled',cancel_reason='disabled')
                    store.put('reminders',user_id,item)
                elif not matching or not set(item['evidence_ids'])<=valid_ids or set(item['evidence_ids'])!=set(matching['evidence_ids']):
                    item.update(status='cancelled',delivery_status='cancelled',cancel_reason='evidence_no_longer_actionable')
                    store.put('reminders',user_id,item)
                elif policy.timestamp(item['expires_at'])<=now_value:
                    item.update(status='expired',delivery_status='expired')
                    store.put('reminders',user_id,item)
                elif item['status']=='snoozed' and policy.timestamp(item['scheduled_at'])<=now_value:
                    relevant=[e for e in events if e['id'] in item['evidence_ids']]
                    if not set(item['evidence_ids'])<=valid_ids or not matching or not policy.has_fresh_evidence(relevant,now_value,36,prefs['timezone']):
                        item.update(status='cancelled',delivery_status='cancelled',cancel_reason='evidence_stale')
                    elif not prefs['enabled'] or item['category'] not in prefs['reminder_categories']:
                        item.update(status='cancelled',delivery_status='cancelled')
                    elif not policy.is_quiet(now_value,prefs):
                        day=now_value.astimezone(ZoneInfo(prefs['timezone'])).date().isoformat()
                        previous_budget=item.get('budget_date') or policy.timestamp(item['created_at']).astimezone(ZoneInfo(prefs['timezone'])).date().isoformat()
                        if previous_budget!=day and policy.daily_count(existing,now_value,prefs['timezone'])>=prefs['daily_limit']:
                            item['scheduled_at']=(now_value+timedelta(minutes=15)).isoformat()
                        else:
                            item.update(status='pending',delivery_status='inbox',budget_date=day)
                    store.put('reminders',user_id,item)
            # Rebuild only machine-derived summaries. User-edited memories keep their authority.
            insights=result['facts']['insights']
            current_memory_ids={'derived-'+user_id+'-'+policy.evidence_fingerprint(i['evidence_ids']) for i in insights}
            for old_memory in store.list('memories',user_id):
                if old_memory['origin']=='derived' and old_memory['id'] not in current_memory_ids:
                    store.delete('memories',user_id,old_memory['id'])
            for insight in insights:
                key=policy.evidence_fingerprint(insight['evidence_ids'])
                memory_id='derived-'+user_id+'-'+key
                existing_memory=store.get('memories',user_id,memory_id)
                if existing_memory is None and memory_id not in prefs.get('_suppressed_memory_ids',[]):
                    store.put('memories',user_id,{'id':memory_id,'text':insight['body'],'category':'pattern','origin':'derived',
                        'confidence':1.0,'evidence_ids':insight['evidence_ids'],'created_at':now_value.isoformat()})
            skipped=[]
            if not prefs['enabled']:
                return {'created':0,'skipped':['disabled']}
            if policy.is_quiet(now_value,prefs):
                return {'created':0,'skipped':['quiet_hours']}
            created=0
            count=policy.daily_count(existing,now_value,prefs['timezone'])
            used={r['dedupe_key'] for r in existing}
            for candidate in result['candidates']:
                if candidate['category'] not in prefs['reminder_categories']:
                    skipped.append('category_disabled')
                    continue
                dedupe=policy.candidate_key(candidate,now_value,prefs['timezone'])
                if dedupe in used:
                    skipped.append('already_evaluated')
                    continue
                if count>=prefs['daily_limit']:
                    skipped.append('daily_budget')
                    continue
                item={'id':uid(),**candidate,'status':'pending','created_at':now_value.isoformat(),
                      'scheduled_at':now_value.isoformat(),'expires_at':(now_value+timedelta(hours=24)).isoformat(),
                      'delivery_status':'inbox','dedupe_key':dedupe,'budget_date':now_value.astimezone(ZoneInfo(prefs['timezone'])).date().isoformat()}
                store.put('reminders',user_id,item)
                used.add(dedupe)
                created+=1
                count+=1
            if not result['candidates']:
                skipped.append('no_fresh_actionable_evidence')
        return {'created':created,'skipped':skipped}

    async def deliver_user(user):
        """Inbox is always available. Native push is conditional and server-selected."""
        try:
            from .integrations import send_push
        except ImportError:
            return
        user_id=user['id']
        prefs=store.singleton('preferences',user_id)
        if not prefs.get('enabled') or policy.is_quiet(current_time(),prefs):
            return
        tokens=[d['token'] for d in store.list('devices',user_id)]
        if not tokens:
            return
        # Revalidate against the newest observations before any external delivery.
        await run_in_threadpool(evaluate_user,user)
        for item in store.list('reminders',user_id):
            if item['status']!='pending' or item['delivery_status']!='inbox' or item['category'] not in prefs['reminder_categories']:
                continue
            if policy.timestamp(item['expires_at'])<=current_time():
                continue
            if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS') and not os.getenv('FCM_SERVICE_ACCOUNT_FILE'):
                continue
            # Persist the claim before awaiting to prevent simultaneous delivery attempts.
            with store.transaction():
                fresh=store.get('reminders',user_id,item['id'])
                if fresh['delivery_status']!='inbox' or fresh['status']!='pending':
                    continue
                if policy.push_daily_count(store.list('reminders',user_id),current_time(),prefs['timezone'])>=prefs['daily_limit']:
                    continue
                fresh['delivery_status']='sending'
                fresh['sending_at']=current_time().isoformat()
                fresh.setdefault('push_attempts',[]).append(fresh['sending_at'])
                store.put('reminders',user_id,fresh)
            try:
                sent=await send_push(tokens,'日常 · 陪伴','有一条新的生活提醒，打开应用查看。',{'reminder_id':item['id']})
                fresh['delivery_status']='queued' if sent.get('sent',0) else 'failed' if sent.get('configured') else 'inbox'
            except Exception:
                fresh['delivery_status']='failed'
                logger.warning('Optional push delivery failed')
            # Do not overwrite feedback submitted while the network request was in flight.
            with store.transaction():
                latest=store.get('reminders',user_id,item['id'])
                if latest and latest['status']=='pending':
                    latest['delivery_status']=fresh['delivery_status']
                    store.put('reminders',user_id,latest)

    async def background():
        while True:
            for user in store.users():
                try:
                    mapping=store.singleton('integrations',user['id'])
                    if mapping.get('wearable_user_id') and not mapping.get('upstream_deleted') and (
                        not mapping.get('last_sync_at') or current_time()-policy.timestamp(mapping['last_sync_at'])>=timedelta(minutes=15)):
                        try:
                            await sync_user(user)
                        except HTTPException:
                            logger.warning('Wearable background sync unavailable for a user')
                    await run_in_threadpool(evaluate_user,user)
                    await deliver_user(user)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning('Background evaluation failed for a user; continuing other accounts')
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(background()) if start_scheduler else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            store.close()

    app=FastAPI(title='日常·陪伴',version='0.1.0',lifespan=lifespan,
                docs_url=None if public_mode else '/docs',redoc_url=None if public_mode else '/redoc',
                openapi_url=None if public_mode else '/openapi.json')
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware,allowed_hosts=allowed_hosts,www_redirect=False)
    app.state.store=store
    app.state.agent=agent
    app.state.evaluate_user=evaluate_user
    app.state.deliver_user=deliver_user
    app.state.memory_adapter=memory_adapter
    app.state.clock=current_time

    @app.exception_handler(sqlite3.Error)
    async def database_error(request,exc):
        logger.error('Database operation failed')
        return JSONResponse(status_code=503,content={'detail':'数据服务暂时不可用，请稍后重试'})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # FastAPI's default includes submitted input, including invalid passwords.
        fields=list(dict.fromkeys(str(error['loc'][-1]) for error in exc.errors()))[:5]
        return JSONResponse(status_code=422,content={'detail':'请求参数有误，请检查：'+', '.join(fields)})

    @app.middleware('http')
    async def security_headers(request:Request,call_next):
        response=None
        try:
            length=int(request.headers.get('content-length','0') or '0')
            if length<0:
                raise ValueError('negative length')
        except ValueError:
            response=JSONResponse(status_code=400,content={'detail':'无效的请求长度'})
            length=0
        if response is None and length>64_000:
            response=JSONResponse(status_code=413,content={'detail':'请求内容过大'})
        if response is None and request.method=='POST' and request.url.path in limiter.limits and rate_enabled:
            retry=limiter.check(request.url.path,_proxy_client(request,trusted_networks),current_time())
            if retry:
                response=JSONResponse(status_code=429,content={'detail':'尝试次数较多，请稍后再试。'},headers={'Retry-After':str(retry)})
        if response is None and request.url.path.startswith('/api') and request.method in {'POST','PUT','PATCH','DELETE'}:
            # Enforce the byte cap even when Content-Length is absent/chunked.
            # Starlette's cached request replays _body to the downstream parser.
            chunks=[]
            total=0
            async for chunk in request.stream():
                total+=len(chunk)
                if total>64_000:
                    response=JSONResponse(status_code=413,content={'detail':'请求内容过大'})
                    break
                chunks.append(chunk)
            if response is None:
                request._body=b''.join(chunks)
        if response is None:
            response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
        if public_mode or request.url.path not in {'/docs','/redoc','/docs/oauth2-redirect'}:
            response.headers.setdefault('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if public_mode:
            response.headers['Strict-Transport-Security']='max-age=31536000'
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control']='no-store'
        return response

    def authenticated(authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):
            raise HTTPException(401,'请先登录')
        user=store.authenticate(authorization[7:],current_time())
        if user is None:
            raise HTTPException(401,'登录已过期，请重新登录')
        return user

    def own(table,user,item_id):
        item=store.get(table,user['id'],item_id)
        if item is None:
            raise HTTPException(404,'记录不存在')
        return item

    @app.get('/api/health')
    def health():
        if public_mode:
            return {'status':'ok'}
        return {'status':'ok','mode':'model' if agent.model_configured else 'local',
                'integrations':{'open_wearables':bool(os.getenv('OPEN_WEARABLES_API_KEY')),'memory':bool(memory_adapter and memory_adapter.enabled)}}

    @app.post('/api/auth/register')
    def register(data:Register):
        try:
            user=store.create_user(data.name,data.email,data.password,data.timezone,current_time())
        except sqlite3.IntegrityError:
            raise HTTPException(409,'该邮箱已注册')
        return {'token':store.session(user['id'],current_time()),'user':user_data(user)}

    @app.post('/api/auth/login')
    def login(data:Login):
        user=store.login(data.email,data.password)
        if user is None:
            raise HTTPException(401,'邮箱或密码不正确')
        return {'token':store.session(user['id'],current_time()),'user':user_data(user)}

    @app.post('/api/auth/logout')
    def logout(authorization:str|None=Header(default=None),user=Depends(authenticated)):
        store.revoke_session(user['id'],authorization[7:])
        return {'logged_out':True}

    @app.post('/api/demo')
    def demo():
        if public_mode and not public_demo_enabled:
            raise HTTPException(403,'公开演示暂未启用，请创建自己的账号。')
        now_value=current_time()
        with store.transaction():
            if public_mode and sum(bool(user.get('is_demo')) for user in store.users())>=demo_account_cap:
                raise HTTPException(429,'演示名额暂满，请创建自己的账号。')
            user=store.create_user('小林 · 合成数据',uid()+'@demo.invalid',secrets.token_urlsafe(30),'Asia/Shanghai',now_value,True)
        zone=ZoneInfo(user['timezone'])
        local=now_value.astimezone(zone)
        for offset in reversed(range(21)):
            day=local.date()-timedelta(days=offset)
            recorded=datetime.combine(day,datetime.min.time(),zone)+timedelta(hours=23,minutes=55) if offset else local-timedelta(minutes=3)
            sleep_end=datetime.combine(day,datetime.min.time(),zone)+timedelta(hours=7)
            if sleep_end>local:
                sleep_end=local-timedelta(minutes=2)
            steps=6400+(offset%5)*530 if offset>=3 else 2850+offset*170
            sleep_hours=7.3+(offset%3)*.2 if offset>=3 else 5.7+offset*.1
            records=[('steps',steps,'count',recorded,None,{'is_complete_day':offset>0}),
                     ('sleep',sleep_hours,'h',sleep_end-timedelta(hours=sleep_hours),sleep_end,{}),
                     ('workout',25 if offset%3==0 else 10,'min',recorded,None,{}),
                     ('water',1200+(offset%4)*150,'ml',recorded,None,{})]
            for kind,value,unit,start,end,meta in records:
                store.upsert_event(user['id'],{'kind':kind,'value':value,'unit':unit,'start_at':start.isoformat(),
                    'end_at':end.isoformat() if end else None,'source':'synthetic_demo','external_id':f'demo-{day}-{kind}',
                    'metadata':{**meta,'synthetic':True,'note':'明确标记的合成演示记录'}},now_value)
        store.put('memories',user['id'],{'id':uid(),'text':'我想逐步建立规律作息，也喜欢饭后散步。','category':'goal','origin':'user',
                   'confidence':1.0,'evidence_ids':[],'created_at':now_value.isoformat()})
        # Populate the memory timeline without spending reminder budget before the user requests evaluation.
        result=agent.trends(store.list('events',user['id']),now_value,user['timezone'])
        for insight in result['facts']['insights']:
            store.put('memories',user['id'],{'id':'derived-'+user['id']+'-'+policy.evidence_fingerprint(insight['evidence_ids']),
                'text':insight['body'],'category':'pattern','origin':'derived','confidence':1.0,
                'evidence_ids':insight['evidence_ids'],'created_at':now_value.isoformat()})
        return {'token':store.session(user['id'],now_value),'user':user_data(user)}

    @app.get('/api/me')
    def me(user=Depends(authenticated)):
        return user_data(user)

    @app.delete('/api/me')
    async def delete_me(user=Depends(authenticated)):
        mapping=store.singleton('integrations',user['id'])
        if mapping.get('wearable_user_id') and not mapping.get('upstream_deleted'):
            client=upstream()
            if client is None:
                raise HTTPException(503,'上游穿戴服务未配置，资料清理未完成；本地账号暂保留')
            try:
                await client.delete_user(mapping['wearable_user_id'])
                mapping['upstream_deleted']=True
                store.singleton('integrations',user['id'],mapping)
            except Exception:
                raise HTTPException(503,'上游穿戴资料清理未完成，账号暂保留，请稍后重试')
        if memory_adapter and memory_adapter.enabled and store.singleton('preferences',user['id']).get('_memory_index_used'):
            try:
                await run_in_threadpool(memory_adapter.delete_user,user['id'])
            except Exception:
                raise HTTPException(503,'外部记忆清理未完成，账号暂保留，请稍后重试')
        store.delete_user(user['id'])
        return {'deleted':True}

    @app.get('/api/dashboard')
    def dashboard(user=Depends(authenticated)):
        prefs=store.singleton('preferences',user['id'])
        result=agent.trends(store.list('events',user['id']),current_time(),prefs['timezone'])
        return {'user':user_data(user),**result['facts'],'mode':'local'}

    @app.get('/api/events')
    def events(user=Depends(authenticated)):
        return {'items':sorted(store.list('events',user['id']),key=lambda e:e['start_at'],reverse=True)}

    @app.post('/api/events')
    def add_event(data:EventInput,user=Depends(authenticated)):
        if data.start_at>current_time()+timedelta(minutes=5) or (data.end_at and data.end_at>current_time()+timedelta(minutes=5)):
            raise HTTPException(422,'行为记录不能在未来')
        try:
            event,created=store.upsert_event(user['id'],data.model_dump(mode='json'),current_time())
        except EventSuppressedError:
            raise HTTPException(409,'该来源记录已被删除并屏蔽自动导入；新记录请使用新的记录编号')
        return {'event':event,'created':created,'memory_sync':memory_sync(user['id'])}

    @app.delete('/api/events/{item_id}')
    def delete_event(item_id:str,user=Depends(authenticated)):
        event=own('events',user,item_id)
        with store.transaction():
            store.suppress_event(user['id'],event,current_time())
            store.bump_context(user['id'])
            store.invalidate(user['id'],item_id)
            store.delete('events',user['id'],item_id)
        return {'deleted':True,'memory_sync':memory_sync(user['id'])}

    @app.get('/api/memories')
    def memories(user=Depends(authenticated)):
        return {'items':store.list('memories',user['id'])}

    @app.post('/api/memories')
    def add_memory(data:MemoryInput,user=Depends(authenticated)):
        item={'id':uid(),**data.model_dump(),'origin':'user','confidence':1.0,'evidence_ids':[],'created_at':current_time().isoformat()}
        store.put('memories',user['id'],item)
        return {**item,'memory_sync':memory_sync(user['id'])}

    @app.patch('/api/memories/{item_id}')
    def edit_memory(item_id:str,data:MemoryEdit,user=Depends(authenticated)):
        item=own('memories',user,item_id)
        store.bump_context(user['id'])
        store.invalidate(user['id'],item_id)
        item.update(text=data.text,origin='user',confidence=1.0,evidence_ids=[])
        store.put('memories',user['id'],item)
        return {**item,'memory_sync':memory_sync(user['id'])}

    @app.delete('/api/memories/{item_id}')
    def delete_memory(item_id:str,user=Depends(authenticated)):
        item=own('memories',user,item_id)
        store.bump_context(user['id'])
        if item['origin']=='derived':
            prefs=store.singleton('preferences',user['id'])
            prefs['_suppressed_memory_ids']=list(set(prefs.get('_suppressed_memory_ids',[]))|{item_id})
            store.singleton('preferences',user['id'],prefs)
        store.invalidate(user['id'],item_id)
        store.delete('memories',user['id'],item_id)
        return {'deleted':True,'memory_sync':memory_sync(user['id'])}

    @app.get('/api/chat')
    def chat_history(user=Depends(authenticated)):
        return {'items':store.list('chat',user['id'])[-200:]}

    @app.post('/api/chat')
    def chat(data:ChatInput,user=Depends(authenticated)):
        user_id=user['id']
        remembered=re.match(r'^(?:请)?(?:帮我)?记住[：:，,\s]*(.+)$',data.message)
        if remembered and not any(mark in remembered[1] for mark in ['?','？']) and len(remembered[1])>=2:
            text=remembered[1].strip()
            if not any(m['text']==text and m['origin']=='user' for m in store.list('memories',user_id)):
                category='goal' if any(word in text for word in ['目标','希望','想要','计划']) else 'preference'
                store.put('memories',user_id,{'id':uid(),'text':text,'category':category,'origin':'user',
                    'confidence':1.0,'evidence_ids':[],'created_at':current_time().isoformat()})
        memories=store.list('memories',user_id)
        sync_status=memory_sync(user_id)
        if memory_adapter and memory_adapter.enabled and sync_status=='synced':
            try:
                hits=memory_adapter.search(user_id,data.message,limit=8)
                order={hit['id']:index for index,hit in enumerate(hits)}
                # Only SQL-authorized ids survive. Stale external content never supplies the text.
                memories=sorted(memories,key=lambda m:order.get(m['id'],100))
            except Exception:
                sync_status='failed'
        now_value=current_time()
        prefs=store.singleton('preferences',user_id)
        revision=prefs.get('_context_revision',0)
        history=[message for message in store.list('chat',user_id) if message.get('context_revision',0)==revision]
        result=agent.chat(store.list('events',user_id),memories,history,data.message,
                          now_value,prefs['timezone'],allow_model=prefs.get('model_consent',False))
        for role,content in [('user',data.message),('assistant',result['reply'])]:
            store.put('chat',user_id,{'id':uid(),'role':role,'content':content,'created_at':now_value.isoformat(),'context_revision':revision})
        return {'reply':result['reply'],'evidence':result['evidence'],'mode':result['mode'],'memory_sync':sync_status}

    @app.get('/api/preferences')
    def preferences(user=Depends(authenticated)):
        return {k:v for k,v in store.singleton('preferences',user['id']).items() if not k.startswith('_')}

    @app.patch('/api/preferences')
    def edit_preferences(data:PreferencesInput,user=Depends(authenticated)):
        prefs=store.singleton('preferences',user['id'])
        prefs.update(data.model_dump(exclude_none=True))
        prefs['reminder_categories']=list(dict.fromkeys(prefs['reminder_categories']))
        store.singleton('preferences',user['id'],prefs)
        evaluate_user(user)
        sync_status=memory_sync(user['id']) if data.model_consent is not None else None
        response={k:v for k,v in prefs.items() if not k.startswith('_')}
        if sync_status is not None:
            response['memory_sync']=sync_status
        return response

    @app.post('/api/evaluate')
    async def evaluate(user=Depends(authenticated)):
        result=await run_in_threadpool(evaluate_user,user)
        result['memory_sync']=await run_in_threadpool(memory_sync,user['id'])
        await deliver_user(user)
        return result

    @app.get('/api/reminders')
    def reminders(user=Depends(authenticated)):
        return {'items':list(reversed(store.list('reminders',user['id'])))}

    @app.post('/api/reminders/{item_id}/feedback')
    def feedback(item_id:str,data:Feedback,user=Depends(authenticated)):
        item=own('reminders',user,item_id)
        now_value=current_time()
        if policy.timestamp(item['expires_at'])<=now_value:
            item.update(status='expired',delivery_status='expired')
            store.put('reminders',user['id'],item)
        if item['status'] in {'cancelled','expired'}:
            raise HTTPException(409,'这条提醒已过期或依据发生变化，请重新评估')
        if data.action=='disable':
            prefs=store.singleton('preferences',user['id'])
            prefs['reminder_categories']=[c for c in prefs['reminder_categories'] if c!=item['category']]
            store.singleton('preferences',user['id'],prefs)
            item.update(status='dismissed',delivery_status='dismissed')
        elif data.action=='snooze':
            item.update(status='snoozed',scheduled_at=(now_value+timedelta(minutes=data.minutes)).isoformat(),delivery_status='scheduled')
        else:
            item.update(status='done' if data.action=='done' else 'dismissed',delivery_status='acknowledged')
        item['feedback_at']=now_value.isoformat()
        return store.put('reminders',user['id'],item)

    @app.get('/api/integrations/status')
    def integration_status(user=Depends(authenticated)):
        mapping=store.singleton('integrations',user['id'])
        return {'open_wearables':{'configured':bool(os.getenv('OPEN_WEARABLES_API_KEY') and (os.getenv('OPEN_WEARABLES_BASE_URL') or os.getenv('OPEN_WEARABLES_URL'))) or wearables_client is not None,
                'connected':bool(mapping.get('wearable_user_id') and not mapping.get('upstream_deleted')),'last_sync_at':mapping.get('last_sync_at')},
                'model':{'configured':agent.model_configured,'consented':store.singleton('preferences',user['id']).get('model_consent',False)},
                'push':{'configured':bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS') or os.getenv('FCM_SERVICE_ACCOUNT_FILE'))},
                'memory':{'configured':bool(memory_adapter and memory_adapter.enabled)}}

    def upstream():
        if wearables_client is not None:
            return wearables_client
        base,key=os.getenv('OPEN_WEARABLES_BASE_URL') or os.getenv('OPEN_WEARABLES_URL'),os.getenv('OPEN_WEARABLES_API_KEY')
        if not base or not key:
            return None
        from .integrations import WearablesClient
        return WearablesClient(base,key)

    @app.post('/api/integrations/open-wearables/connect')
    async def connect(user=Depends(authenticated)):
        client=upstream()
        if client is None:
            return {'configured':False,'detail':'管理员尚未配置 Open Wearables 服务'}
        try:
            async with upstream_lock(user['id']):
                mapping=store.singleton('integrations',user['id'])
                if not mapping.get('wearable_user_id') or mapping.get('upstream_deleted'):
                    created=await client.create_user(external_id=user['id'],name=user['name'],email=user['email'])
                    mapping={'wearable_user_id':str(created['id'])}
                    store.singleton('integrations',user['id'],mapping)
                tokens=await client.issue_sdk_tokens(mapping['wearable_user_id'])
                return {'configured':True,'wearable_user_id':mapping['wearable_user_id'],**tokens,'host':os.getenv('OPEN_WEARABLES_PUBLIC_URL') or getattr(client,'base_url','')}
        except Exception:
            raise HTTPException(503,'穿戴服务连接暂不可用，请检查管理员配置后重试')

    async def sync_user(user):
        client=upstream()
        mapping=store.singleton('integrations',user['id'])
        if client is None or not mapping.get('wearable_user_id'):
            raise HTTPException(409,'请先连接穿戴数据服务')
        now_value=current_time()
        # A rolling look-back also imports delayed records and corrections, independent of last poll time.
        start=now_value-timedelta(days=30)
        try:
            data=await client.fetch_events(mapping['wearable_user_id'],start,now_value)
            validated=[EventInput.model_validate(e).model_dump(mode='json') for e in data]
            imported=0
            with store.transaction():
                for item in validated:
                    if store.is_event_suppressed(user['id'],item['source'],item.get('external_id')):
                        continue
                    timezone_name=store.singleton('preferences',user['id'])['timezone']
                    if item.get('metadata',{}).get('granularity')=='daily_summary':
                        if date.fromisoformat(item['metadata']['summary_date'])>now_value.astimezone(ZoneInfo(timezone_name)).date():
                            continue
                    elif policy.event_time(item,timezone_name)>now_value+timedelta(minutes=5):
                            continue
                    _,created=store.upsert_event(user['id'],item,now_value)
                    imported+=int(created)
                mapping['last_sync_at']=now_value.isoformat()
                store.singleton('integrations',user['id'],mapping)
            return {'imported':imported,'detail':'已同步；重复记录会合并，原始观测时间用于提醒新鲜度判断',
                    'memory_sync':await run_in_threadpool(memory_sync,user['id'])}
        except Exception:
            raise HTTPException(503,'同步未完成，请稍后重试；未确认收到的数据不会作为行为事实')

    app.state.sync_user=sync_user

    @app.post('/api/integrations/open-wearables/sync')
    async def sync(user=Depends(authenticated)):
        async with upstream_lock(user['id']):
            return await sync_user(user)

    @app.post('/api/integrations/open-wearables/webhook')
    async def webhook(request:Request):
        from .integrations import verify_webhook
        payload=await request.body()
        if len(payload)>64_000:
            raise HTTPException(413,'事件内容过大')
        try:
            value=verify_webhook(payload,dict(request.headers))
        except Exception:
            raise HTTPException(401,'Webhook 签名无效或已过期')
        wearable_id=(value.get('data') or {}).get('user_id')
        message_id=request.headers.get('svix-id','')
        if not wearable_id or not message_id:
            raise HTTPException(422,'Webhook 缺少用户或消息标识')
        user=next((u for u in store.users() if store.singleton('integrations',u['id']).get('wearable_user_id')==wearable_id),None)
        if user is None:
            return {'accepted':True,'imported':0}
        if not store.claim_webhook(message_id,user['id'],current_time()):
            return {'accepted':True,'duplicate':True}
        try:
            async with upstream_lock(user['id']):
                result=await sync_user(user)
            await run_in_threadpool(evaluate_user,user)
            await deliver_user(user)
            store.finish_webhook(message_id,True)
            return {'accepted':True,'imported':result['imported']}
        except Exception:
            store.finish_webhook(message_id,False)
            raise HTTPException(503,'Webhook 同步未完成，请重试')

    @app.post('/api/devices')
    def device(data:DeviceInput,user=Depends(authenticated)):
        store.register_device(user['id'],data.token,data.platform)
        return {'registered':True}

    # Static mount is intentionally last so it can never shadow the API.
    web=Path(web_dir) if web_dir is not None else Path(__file__).resolve().parent.parent/'web'
    if web.exists():
        app.mount('/',StaticFiles(directory=web,html=True),name='web')
    return app
