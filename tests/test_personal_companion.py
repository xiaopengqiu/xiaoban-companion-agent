from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from companion.app import create_app

NOW=datetime(2026,11,8,6,tzinfo=timezone.utc)

@pytest.fixture
def session(tmp_path):
    clock=[NOW]
    with TestClient(create_app(db_path=tmp_path/'personal.sqlite',clock=lambda:clock[0])) as c:
        yield c,clock

def register(c,email='a@example.com'):
    r=c.post('/api/auth/register',json={'name':'小林','email':email,'password':'a-long-password','consent_health_data':True})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}

def thread(c,h,**values):
    data={'title':'周五面试','details':'我想先准备一下自我介绍'}
    data.update(values)
    r=c.post('/api/threads',headers=h,json=data)
    assert r.status_code==200,r.text
    return r.json()

def evaluate(c,h):
    r=c.post('/api/evaluate',headers=h,json={})
    assert r.status_code==200,r.text
    return r.json()

def test_personal_context_expiry_defaults_and_null_are_explicit(session):
    c,clock=session;h=register(c)
    r=c.post('/api/memories',headers=h,json={'text':'这周在赶项目','category':'context'})
    assert r.status_code==200,r.text
    m=r.json();assert datetime.fromisoformat(m['expires_at'])==NOW+timedelta(days=7)
    assert m['is_active'] is True
    indefinite=c.post('/api/memories',headers=h,json={'text':'最近住在上海','category':'context','expires_at':None}).json()
    assert indefinite['expires_at'] is None
    for category in ['relationship','boundary']:
        assert c.post('/api/memories',headers=h,json={'text':'请先听我说完','category':category}).status_code==200
    clock[0]+=timedelta(days=8)
    rows=c.get('/api/memories',headers=h).json()['items']
    assert next(x for x in rows if x['id']==m['id'])['is_active'] is False
    updated=c.patch('/api/memories/'+m['id'],headers=h,json={'text':'项目已交付','category':'context','expires_at':(clock[0]+timedelta(days=2)).isoformat()})
    assert updated.status_code==200,updated.text
    assert updated.json()['is_active'] is True

def test_memory_patch_preserves_expiry_and_rejects_naive_or_bad_fields(session):
    c,_=session;h=register(c)
    m=c.post('/api/memories',headers=h,json={'text':'这周在备考','category':'context'}).json()
    r=c.patch('/api/memories/'+m['id'],headers=h,json={'text':'下周还在备考'})
    assert r.status_code==200,r.text
    assert r.json()['expires_at']==m['expires_at']
    for data in [{'expires_at':'2026-11-09T12:00:00'},{'category':'invented'},{'user_id':'other'}]:
        assert c.patch('/api/memories/'+m['id'],headers=h,json=data).status_code==422

def test_personal_dates_require_iso_strings_not_unix_numbers(session):
    c,_=session;h=register(c)
    for value in [1800000000,'1800000000',True]:
        assert c.post('/api/memories',headers=h,json={'text':'近期处境','category':'context','expires_at':value}).status_code==422
        assert c.post('/api/threads',headers=h,json={'title':'一件重要的事','event_at':value}).status_code==422
        assert c.post('/api/threads',headers=h,json={'title':'一件重要的事','follow_up_at':value}).status_code==422

def test_thread_crud_is_scoped_and_cascades(session):
    c,_=session;a=register(c);b=register(c,'b@example.com')
    t=thread(c,a)
    assert t['status']=='active' and t['follow_up_enabled'] is False and t['revision']==1
    assert c.get('/api/threads',headers=b).json()['items']==[]
    assert c.patch('/api/threads/'+t['id'],headers=b,json={'outcome':'wrong'}).status_code==404
    assert c.delete('/api/threads/'+t['id'],headers=b).status_code==404
    r=c.patch('/api/threads/'+t['id'],headers=a,json={'status':'completed','outcome':'想再练习自我介绍'})
    assert r.status_code==200,r.text
    assert r.json()['revision']==2 and r.json()['outcome']=='想再练习自我介绍'
    assert c.delete('/api/me',headers=a).status_code==200
    assert c.app.state.store.connection.execute('SELECT count(*) FROM threads').fetchone()[0]==0

def test_thread_followup_requires_explicit_valid_schedule(session):
    c,_=session;h=register(c)
    for fields in [dict(follow_up_enabled=True),dict(follow_up_enabled=True,follow_up_at='2026-11-09T12:00:00'),
                   dict(follow_up_enabled=True,follow_up_at=(NOW+timedelta(days=181)).isoformat()),
                   dict(follow_up_enabled='yes'),dict(status='unknown'),dict(user_id='other')]:
        r=c.post('/api/threads',headers=h,json={'title':'面试',**fields})
        assert r.status_code==422,r.text
    t=thread(c,h)
    assert c.patch('/api/threads/'+t['id'],headers=h,json={'follow_up_enabled':True}).status_code==422

def test_due_thread_needs_no_health_data_and_is_deduplicated(session):
    c,clock=session;h=register(c)
    due=NOW+timedelta(minutes=10)
    t=thread(c,h,follow_up_enabled=True,follow_up_at=due.isoformat())
    assert evaluate(c,h)['created']==0
    clock[0]=due
    assert evaluate(c,h)['created']==1
    assert evaluate(c,h)['created']==0
    r=c.get('/api/reminders',headers=h).json()['items'][0]
    assert r['category']=='life' and r['evidence_ids']==[t['id']]
    assert r['dedupe_key']==f"life:{t['id']}:1"
    assert datetime.fromisoformat(r['expires_at'])==due+timedelta(hours=24)
    assert '成功' not in r['body'] and '结束了' not in r['body']

@pytest.mark.parametrize('changes',[{'status':'completed'},{'status':'paused'},{'follow_up_enabled':False},{'title':'改期后的面试'}])
def test_thread_changes_cancel_pending_reminders_immediately(session,changes):
    c,clock=session;h=register(c)
    t=thread(c,h,follow_up_enabled=True,follow_up_at=(NOW+timedelta(minutes=1)).isoformat())
    clock[0]+=timedelta(minutes=1);evaluate(c,h)
    r=c.patch('/api/threads/'+t['id'],headers=h,json=changes)
    assert r.status_code==200,r.text
    assert c.get('/api/reminders',headers=h).json()['items'][0]['status']=='cancelled'

def test_thread_deletion_cancels_and_overdue_edit_does_not_fail(session):
    c,clock=session;h=register(c)
    t=thread(c,h,follow_up_enabled=True,follow_up_at=(NOW+timedelta(minutes=1)).isoformat())
    clock[0]+=timedelta(minutes=5);evaluate(c,h)
    assert c.patch('/api/threads/'+t['id'],headers=h,json={'details':'今天想晚点聊'}).status_code==200
    assert c.delete('/api/threads/'+t['id'],headers=h).status_code==200
    assert c.get('/api/reminders',headers=h).json()['items'][0]['status']=='cancelled'
    assert evaluate(c,h)['created']==0

def test_life_followups_respect_quiet_global_budget_and_optout(session):
    c,clock=session;h=register(c)
    for n in range(3):thread(c,h,title=f'事情{n}',follow_up_enabled=True,follow_up_at=(NOW+timedelta(minutes=1)).isoformat())
    c.patch('/api/preferences',headers=h,json={'quiet_start':'00:00','quiet_end':'23:59'})
    clock[0]+=timedelta(minutes=1)
    assert evaluate(c,h)['created']==0
    r=c.patch('/api/preferences',headers=h,json={'quiet_start':'22:00','quiet_end':'08:00','daily_limit':1})
    assert r.status_code==200,r.text
    rows=c.get('/api/reminders',headers=h).json()['items']
    assert len([x for x in rows if x['status']=='pending'])==1
    assert evaluate(c,h)['created']==0
    r=c.patch('/api/preferences',headers=h,json={'life_followups_enabled':False})
    assert r.status_code==200,r.text
    assert all(x['status']=='cancelled' for x in c.get('/api/reminders',headers=h).json()['items'])

def test_expired_followup_and_disabled_thread_never_create(session):
    c,clock=session;h=register(c)
    thread(c,h,follow_up_at=(NOW+timedelta(minutes=1)).isoformat())
    thread(c,h,follow_up_enabled=True,follow_up_at=(NOW+timedelta(minutes=1)).isoformat())
    clock[0]+=timedelta(days=2)
    assert evaluate(c,h)['created']==0

def test_support_style_and_suggestions_are_confirmed_not_automatic(session):
    c,_=session;h=register(c)
    assert c.get('/api/preferences',headers=h).json()['support_style']=='reflect'
    assert c.patch('/api/preferences',headers=h,json={'support_style':'listen'}).status_code==200
    assert c.patch('/api/preferences',headers=h,json={'support_style':'diagnose'}).status_code==422
    r=c.post('/api/chat',headers=h,json={'message':'我这周在准备面试，今天感觉有点紧张。'})
    assert r.status_code==200,r.text
    assert '步' not in r.json()['reply']
    assert len(r.json()['suggestions'])==2
    assert c.get('/api/threads',headers=h).json()['items']==[]
    assert c.get('/api/memories',headers=h).json()['items']==[]
    for message in ['我今天走了多少步？','例如我最近要面试该怎么办','请记住：我喜欢散步']:
        assert c.post('/api/chat',headers=h,json={'message':message}).json()['suggestions']==[]

def test_sharing_suggestions_match_shared_consent_fixtures():
    from companion.life import suggestions
    cases=json.loads((Path(__file__).parent/'fixtures/personal-sharing.json').read_text(encoding='utf-8'))
    for case in cases:
        assert bool(suggestions(case['message'],NOW)) is case['suggest'],case['message']

def test_sharing_suggestions_require_a_first_person_subject():
    from companion.life import suggestions
    assert suggestions('他最近准备面试，今天来问我有没有建议。',NOW)==[]
    assert suggestions('同事最近在准备面试，今天来问我该怎么做。',NOW)==[]
    assert len(suggestions('最近我一直在准备面试，很担心自我介绍说不清楚。',NOW))==2

def test_personal_fallback_mentions_only_relevant_context_and_respects_topic_refusal(session):
    c,_=session;h=register(c)
    c.post('/api/memories',headers=h,json={'text':'我这周正在赶一个保密项目','category':'context'})
    thread(c,h,title='周五面试')
    reply=c.post('/api/chat',headers=h,json={'message':'我想聊聊一部电影'}).json()
    assert '保密项目' not in reply['reply'] and '面试' not in reply['reply']
    reply=c.post('/api/chat',headers=h,json={'message':'我想接着聊聊面试的准备'}).json()
    assert '面试' in reply['reply']
    reply=c.post('/api/chat',headers=h,json={'message':'我今天不想提面试了，想聊点别的。'}).json()
    assert '面试' not in reply['reply'] and '保密项目' not in reply['reply']
    assert reply['suggestions']==[]

def test_new_preferences_reject_nulls_and_old_accounts_get_safe_defaults(session):
    c,_=session;h=register(c)
    for data in [{'support_style':None},{'life_followups_enabled':None},{'life_followups_enabled':'yes'}]:
        assert c.patch('/api/preferences',headers=h,json=data).status_code==422
    store=c.app.state.store;user=c.get('/api/me',headers=h).json()
    prefs=store.singleton('preferences',user['id'])
    del prefs['support_style'];del prefs['life_followups_enabled']
    store.singleton('preferences',user['id'],prefs)
    current=c.get('/api/preferences',headers=h).json()
    assert current['support_style']=='reflect' and current['life_followups_enabled'] is True

def test_thread_limit_is_per_account_and_edits_still_work_at_capacity(session):
    c,_=session;h=register(c);other=register(c,'b@example.com')
    first=thread(c,h)
    for n in range(99):thread(c,h,title=f'待关注的事情{n}')
    assert c.post('/api/threads',headers=h,json={'title':'超出容量'}).status_code==409
    assert c.patch('/api/threads/'+first['id'],headers=h,json={'status':'completed'}).status_code==200
    assert c.post('/api/threads',headers=other,json={'title':'我的第一件事'}).status_code==200

def test_model_receives_style_active_threads_but_no_expired_context_or_history(session,monkeypatch):
    c,clock=session;h=register(c)
    for key,value in {'COMPANION_MODEL_BASE_URL':'https://model.invalid/v1','COMPANION_MODEL_API_KEY':'test-only','COMPANION_MODEL':'test'}.items():monkeypatch.setenv(key,value)
    c.patch('/api/preferences',headers=h,json={'support_style':'listen','model_consent':True})
    m=c.post('/api/memories',headers=h,json={'text':'测试过期秘密内容','category':'context','expires_at':(NOW+timedelta(minutes=1)).isoformat()}).json()
    t=thread(c,h,title='周五面试',details='想练习自我介绍')
    seen=[]
    original=httpx.AsyncClient
    def response(request):
        seen.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':'我在听，你可以继续说。'}}]})
    monkeypatch.setattr('companion.model_runtime.http_client',lambda **kw:original(transport=httpx.MockTransport(response),**kw))
    assert c.post('/api/chat',headers=h,json={'message':'我们聊聊近况'}).json()['mode']=='model'
    assert '测试过期秘密内容' in json.dumps(seen[-1],ensure_ascii=False)
    clock[0]+=timedelta(minutes=2)
    assert c.post('/api/chat',headers=h,json={'message':'接着聊吧'}).json()['mode']=='model'
    payload=json.dumps(seen[-1],ensure_ascii=False)
    assert '测试过期秘密内容' not in payload
    assert '我们聊聊近况' not in payload
    assert '周五面试' in payload and 'listen' in payload
    assert c.patch('/api/threads/'+t['id'],headers=h,json={'status':'completed','outcome':'已沟通'}).status_code==200
    c.post('/api/chat',headers=h,json={'message':'现在怎么样'})
    assert '想练习自我介绍' not in json.dumps(seen[-1],ensure_ascii=False)

def test_life_push_uses_generic_notification_and_revoked_thread_is_not_delivered(session,monkeypatch):
    c,clock=session;h=register(c)
    from companion import integrations
    calls=[]
    async def send(tokens,title,body,data=None):calls.append((title,body));return {'configured':True,'sent':1}
    monkeypatch.setattr(integrations,'send_push',send);monkeypatch.setenv('FCM_SERVICE_ACCOUNT_FILE','test-only')
    c.post('/api/devices',headers=h,json={'token':'test-device-token-long-enough','platform':'android'})
    t=thread(c,h,title='私人面试事情',follow_up_enabled=True,follow_up_at=(NOW+timedelta(minutes=1)).isoformat())
    clock[0]+=timedelta(minutes=1);evaluate(c,h)
    assert calls and all('面试' not in body for _,body in calls)
    c.patch('/api/threads/'+t['id'],headers=h,json={'status':'paused'});evaluate(c,h)
    assert len(calls)==1
