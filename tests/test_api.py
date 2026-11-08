from datetime import datetime, timedelta, timezone
import importlib
import pytest
from fastapi.testclient import TestClient

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)

@pytest.fixture
def client(tmp_path):
    try:
        create_app = importlib.import_module('companion.app').create_app
    except (ImportError, AttributeError):
        pytest.fail('Companion create_app API has not been implemented')
    with TestClient(create_app(db_path=tmp_path / 'test.sqlite', start_scheduler=False, clock=lambda: NOW)) as c:
        yield c

def register(client, email='a@example.com'):
    response = client.post('/api/auth/register', json={'name':'小林','email':email,'password':'a-long-password','timezone':'Asia/Shanghai','consent_health_data':True})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}

def event(client, headers, **overrides):
    data = dict(kind='steps', value=1400, unit='count', start_at=(NOW-timedelta(hours=1)).isoformat(), source='manual', external_id='steps-1', metadata={})
    data.update(overrides)
    response = client.post('/api/events', headers=headers, json=data)
    assert response.status_code == 200, response.text
    return response.json()

def test_explicit_consent_login_and_isolation(client):
    denied = client.post('/api/auth/register', json={'name':'林','email':'no@example.com','password':'a-long-password','timezone':'Asia/Shanghai','consent_health_data':False})
    assert denied.status_code == 422
    a = register(client)
    b = register(client, 'b@example.com')
    item = event(client,a)['event']
    assert client.get('/api/events',headers=b).json()['items'] == []
    assert client.delete('/api/events/'+item['id'],headers=b).status_code == 404
    assert client.get('/api/me').status_code == 401
    assert client.post('/api/auth/login',json={'email':'a@example.com','password':'wrong-password'}).status_code == 401
    login = client.post('/api/auth/login',json={'email':'a@example.com','password':'a-long-password'})
    assert login.status_code == 200
    assert login.json()['token'] != a['Authorization'][7:]

def test_naive_times_negative_values_and_unknown_timezone_rejected(client):
    a=register(client)
    base=dict(kind='steps',value=10,unit='count',start_at='2026-11-08T12:00:00',source='manual')
    assert client.post('/api/events',headers=a,json=base).status_code == 422
    base.update(start_at=NOW.isoformat(),value=-10)
    assert client.post('/api/events',headers=a,json=base).status_code == 422
    assert client.patch('/api/preferences',headers=a,json={'timezone':'Missing/Zone'}).status_code == 422

def test_ingestion_deduplicates_and_dashboard_uses_current_local_day(client):
    a=register(client)
    first=event(client,a)
    again=event(client,a)
    assert first['created'] is True and again['created'] is False
    assert len(client.get('/api/events',headers=a).json()['items']) == 1
    corrected=event(client,a,value=2300)
    assert corrected['event']['id']==first['event']['id']
    data=client.get('/api/dashboard',headers=a).json()
    assert data['metrics']['steps']==2300
    assert data['mode']=='local'
    assert len(data['series'])>=7

def test_memories_user_edits_and_chat_persist_and_are_scoped(client):
    a=register(client)
    b=register(client,'b@example.com')
    r=client.post('/api/memories',headers=a,json={'text':'我希望每晚十点半睡觉','category':'goal'})
    assert r.status_code == 200
    memory=r.json()
    assert memory['origin']=='user'
    assert client.patch('/api/memories/'+memory['id'],headers=b,json={'text':'wrong'}).status_code==404
    updated=client.patch('/api/memories/'+memory['id'],headers=a,json={'text':'我希望每晚十一点睡觉'})
    assert updated.status_code==200
    chat=client.post('/api/chat',headers=a,json={'message':'我之前的目标是什么？'})
    assert chat.status_code==200,chat.text
    assert chat.json()['mode']=='local'
    assert '十一点' in chat.json()['reply']
    assert client.app.state.agent.graph_runs>0
    assert len(client.get('/api/chat',headers=a).json()['items'])==2
    assert client.get('/api/chat',headers=b).json()['items']==[]

def test_isolated_demo_has_21_days_and_fresh_evidence(client):
    r=client.post('/api/demo',json={})
    assert r.status_code==200,r.text
    h={'Authorization':'Bearer '+r.json()['token']}
    dashboard=client.get('/api/dashboard',headers=h).json()
    assert dashboard['user']['is_demo'] is True
    assert '合成' in dashboard['user']['name']
    assert len(dashboard['series'])==21
    assert datetime.fromisoformat(dashboard['last_sync_at'])>=NOW-timedelta(minutes=5)
    assert len(dashboard['insights'])>0
    other=client.post('/api/demo',json={}).json()
    assert other['user']['id']!=r.json()['user']['id']

def test_corrections_remove_dependent_patterns_and_pending_reminders(client):
    r=client.post('/api/demo',json={}).json()
    h={'Authorization':'Bearer '+r['token']}
    client.post('/api/evaluate',headers=h,json={})
    pending=client.get('/api/reminders',headers=h).json()['items']
    assert pending
    evidence=pending[0]['evidence_ids'][0]
    assert client.delete('/api/events/'+evidence,headers=h).status_code==200
    remaining=client.get('/api/reminders',headers=h).json()['items']
    assert all(evidence not in r['evidence_ids'] or r['status']=='cancelled' for r in remaining)
    memories=client.get('/api/memories',headers=h).json()['items']
    assert all(evidence not in m['evidence_ids'] for m in memories)

def test_evaluation_feedback_budget_and_idempotency(client):
    r=client.post('/api/demo',json={}).json()
    h={'Authorization':'Bearer '+r['token']}
    first=client.post('/api/evaluate',headers=h,json={})
    assert first.status_code==200
    assert first.json()['created']>=1
    assert client.post('/api/evaluate',headers=h,json={}).json()['created']==0
    reminders=client.get('/api/reminders',headers=h).json()['items']
    assert len(reminders)<=2
    item=reminders[0]
    snoozed=client.post('/api/reminders/'+item['id']+'/feedback',headers=h,json={'action':'snooze','minutes':30})
    assert snoozed.status_code==200,snoozed.text
    assert snoozed.json()['status']=='snoozed'
    assert datetime.fromisoformat(snoozed.json()['scheduled_at'])==NOW+timedelta(minutes=30)
    assert client.post('/api/reminders/'+item['id']+'/feedback',headers=h,json={'action':'disable'}).status_code==200
    assert item['category'] not in client.get('/api/preferences',headers=h).json()['reminder_categories']

def test_account_deletion_revokes_all_sessions(client):
    a=register(client)
    event(client,a)
    assert client.delete('/api/me',headers=a).status_code==200
    assert client.get('/api/me',headers=a).status_code==401
    assert client.post('/api/auth/login',json={'email':'a@example.com','password':'a-long-password'}).status_code==401
    db=client.app.state.store.connection
    for table in ['users','sessions','events','memories','chat','reminders','preferences','integrations','devices']:
        assert db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0

def test_persistence_survives_app_restart(tmp_path):
    try:
        create_app=importlib.import_module('companion.app').create_app
    except (ImportError,AttributeError):
        pytest.fail('Persistent Companion create_app API has not been implemented')
    path=tmp_path/'persistent.sqlite'
    with TestClient(create_app(db_path=path,start_scheduler=False,clock=lambda:NOW)) as first:
        h=register(first)
        event(first,h)
    with TestClient(create_app(db_path=path,start_scheduler=False,clock=lambda:NOW)) as second:
        assert second.get('/api/events',headers=h).json()['items'][0]['value']==1400

def test_integrations_fail_truthfully_without_credentials(client,monkeypatch):
    monkeypatch.delenv('OPEN_WEARABLES_API_KEY',raising=False)
    a=register(client)
    status=client.get('/api/integrations/status',headers=a)
    assert status.status_code==200,status.text
    assert status.json()['open_wearables']['connected'] is False
    connected=client.post('/api/integrations/open-wearables/connect',headers=a,json={})
    assert connected.status_code==200
    assert connected.json()['configured'] is False

def test_meal_without_quantification_is_not_invented(client):
    a=register(client)
    response=client.post('/api/events',headers=a,json={'kind':'meal','value':None,'unit':'entry','start_at':NOW.isoformat(),'source':'manual','metadata':{'note':'午餐吃了鱼和蔬菜'}})
    assert response.status_code==200,response.text
    assert response.json()['event']['value'] is None
    assert client.post('/api/events',headers=a,json={'kind':'meal','value':1,'unit':'meal','start_at':NOW.isoformat(),'source':'manual','metadata':{}}).status_code==200

def test_derived_summary_deleted_by_user_stays_deleted(client):
    demo=client.post('/api/demo',json={}).json()
    h={'Authorization':'Bearer '+demo['token']}
    memory=next(m for m in client.get('/api/memories',headers=h).json()['items'] if m['origin']=='derived')
    assert client.delete('/api/memories/'+memory['id'],headers=h).status_code==200
    client.post('/api/evaluate',headers=h,json={})
    assert memory['id'] not in {m['id'] for m in client.get('/api/memories',headers=h).json()['items']}

def test_model_and_external_memory_require_separate_opt_in(client):
    a=register(client)
    prefs=client.get('/api/preferences',headers=a).json()
    assert prefs['model_consent'] is False
    updated=client.patch('/api/preferences',headers=a,json={'model_consent':True})
    assert updated.status_code==200
    assert updated.json()['model_consent'] is True

def test_connected_user_is_server_bound_and_upstream_erasure_is_required(tmp_path):
    create_app=importlib.import_module('companion.app').create_app
    class Upstream:
        base_url='http://127.0.0.1:8000'
        def __init__(self):
            self.created=[]
            self.deleted=[]
        async def create_user(self,external_id,name,email):
            self.created.append(external_id)
            return {'id':'upstream-'+external_id}
        async def issue_sdk_tokens(self,wearable_user_id):
            return {'access_token':'scoped-'+wearable_user_id,'refresh_token':'refresh'}
        async def fetch_events(self,wearable_user_id,start,end):
            return []
        async def delete_user(self,wearable_user_id):
            self.deleted.append(wearable_user_id)
            raise RuntimeError('secret upstream diagnostic that must not leak')
    upstream=Upstream()
    with TestClient(create_app(db_path=tmp_path/'upstream.sqlite',clock=lambda:NOW,wearables_client=upstream)) as c:
        h=register(c)
        user=c.get('/api/me',headers=h).json()
        response=c.post('/api/integrations/open-wearables/connect',headers=h,json={'wearable_user_id':'foreign'})
        assert response.status_code==200,response.text
        assert response.json()['wearable_user_id']=='upstream-'+user['id']
        assert upstream.created==[user['id']]
        failed=c.delete('/api/me',headers=h)
        assert failed.status_code==503
        assert 'secret' not in failed.text
        assert c.get('/api/me',headers=h).status_code==200

def test_explicit_remember_instruction_persists_but_ordinary_chat_does_not(client):
    h=register(client)
    first=client.post('/api/chat',headers=h,json={'message':'请记住：我不吃海鲜'})
    assert first.status_code==200
    stored=client.get('/api/memories',headers=h).json()['items']
    assert any(m['text']=='我不吃海鲜' and m['origin']=='user' for m in stored)
    second=client.post('/api/chat',headers=h,json={'message':'你记得我的饮食偏好吗？'})
    assert '不吃海鲜' in second.json()['reply']
    client.post('/api/chat',headers=h,json={'message':'今天我想吃苹果'})
    assert len(client.get('/api/memories',headers=h).json()['items'])==len(stored)

def test_reminders_use_confirmed_goals_and_disabled_categories_cancel_pending(client):
    demo=client.post('/api/demo',json={}).json()
    h={'Authorization':'Bearer '+demo['token']}
    user_goal=next(m for m in client.get('/api/memories',headers=h).json()['items'] if m['origin']=='user')
    client.post('/api/evaluate',headers=h,json={})
    reminder=client.get('/api/reminders',headers=h).json()['items'][0]
    assert user_goal['id'] in reminder['evidence_ids']
    assert '规律作息' in reminder['body'] or '饭后散步' in reminder['body']
    client.patch('/api/preferences',headers=h,json={'enabled':False})
    assert all(r['status']=='cancelled' for r in client.get('/api/reminders',headers=h).json()['items'])

def test_bad_summary_metadata_is_rejected_without_poisoning_dashboard(client):
    h=register(client)
    response=client.post('/api/events',headers=h,json={'kind':'steps','value':3000,'unit':'steps','start_at':NOW.isoformat(),'source':'manual','metadata':{'granularity':'daily_summary','summary_date':'bad'}})
    assert response.status_code==422
    assert client.get('/api/dashboard',headers=h).status_code==200

def test_signed_webhook_syncs_only_bound_account_and_deduplicates(tmp_path,monkeypatch):
    import base64,hashlib,hmac,json,time
    create_app=importlib.import_module('companion.app').create_app
    class Upstream:
        base_url='http://127.0.0.1:8000'
        def __init__(self):self.fetches=0
        async def create_user(self,**kwargs):return {'id':'bound-user'}
        async def issue_sdk_tokens(self,wearable_user_id):return {'access_token':'scoped','refresh_token':'refresh'}
        async def fetch_events(self,wearable_user_id,start,end):
            assert wearable_user_id=='bound-user'
            self.fetches+=1
            return [{'kind':'steps','value':500,'unit':'steps','start_at':NOW.isoformat(),'source':'open-wearables','external_id':'unique','metadata':{'granularity':'daily_summary','summary_date':'2026-11-08','is_complete_day':False}}]
    upstream=Upstream()
    secret_bytes=b'test-webhook-signing-key'
    monkeypatch.setenv('OPEN_WEARABLES_WEBHOOK_SECRET','whsec_'+base64.b64encode(secret_bytes).decode())
    with TestClient(create_app(db_path=tmp_path/'hooks.sqlite',clock=lambda:NOW,wearables_client=upstream)) as c:
        headers=register(c)
        c.post('/api/integrations/open-wearables/connect',headers=headers,json={})
        body=json.dumps({'type':'sync.completed','data':{'user_id':'bound-user'}}).encode()
        stamp=str(int(time.time()))
        signature=base64.b64encode(hmac.new(secret_bytes,b'msg-one.'+stamp.encode()+b'.'+body,hashlib.sha256).digest()).decode()
        webhook_headers={'content-type':'application/json','svix-id':'msg-one','svix-timestamp':stamp,'svix-signature':'v1,'+signature}
        assert c.post('/api/integrations/open-wearables/webhook',content=body,headers=webhook_headers).status_code==200
        assert c.post('/api/integrations/open-wearables/webhook',content=body,headers=webhook_headers).status_code==200
        assert upstream.fetches==1
        assert c.get('/api/events',headers=headers).json()['items'][0]['value']==500
        assert c.get('/api/dashboard',headers=headers).json()['metrics']['steps']==500
        assert c.post('/api/integrations/open-wearables/webhook',content=body+b' ',headers=webhook_headers).status_code==401

def test_pending_reminders_revalidated_when_new_observations_reverse_trend(client):
    demo=client.post('/api/demo',json={}).json()
    h={'Authorization':'Bearer '+demo['token']}
    client.post('/api/evaluate',headers=h,json={})
    sleeps=[e for e in client.get('/api/events',headers=h).json()['items'] if e['kind']=='sleep']
    for old in sleeps[:3]:
        event(client,h,**{k:old[k] for k in ['kind','unit','start_at','end_at','source','external_id','metadata']},value=8)
    client.post('/api/evaluate',headers=h,json={})
    assert all(r['category']!='sleep' or r['status']=='cancelled' for r in client.get('/api/reminders',headers=h).json()['items'])

def test_push_is_generic_on_lock_screen_and_reports_queued(client,monkeypatch):
    import asyncio
    from companion import integrations
    seen=[]
    async def send(tokens,title,body,data=None):
        seen.append((tokens,title,body))
        return {'configured':True,'sent':1,'failed':0,'status':'queued'}
    monkeypatch.setattr(integrations,'send_push',send)
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS','test-only-file.json')
    demo=client.post('/api/demo',json={}).json()
    h={'Authorization':'Bearer '+demo['token']}
    assert client.post('/api/devices',headers=h,json={'token':'test-fcm-token-that-is-long-enough','platform':'android'}).status_code==200
    client.post('/api/evaluate',headers=h,json={})
    assert seen
    assert all('小时' not in body and '步' not in body for _,title,body in seen)
    assert all(r['delivery_status']=='queued' for r in client.get('/api/reminders',headers=h).json()['items'])

def test_rolling_memory_replaces_older_machine_summary(client):
    demo=client.post('/api/demo',json={}).json()
    h={'Authorization':'Bearer '+demo['token']}
    old=[m for m in client.get('/api/memories',headers=h).json()['items'] if m['origin']=='derived']
    event(client,h,kind='sleep',value=1,unit='h',start_at=(NOW-timedelta(minutes=20)).isoformat(),end_at=(NOW-timedelta(minutes=10)).isoformat(),external_id='new-nap')
    client.post('/api/evaluate',headers=h,json={})
    new=client.get('/api/memories',headers=h).json()['items']
    old_sleep=next(m for m in old if '睡眠' in m['text'])
    assert old_sleep['id'] not in {m['id'] for m in new}

def test_expired_feedback_does_not_revive_reminder(tmp_path):
    create_app=importlib.import_module('companion.app').create_app
    clock=[NOW]
    with TestClient(create_app(db_path=tmp_path/'expiry.sqlite',clock=lambda:clock[0])) as c:
        demo=c.post('/api/demo',json={}).json()
        h={'Authorization':'Bearer '+demo['token']}
        c.post('/api/evaluate',headers=h,json={})
        reminder=c.get('/api/reminders',headers=h).json()['items'][0]
        clock[0]=NOW+timedelta(hours=25)
        r=c.post('/api/reminders/'+reminder['id']+'/feedback',headers=h,json={'action':'snooze','minutes':30})
        assert r.status_code==409

def test_concurrent_connect_creates_only_one_upstream_identity(tmp_path):
    import asyncio
    import httpx
    create_app=importlib.import_module('companion.app').create_app
    class Upstream:
        base_url='http://127.0.0.1:8000'
        def __init__(self):self.created=0
        async def create_user(self,**kwargs):
            self.created+=1
            await asyncio.sleep(0.02)
            return {'id':'single-bound-id'}
        async def issue_sdk_tokens(self,wearable_user_id):return {'access_token':'scoped','refresh_token':'refresh'}
    upstream=Upstream()
    app=create_app(db_path=tmp_path/'concurrent.sqlite',clock=lambda:NOW,wearables_client=upstream)
    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
                account=(await c.post('/api/auth/register',json={'name':'林','email':'a@example.com','password':'a-long-password','consent_health_data':True})).json()
                headers={'Authorization':'Bearer '+account['token']}
                responses=await asyncio.gather(*[c.post('/api/integrations/open-wearables/connect',headers=headers,json={}) for _ in range(3)])
                assert all(r.status_code==200 for r in responses)
                assert upstream.created==1
    asyncio.run(exercise())

def test_semantic_memory_is_opt_in_and_revocation_erases_mirror(tmp_path):
    create_app=importlib.import_module('companion.app').create_app
    class Mirror:
        enabled=True
        def __init__(self):self.synced=[];self.deleted=[]
        def sync(self,user_id,memories):self.synced.append((user_id,memories))
        def search(self,user_id,query,limit=5):return [{'id':'foreign-id','text':'another person secret'}]
        def delete_user(self,user_id):self.deleted.append(user_id)
    mirror=Mirror()
    with TestClient(create_app(db_path=tmp_path/'privacy.sqlite',clock=lambda:NOW,memory_adapter=mirror)) as c:
        h=register(c)
        c.post('/api/memories',headers=h,json={'text':'我喜欢早上散步','category':'preference'})
        assert mirror.synced==[]
        user=c.get('/api/me',headers=h).json()
        c.patch('/api/preferences',headers=h,json={'model_consent':True})
        reply=c.post('/api/chat',headers=h,json={'message':'记得我的偏好吗？'}).json()
        assert mirror.synced
        assert 'another person' not in reply['reply']
        assert all(item['id']!='foreign-id' for item in reply['evidence'])
        revoked=c.patch('/api/preferences',headers=h,json={'model_consent':False})
        assert revoked.status_code==200
        assert mirror.deleted==[user['id']]

def test_interrupted_push_claim_recovers_after_lease_expiry(client):
    demo=client.post('/api/demo',json={}).json()
    h={'Authorization':'Bearer '+demo['token']}
    client.post('/api/evaluate',headers=h,json={})
    user=client.get('/api/me',headers=h).json()
    store=client.app.state.store
    reminder=store.list('reminders',user['id'])[0]
    reminder.update(delivery_status='sending',sending_at=(NOW-timedelta(minutes=6)).isoformat())
    store.put('reminders',user['id'],reminder)
    client.post('/api/evaluate',headers=h,json={})
    assert store.get('reminders',user['id'],reminder['id'])['delivery_status']=='inbox'

def test_snoozed_previous_day_reminder_respects_todays_budget(tmp_path,monkeypatch):
    from companion import integrations
    create_app=importlib.import_module('companion.app').create_app
    now=[datetime(2026,11,7,13,tzinfo=timezone.utc)]
    sent=[]
    async def push(tokens,title,body,data=None):
        sent.append(data)
        return {'configured':True,'sent':1,'failed':0,'status':'queued'}
    monkeypatch.setattr(integrations,'send_push',push)
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS','test-only.json')
    with TestClient(create_app(db_path=tmp_path/'budget.sqlite',clock=lambda:now[0])) as c:
        h=register(c)
        c.patch('/api/preferences',headers=h,json={'quiet_start':'00:00','quiet_end':'00:00','daily_limit':1})
        for offset in range(3):
            end=now[0]-timedelta(days=offset,hours=1)
            event(c,h,kind='sleep',value=6,unit='h',start_at=(end-timedelta(hours=6)).isoformat(),end_at=end.isoformat(),external_id='sleep-'+str(offset))
        c.post('/api/evaluate',headers=h,json={})
        reminder=c.get('/api/reminders',headers=h).json()['items'][0]
        c.post('/api/reminders/'+reminder['id']+'/feedback',headers=h,json={'action':'snooze','minutes':660})
        now[0]+=timedelta(hours=12)
        c.post('/api/devices',headers=h,json={'token':'test-fcm-token-that-is-long-enough','platform':'android'})
        c.post('/api/evaluate',headers=h,json={})
        assert len(sent)<=1
        assert len([r for r in c.get('/api/reminders',headers=h).json()['items'] if r['status']=='pending'])<=1

def test_memory_changes_remove_superseded_chat_from_model_context(client,monkeypatch):
    monkeypatch.setenv('COMPANION_MODEL_BASE_URL','https://model.invalid/v1')
    monkeypatch.setenv('COMPANION_MODEL_API_KEY','test-only')
    monkeypatch.setenv('COMPANION_MODEL','test-model')
    contexts=[]
    def model(state):
        contexts.append(state)
        return '已根据当前授权记忆回答'
    monkeypatch.setattr(client.app.state.agent,'_model_reply',model)
    h=register(client)
    old=client.post('/api/memories',headers=h,json={'text':'我的旧偏好是海鲜过敏','category':'preference'}).json()
    client.patch('/api/preferences',headers=h,json={'model_consent':True})
    client.post('/api/chat',headers=h,json={'message':'我之前说过海鲜过敏，记得吗？'})
    client.patch('/api/memories/'+old['id'],headers=h,json={'text':'我喜欢海鲜'})
    client.post('/api/chat',headers=h,json={'message':'现在的偏好是什么？'})
    assert all('过敏' not in m['content'] for m in contexts[-1]['history'])
    assert any(m['text']=='我喜欢海鲜' for m in contexts[-1]['memories'])
    client.delete('/api/memories/'+old['id'],headers=h)
    client.post('/api/chat',headers=h,json={'message':'我的偏好是什么？'})
    assert contexts[-1]['history']==[]
    assert contexts[-1]['memories']==[]
    assert len(client.get('/api/chat',headers=h).json()['items'])==6

def test_recent_and_relevant_memory_survives_more_than_twelve_records(client):
    h=register(client)
    for i in range(13):
        client.post('/api/memories',headers=h,json={'text':'我的偏好编号'+str(i),'category':'preference'})
    latest=client.post('/api/memories',headers=h,json={'text':'我不吃海鲜','category':'preference'}).json()
    response=client.post('/api/chat',headers=h,json={'message':'你记得我的哪些偏好和目标？'}).json()
    assert '不吃海鲜' in response['reply']
    response=client.post('/api/chat',headers=h,json={'message':'我的海鲜偏好是什么？'}).json()
    assert latest['id'] in {e['id'] for e in response['evidence']}

def test_validation_errors_do_not_echo_submitted_password(client):
    response=client.post('/api/auth/register',json={'name':'林','email':'a@example.com','password':'secretabc','consent_health_data':True})
    assert response.status_code==422
    assert 'secretabc' not in response.text

def test_local_meal_answer_reads_dated_note_and_cites_actual_event(client):
    h=register(client)
    meal=event(client,h,kind='meal',value=None,unit='entry',external_id='lunch',metadata={'note':'午餐：清蒸鱼、青菜和米饭','meal_type':'lunch'})['event']
    event(client,h,kind='meal',value=None,unit='entry',external_id='old-lunch',start_at=(NOW-timedelta(days=1)).isoformat(),metadata={'note':'昨天的披萨'})
    result=client.post('/api/chat',headers=h,json={'message':'今天午饭吃了什么？'}).json()
    assert result['mode']=='local'
    assert '清蒸鱼、青菜和米饭' in result['reply']
    assert '2026-11-08' in result['reply']
    assert '披萨' not in result['reply']
    assert meal['id'] in {e['id'] for e in result['evidence']}

def test_water_question_uses_measured_volume_not_meal_count(client):
    h=register(client)
    first=event(client,h,kind='water',value=350,unit='ml',external_id='water-a')['event']
    event(client,h,kind='water',value=0.5,unit='l',external_id='water-b')
    result=client.post('/api/chat',headers=h,json={'message':'今天喝了多少水？'}).json()
    assert '850' in result['reply'] and '毫升' in result['reply']
    assert '饮食记录' not in result['reply']
    assert first['id'] in {e['id'] for e in result['evidence']}

def test_mood_answer_repeats_user_report_without_inference(client):
    h=register(client)
    mood=event(client,h,kind='mood',value=3,unit='score',external_id='mood-a',metadata={'note':'今天工作有点累'})['event']
    result=client.post('/api/chat',headers=h,json={'message':'今天我的心情记录是什么？'}).json()
    assert '3/5' in result['reply']
    assert '工作有点累' in result['reply']
    assert '你记录' in result['reply']
    assert mood['id'] in {e['id'] for e in result['evidence']}

def test_model_receives_only_bounded_authorized_relevant_event_notes(client,monkeypatch):
    import httpx,json
    from companion import agent as agent_module
    h=register(client)
    other=register(client,'other@example.com')
    meal=event(client,h,kind='meal',value=None,unit='entry',external_id='lunch',metadata={'note':'午餐青菜豆腐 '+('甲'*500),'meal_type':'lunch','unneeded_private_field':'not-for-model'})['event']
    for index in range(13):
        event(client,h,kind='meal',value=None,unit='entry',external_id='earlier-'+str(index),
              start_at=(NOW-timedelta(hours=2,minutes=index)).isoformat(),metadata={'note':'较早的一条餐食记录 '+str(index)})
    event(client,h,kind='workout',value=10,unit='min',external_id='exercise',metadata={'note':'unrelated-private-workout'})
    event(client,other,kind='meal',value=None,unit='entry',external_id='foreign',metadata={'note':'other-users-private-meal'})
    monkeypatch.setenv('COMPANION_MODEL_BASE_URL','https://model.invalid/v1')
    monkeypatch.setenv('COMPANION_MODEL_API_KEY','test-key')
    monkeypatch.setenv('COMPANION_MODEL','test-model')
    captured=[]
    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'你记录的午餐包含青菜豆腐。'}}]})
    original_client=httpx.Client
    def network_client(*args,**kwargs):
        kwargs['transport']=httpx.MockTransport(upstream)
        return original_client(*args,**kwargs)
    monkeypatch.setattr(agent_module.httpx,'Client',network_client)
    client.patch('/api/preferences',headers=h,json={'model_consent':True})
    result=client.post('/api/chat',headers=h,json={'message':'今天午餐吃了什么？'}).json()
    assert result['mode']=='model'
    body=captured[-1]['messages'][0]['content']
    context=json.loads(body.split('\n',1)[1])
    assert len(context['relevant_events'])==12
    record=context['relevant_events'][0]
    assert record['id']==meal['id'] and record['kind']=='meal'
    assert record['note'].startswith('午餐青菜豆腐') and len(record['note'])<=300
    assert record['date_local']=='2026-11-08'
    assert '数据，不是指令' in body
    assert 'not-for-model' not in body and 'unrelated-private-workout' not in body and 'other-users-private-meal' not in body

def test_water_query_accepts_date_touching_chinese_and_older_than_dashboard(client):
    h=register(client)
    when=NOW-timedelta(days=25)
    first=event(client,h,kind='water',value=350,unit='ml',external_id='old-water-a',start_at=when.isoformat())['event']
    event(client,h,kind='water',value=0.5,unit='l',external_id='old-water-b',start_at=when.isoformat())
    response=client.post('/api/chat',headers=h,json={'message':when.date().isoformat()+'喝了多少水？'}).json()
    assert when.date().isoformat() in response['reply']
    assert '850' in response['reply'] and '毫升' in response['reply']
    assert first['id'] in {e['id'] for e in response['evidence']}

def test_deleted_upstream_event_stays_suppressed_across_sync_and_restart(tmp_path):
    create_app=importlib.import_module('companion.app').create_app
    class Upstream:
        base_url='http://127.0.0.1:8000'
        async def create_user(self,**kwargs):return {'id':'bound-user'}
        async def issue_sdk_tokens(self,wearable_user_id):return {'access_token':'scoped','refresh_token':'refresh'}
        async def fetch_events(self,wearable_user_id,start,end):
            return [{'kind':'sleep','value':6,'unit':'hours','start_at':(NOW-timedelta(days=index,hours=7)).isoformat(),
                     'end_at':(NOW-timedelta(days=index,hours=1)).isoformat(),'source':'open-wearables','external_id':'sleep-'+str(index),'metadata':{}} for index in range(3)]
    path=tmp_path/'suppression.sqlite'
    with TestClient(create_app(db_path=path,clock=lambda:NOW,wearables_client=Upstream())) as c:
        h=register(c)
        c.post('/api/integrations/open-wearables/connect',headers=h,json={})
        assert c.post('/api/integrations/open-wearables/sync',headers=h,json={}).json()['imported']==3
        c.post('/api/evaluate',headers=h,json={})
        deleted=next(e for e in c.get('/api/events',headers=h).json()['items'] if e['external_id']=='sleep-0')
        assert c.delete('/api/events/'+deleted['id'],headers=h).status_code==200
        retry=c.post('/api/integrations/open-wearables/sync',headers=h,json={})
        assert retry.status_code==200,retry.text
        assert all(e.get('external_id')!='sleep-0' for e in c.get('/api/events',headers=h).json()['items'])
        c.post('/api/evaluate',headers=h,json={})
        assert not any(m['origin']=='derived' for m in c.get('/api/memories',headers=h).json()['items'])
        assert c.post('/api/events',headers=h,json={k:deleted[k] for k in ['kind','value','unit','start_at','end_at','source','external_id','metadata']}).status_code==409
        event(c,h,external_id=None)
        event(c,h,external_id=None)
    with TestClient(create_app(db_path=path,clock=lambda:NOW,wearables_client=Upstream())) as c:
        assert c.post('/api/integrations/open-wearables/sync',headers=h,json={}).status_code==200
        assert all(e.get('external_id')!='sleep-0' for e in c.get('/api/events',headers=h).json()['items'])
        user=c.get('/api/me',headers=h).json()
        # Remove the remote binding so this isolated fixture can exercise local cascade erasure.
        c.app.state.store.singleton('integrations',user['id'],{})
        assert c.delete('/api/me',headers=h).status_code==200
        assert c.app.state.store.connection.execute('SELECT COUNT(*) FROM event_suppressions').fetchone()[0]==0
