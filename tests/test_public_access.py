"""Public access boundaries; no external services or credentials are used."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from companion.app import create_app


NOW = datetime(2026, 11, 8, 6, tzinfo=timezone.utc)


def public_settings(monkeypatch):
    monkeypatch.setenv('COMPANION_PUBLIC_MODE', 'true')
    monkeypatch.setenv('COMPANION_ALLOWED_HOSTS', 'companion.example')
    monkeypatch.setenv('COMPANION_AUTH_RATE_WINDOW_SECONDS', '60')
    monkeypatch.setenv('COMPANION_LOGIN_RATE_LIMIT', '2')
    monkeypatch.setenv('COMPANION_REGISTER_RATE_LIMIT', '2')
    monkeypatch.setenv('COMPANION_DEMO_RATE_LIMIT', '1')


def test_public_mode_requires_an_explicit_host_allowlist(monkeypatch):
    monkeypatch.setenv('COMPANION_PUBLIC_MODE', 'true')
    monkeypatch.delenv('COMPANION_ALLOWED_HOSTS', raising=False)
    with pytest.raises(ValueError, match='ALLOWED_HOSTS'):
        create_app(db_path=':memory:')


def test_public_health_and_static_resources_have_restrictive_headers(tmp_path, monkeypatch):
    public_settings(monkeypatch)
    monkeypatch.setenv('OPEN_WEARABLES_API_KEY', 'private-admin-key')
    resources=tmp_path/'packaged-web'
    resources.mkdir()
    (resources/'index.html').write_text('<!doctype html><h1>Packaged companion</h1>',encoding='utf-8')
    with TestClient(create_app(db_path=':memory:',web_dir=resources),base_url='https://companion.example') as client:
        assert client.get('/api/health').json()=={'status':'ok'}
        response=client.get('/')
        assert 'Packaged companion' in response.text
        assert response.headers['X-Frame-Options']=='DENY'
        assert response.headers['X-Content-Type-Options']=='nosniff'
        assert "script-src 'self'" in response.headers['Content-Security-Policy']
        assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
        assert 'unsafe-inline' not in response.headers['Content-Security-Policy']
        assert 'Strict-Transport-Security' in response.headers
        assert client.get('/api/me').headers['Cache-Control']=='no-store'
        assert client.get('/api/health',headers={'Host':'attacker.example'}).status_code==400


def test_login_rate_limit_does_not_trust_spoofed_forwarding_headers(monkeypatch):
    public_settings(monkeypatch)
    clock=[NOW]
    with TestClient(create_app(db_path=':memory:',clock=lambda:clock[0]),base_url='https://companion.example') as client:
        for address in ['203.0.113.1','203.0.113.2']:
            assert client.post('/api/auth/login',json={'email':'none@example.com','password':'wrong-password'},headers={'X-Forwarded-For':address}).status_code==401
        limited=client.post('/api/auth/login',json={'email':'none@example.com','password':'wrong-password'},headers={'X-Forwarded-For':'203.0.113.3'})
        assert limited.status_code==429
        assert int(limited.headers['Retry-After'])>0
        clock[0]+=timedelta(seconds=61)
        assert client.post('/api/auth/login',json={'email':'none@example.com','password':'wrong-password'}).status_code==401


def test_explicit_trusted_proxy_uses_rightmost_untrusted_address(monkeypatch):
    public_settings(monkeypatch)
    monkeypatch.setenv('COMPANION_TRUSTED_PROXY_IPS','10.0.0.1/32')
    with TestClient(create_app(db_path=':memory:',clock=lambda:NOW),base_url='https://companion.example',client=('10.0.0.1',12345)) as client:
        body={'email':'none@example.com','password':'wrong-password'}
        assert client.post('/api/auth/login',json=body,headers={'X-Forwarded-For':'198.51.100.99, 203.0.113.1'}).status_code==401
        assert client.post('/api/auth/login',json=body,headers={'X-Forwarded-For':'198.51.100.98, 203.0.113.1'}).status_code==401
        assert client.post('/api/auth/login',json=body,headers={'X-Forwarded-For':'198.51.100.97, 203.0.113.1'}).status_code==429
        assert client.post('/api/auth/login',json=body,headers={'X-Forwarded-For':'203.0.113.2'}).status_code==401


def test_public_demo_requires_opt_in_and_is_bounded_when_enabled(monkeypatch):
    public_settings(monkeypatch)
    with TestClient(create_app(db_path=':memory:'),base_url='https://companion.example') as client:
        assert client.post('/api/demo',json={}).status_code==403
    monkeypatch.setenv('COMPANION_PUBLIC_DEMO_ENABLED','true')
    with TestClient(create_app(db_path=':memory:',clock=lambda:NOW),base_url='https://companion.example') as client:
        assert client.post('/api/demo',json={}).status_code==200
        assert client.post('/api/demo',json={}).status_code==429


def test_public_demo_account_cap_is_atomic_across_concurrent_requests(monkeypatch):
    public_settings(monkeypatch)
    monkeypatch.setenv('COMPANION_PUBLIC_DEMO_ENABLED','true')
    monkeypatch.setenv('COMPANION_PUBLIC_DEMO_MAX_ACCOUNTS','1')
    monkeypatch.setenv('COMPANION_DEMO_RATE_LIMIT','10')
    with TestClient(create_app(db_path=':memory:',clock=lambda:NOW),base_url='https://companion.example') as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda _: client.post('/api/demo',json={}).status_code,range(2)))
        assert sorted(statuses)==[200,429]
        assert len(client.app.state.store.users())==1


def test_register_is_limited_before_expensive_password_hashing(monkeypatch):
    public_settings(monkeypatch)
    with TestClient(create_app(db_path=':memory:'),base_url='https://companion.example') as client:
        for i in range(2):
            body={'name':'小林','email':f'public{i}@example.com','password':'a-long-password','timezone':'Asia/Shanghai','consent_health_data':True}
            assert client.post('/api/auth/register',json=body).status_code==200
        assert client.post('/api/auth/register',json=body).status_code==429


def test_bad_content_length_and_chunked_oversize_never_reach_authentication():
    with TestClient(create_app(db_path=':memory:')) as client:
        assert client.post('/api/auth/login',content=b'{}',headers={'Content-Length':'invalid'}).status_code==400
        response=client.post('/api/auth/login',content=iter([b'{"email":"',b'x'*65_000,b'"}']),headers={'Content-Type':'application/json'})
        assert response.status_code==413


def test_logout_revokes_only_the_current_server_session():
    with TestClient(create_app(db_path=':memory:',clock=lambda:NOW)) as client:
        body={'name':'小林','email':'sessions@example.com','password':'a-long-password','timezone':'Asia/Shanghai','consent_health_data':True}
        first=client.post('/api/auth/register',json=body).json()['token']
        second=client.post('/api/auth/login',json={'email':body['email'],'password':body['password']}).json()['token']
        first_headers={'Authorization':'Bearer '+first}
        second_headers={'Authorization':'Bearer '+second}
        assert client.post('/api/auth/logout',headers=first_headers).status_code==200
        assert client.get('/api/me',headers=first_headers).status_code==401
        assert client.get('/api/me',headers=second_headers).status_code==200


def test_validation_and_login_errors_never_echo_submitted_password():
    with TestClient(create_app(db_path=':memory:')) as client:
        secret='sensitive-invalid-password'
        response=client.post('/api/auth/register',json={'name':'user','email':'bad','password':secret,'consent_health_data':True})
        assert response.status_code==422
        assert secret not in response.text
        response=client.post('/api/auth/login',json={'email':'none@example.com','password':secret})
        assert response.status_code==401
        assert secret not in response.text


def test_browser_request_timeout_does_not_leave_auth_waiting_forever():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
    const source=fs.readFileSync('web/app.js','utf8').replace(/^import .*;$/gm,'');
    new vm.Script(source);
    let prefix=source.split('const post=')[0];
const context={document:{querySelector:()=>({})},localStorage:{getItem:()=>null},updateCareSurface(){},CareInbox:class{reset(){}},SessionGuard:class{},ReminderTracker:class{},DueCheckGate:class{},AbortController,setTimeout,clearTimeout,fetch:(_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')))),DOMException};
vm.createContext(context);
vm.runInContext(prefix+';globalThis.request=api;',context);
(async()=>{await assert.rejects(context.request('/auth/login',{timeoutMs:5}),/超时/);})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result=subprocess.run([node,'-e',script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',timeout=5)
    assert result.returncode==0,result.stdout+result.stderr


def test_cloud_registration_discloses_online_storage_and_keeps_consent_text_valid():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const elements=new Map();
const context={document:{querySelector(selector){if(selector==='meta[name="companion-runtime"]')return {content:'cloud'};if(!elements.has(selector))elements.set(selector,{});return elements.get(selector);}},localStorage:{getItem:()=>null},updateCareSurface(){},CareInbox:class{reset(){}},SessionGuard:class{},ReminderTracker:class{},DueCheckGate:class{}};
const source=fs.readFileSync('web/app.js','utf8').replace(/^import .*;$/gm,'').split('setInterval(checkNewMessages')[0];
vm.createContext(context);vm.runInContext(source+';renderAuth("register");',context);
assert.match(elements.get('#app').innerHTML,/云端账号 · 个人记录保存在在线服务/);
assert.match(elements.get('#app').innerHTML,/我同意小伴在在线服务中保存/);
assert.doesNotMatch(elements.get('#app').innerHTML,/\$\{cloudRuntime/);
'''
    result=subprocess.run([node,'-e',script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',timeout=5)
    assert result.returncode==0,result.stdout+result.stderr


def test_other_tab_account_change_invalidates_old_view_without_erasing_new_token():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
let stored='alice';const listeners={},elements=new Map();
const context={document:{querySelector(selector){if(!elements.has(selector))elements.set(selector,selector==='#modal'?{close(){}}:{});return elements.get(selector);}},window:{addEventListener:(name,handler)=>listeners[name]=handler},localStorage:{getItem:()=>stored,removeItem:()=>stored=null},updateCareSurface(){},CareInbox:class{reset(){}},SessionGuard:class{replace(){}},ReminderTracker:class{reset(){}},DueCheckGate:class{reset(){}},AbortController,setTimeout,clearTimeout};
const source=fs.readFileSync('web/app.js','utf8').replace(/^import .*;$/gm,'');
const listener=source.match(/^window.addEventListener\('storage'.*$/m)[0];
vm.createContext(context);vm.runInContext(source.split('setInterval(checkNewMessages')[0]+listener,context);
stored='bob';listeners.storage({key:'companion_token',newValue:'bob'});
assert.equal(stored,'bob');assert.equal(vm.runInContext('state.token',context),null);
'''
    result=subprocess.run([node,'-e',script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',timeout=5)
    assert result.returncode==0,result.stdout+result.stderr
