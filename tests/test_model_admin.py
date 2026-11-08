import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from companion.app import create_app

SETUP='test-only-setup-token-32-characters-long'

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('COMPANION_ADMIN_SETUP_TOKEN',SETUP)
    monkeypatch.setenv('COMPANION_CONFIG_ENCRYPTION_KEY','test-only-encryption-key-at-least-32chars')
    with TestClient(create_app(db_path=tmp_path/'admin.sqlite')) as c:yield c

def account(c,email):
    r=c.post('/api/auth/register',json={'name':'合成账号','email':email,'password':'test-long-password','consent_health_data':True})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}

def grant(c,email):
    return c.post('/api/internal/admin/grant',headers={'Authorization':'Bearer '+SETUP},json={'email':email})

def test_admin_configuration_is_server_enforced_and_bootstrap_is_single_use(client):
    c=client;admin=account(c,'admin@example.com');other=account(c,'other@example.com')
    assert c.get('/api/admin/model-config',headers=admin).status_code==403
    assert c.post('/api/internal/admin/grant',headers=admin,json={'email':'admin@example.com'}).status_code==401
    assert grant(c,'admin@example.com').status_code==200
    assert c.get('/api/me',headers=admin).json()['is_admin'] is True
    assert grant(c,'other@example.com').status_code==409
    assert c.get('/api/admin/model-config',headers=other).status_code==403
    assert c.put('/api/admin/model-config',headers=other,json={'mode':'disabled'}).status_code==403
    assert c.post('/api/admin/model-test',headers=other,json={}).status_code==403

def test_config_is_encrypted_redacted_and_retains_omitted_secrets(client):
    c=client;h=account(c,'admin@example.com');grant(c,'admin@example.com')
    config={'mode':'api','api_url':'https://model.example/v1','api_key':'secret-api-key-must-not-leak','model':'test-model'}
    r=c.put('/api/admin/model-config',headers=h,json=config)
    assert r.status_code==200,r.text
    assert 'secret-api-key' not in r.text and 'api_key' not in r.json()
    raw=c.app.state.store.connection.execute("SELECT value FROM service_state WHERE state_key='model_settings'").fetchone()[0]
    assert 'secret-api-key' not in raw
    config.pop('api_key');config['model']='changed-model'
    assert c.put('/api/admin/model-config',headers=h,json=config).json()['api_key_configured'] is True
    for bad in [{'mode':'api','api_url':'http://evil.example/v1'}, {'mode':'local','local_provider':'powershell'}, {'mode':'disabled','command':'rm'}]:
        assert c.put('/api/admin/model-config',headers=h,json=bad).status_code==422

def test_http_runtime_uses_selected_admin_config_and_refuses_redirects(client,monkeypatch):
    c=client;h=account(c,'admin@example.com');grant(c,'admin@example.com')
    c.put('/api/admin/model-config',headers=h,json={'mode':'api','api_url':'https://model.example/v1','api_key':'server-secret','model':'configured-model'})
    from companion import model_runtime
    seen=[]
    transport=httpx.MockTransport(lambda request:(seen.append(request) or httpx.Response(200,json={'choices':[{'message':{'content':'配置已连通'}}]})))
    original=httpx.AsyncClient
    monkeypatch.setattr(model_runtime,'http_client',lambda **kw:original(transport=transport,**kw))
    assert c.post('/api/admin/model-test',headers=h,json={}).json()['ok'] is True
    assert json.loads(seen[0].content)['model']=='configured-model'
    assert seen[0].headers['authorization']=='Bearer server-secret'
    monkeypatch.setattr(model_runtime,'http_client',lambda **kw:original(transport=httpx.MockTransport(lambda r:httpx.Response(307,headers={'location':'https://evil.example'})),**kw))
    r=c.post('/api/admin/model-test',headers=h,json={})
    assert r.json()['ok'] is False and 'server-secret' not in r.text

def test_cli_commands_have_no_shell_tools_or_shared_conversation():
    from companion.model_runtime import cli_command,parse_cli_output,ModelRuntimeError
    cmd=cli_command('codex','codex.exe')
    assert '--ignore-user-config' in cmd and '--ephemeral' in cmd and '--sandbox' in cmd
    assert 'features.shell_tool=false' in cmd and 'features.apps=false' in cmd
    claude=cli_command('claude','claude.exe')
    assert claude[claude.index('--tools')+1]=='' and '--no-session-persistence' in claude and '--safe-mode' in claude
    assert parse_cli_output('codex',json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'你好'}}))=='你好'
    assert parse_cli_output('claude',json.dumps({'type':'result','result':'你好','is_error':False}))=='你好'
    with pytest.raises(ModelRuntimeError):parse_cli_output('claude',json.dumps({'type':'result','result':'secret-token error','is_error':True}))
    with pytest.raises(ModelRuntimeError):cli_command('shell','powershell.exe')

def test_invalid_config_types_are_sanitized_and_legacy_http_does_not_send_a_key(client,monkeypatch):
    from companion.model_runtime import ModelRuntimeError,validate_config
    for bad in [{'mode':[]},{'local_provider':{}},{'api_key':None}]:
        with pytest.raises(ModelRuntimeError):validate_config(bad)
    monkeypatch.setenv('COMPANION_MODEL_BASE_URL','http://model.example/v1')
    monkeypatch.setenv('COMPANION_MODEL_API_KEY','do-not-transmit-over-http')
    monkeypatch.setenv('COMPANION_MODEL','example')
    from companion import model_runtime
    monkeypatch.setattr(model_runtime,'http_client',lambda **kw:pytest.fail('Insecure transport must not be opened'))
    with pytest.raises(ModelRuntimeError):client.app.state.model_runtime.complete([{'role':'user','content':'test'}])


def test_admin_user_directory_is_scoped_filtered_and_redacted(client):
    c=client;admin=account(c,'admin@example.com');other=account(c,'other@example.com')
    assert c.get('/api/admin/users',headers=other).status_code==403
    grant(c,'admin@example.com')
    rows=c.get('/api/admin/users',headers=admin)
    assert rows.status_code==200,rows.text
    assert len(rows.json()['items'])==2
    assert all(set(row)=={'id','name','email','is_admin','is_demo','created_at'} for row in rows.json()['items'])
    assert c.get('/api/admin/users?email=OTHER@EXAMPLE.COM',headers=admin).json()['items'][0]['email']=='other@example.com'
    assert c.get('/api/admin/users?email=missing@example.com',headers=admin).json()['items']==[]
    assert c.get('/api/admin/users?email=',headers=admin).status_code==422


def test_only_admin_can_assign_roles_with_strict_input_and_no_demo_promotion(client):
    c=client;a=account(c,'admin@example.com');b=account(c,'other@example.com');grant(c,'admin@example.com')
    target=c.get('/api/me',headers=b).json()['id']
    assert c.post('/api/admin/users/'+target+'/role',headers=b,json={'is_admin':True}).status_code==403
    for body in [{'is_admin':1},{'is_admin':'true'},{'is_admin':None},{'is_admin':True,'role':'owner'},{}]:
        assert c.post('/api/admin/users/'+target+'/role',headers=a,json=body).status_code==422
    result=c.post('/api/admin/users/'+target+'/role',headers=a,json={'is_admin':True})
    assert result.status_code==200,result.text
    assert result.json()['is_admin'] is True
    assert c.get('/api/me',headers=b).json()['is_admin'] is True
    assert c.get('/api/admin/model-config',headers=b).status_code==200
    demo=c.post('/api/demo',json={}).json()['user']
    assert c.post('/api/admin/users/'+demo['id']+'/role',headers=a,json={'is_admin':True}).status_code==409
    assert c.post('/api/admin/users/missing/role',headers=a,json={'is_admin':True}).status_code==404


def test_last_admin_cannot_be_demoted_or_deleted_and_other_admin_can_remove_self(client):
    c=client;a=account(c,'admin@example.com');b=account(c,'other@example.com');grant(c,'admin@example.com')
    aid=c.get('/api/me',headers=a).json()['id'];bid=c.get('/api/me',headers=b).json()['id']
    assert c.post('/api/admin/users/'+aid+'/role',headers=a,json={'is_admin':False}).status_code==409
    # Reject before attempting any external cleanup of the final administrator.
    c.app.state.store.singleton('integrations',aid,{'wearable_user_id':'not-contacted'})
    assert c.delete('/api/me',headers=a).status_code==409
    c.app.state.store.singleton('integrations',aid,{})
    assert c.post('/api/admin/users/'+bid+'/role',headers=a,json={'is_admin':True}).status_code==200
    assert c.delete('/api/me',headers=a).status_code==200
    assert c.get('/api/me',headers=a).status_code==401
    assert c.delete('/api/me',headers=b).status_code==409
    assert c.get('/api/admin/users',headers=b).status_code==200


def test_concurrent_admin_demotion_cannot_remove_every_admin(client):
    from concurrent.futures import ThreadPoolExecutor
    c=client;a=account(c,'admin@example.com');b=account(c,'other@example.com');grant(c,'admin@example.com')
    aid=c.get('/api/me',headers=a).json()['id'];bid=c.get('/api/me',headers=b).json()['id']
    assert c.post('/api/admin/users/'+bid+'/role',headers=a,json={'is_admin':True}).status_code==200
    def demote(values):
        h,user_id=values
        return c.post('/api/admin/users/'+user_id+'/role',headers=h,json={'is_admin':False}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(demote,[(a,aid),(b,bid)]))
    assert sorted(results)==[200,409]
    assert sum(user.get('is_admin') is True for user in c.app.state.store.users())==1


def test_admin_json_errors_and_non_ascii_setup_headers_are_safe(client):
    c=client;a=account(c,'admin@example.com')
    r=c.post('/api/internal/admin/grant',headers={b'authorization':'Bearer 测试错误令牌'.encode()},json={'email':'admin@example.com'})
    assert r.status_code==401
    assert c.post('/api/internal/admin/grant',headers={'Authorization':'Bearer '+SETUP,'Content-Type':'application/json'},content='{').status_code==422
    assert grant(c,'admin@example.com').status_code==200
    assert c.put('/api/admin/model-config',headers={**a,'Content-Type':'application/json'},content='{').status_code==422
    target=c.get('/api/me',headers=a).json()['id']
    assert c.post('/api/admin/users/'+target+'/role',headers={**a,'Content-Type':'application/json'},content='{').status_code==422


def test_last_admin_deletion_does_not_start_upstream_or_memory_erasure(tmp_path,monkeypatch):
    monkeypatch.setenv('COMPANION_ADMIN_SETUP_TOKEN',SETUP)
    class Upstream:
        def __init__(self):self.calls=0
        async def delete_user(self,user_id):self.calls+=1
    class Mirror:
        enabled=True
        def __init__(self):self.calls=0
        def delete_user(self,user_id):self.calls+=1
    upstream=Upstream();mirror=Mirror()
    with TestClient(create_app(db_path=tmp_path/'guard.sqlite',wearables_client=upstream,memory_adapter=mirror)) as c:
        h=account(c,'admin@example.com');grant(c,'admin@example.com')
        user=c.get('/api/me',headers=h).json();store=c.app.state.store
        store.singleton('integrations',user['id'],{'wearable_user_id':'test-bound-user'})
        prefs=store.singleton('preferences',user['id']);prefs['_memory_index_used']=True
        store.singleton('preferences',user['id'],prefs)
        assert c.delete('/api/me',headers=h).status_code==409
        assert upstream.calls==0 and mirror.calls==0
        assert c.get('/api/me',headers=h).status_code==200


def test_final_store_delete_guard_survives_a_role_change_after_early_check(client,monkeypatch):
    c=client;a=account(c,'admin@example.com');b=account(c,'other@example.com');grant(c,'admin@example.com')
    aid=c.get('/api/me',headers=a).json()['id'];bid=c.get('/api/me',headers=b).json()['id']
    c.post('/api/admin/users/'+bid+'/role',headers=a,json={'is_admin':True})
    store=c.app.state.store
    original=store.is_last_admin
    def raced_check(user_id):
        result=original(user_id)
        if user_id==aid:
            # Simulate the other administrator being downgraded between the
            # optimistic pre-cleanup check and the final SQL deletion.
            with store.transaction():
                row=store.connection.execute('SELECT data FROM users WHERE id=?',(bid,)).fetchone()
                user=json.loads(row[0]);user['is_admin']=False
                store.connection.execute('UPDATE users SET data=? WHERE id=?',(json.dumps(user),bid))
        return result
    monkeypatch.setattr(store,'is_last_admin',raced_check)
    assert c.delete('/api/me',headers=a).status_code==409
    assert c.get('/api/me',headers=a).json()['is_admin'] is True


def test_demotion_revokes_administrative_access_of_existing_session(client):
    c=client;a=account(c,'admin@example.com');b=account(c,'other@example.com');grant(c,'admin@example.com')
    bid=c.get('/api/me',headers=b).json()['id']
    assert c.post('/api/admin/users/'+bid+'/role',headers=a,json={'is_admin':True}).status_code==200
    assert c.get('/api/admin/users',headers=b).status_code==200
    assert c.post('/api/admin/users/'+bid+'/role',headers=a,json={'is_admin':False}).status_code==200
    assert c.get('/api/admin/users',headers=b).status_code==403
    assert c.put('/api/admin/model-config',headers=b,json={'mode':'disabled'}).status_code==403
    assert c.post('/api/admin/model-test',headers=b,json={}).status_code==403
