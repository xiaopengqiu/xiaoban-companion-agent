import importlib.util
import pytest
from fastapi.testclient import TestClient

def gateway(monkeypatch):
    assert importlib.util.find_spec('companion.cli_gateway'), 'The local CLI gateway is not implemented'
    from companion.cli_gateway import create_gateway
    monkeypatch.setenv('COMPANION_CLI_GATEWAY_TOKEN','test-only-server-gateway-token-32chars')
    return TestClient(create_gateway())

def test_gateway_requires_server_token_and_never_accepts_commands(monkeypatch):
    with gateway(monkeypatch) as c:
        payload={'model':'codex','messages':[{'role':'user','content':'你好'}]}
        assert c.post('/v1/chat/completions',json=payload).status_code==401
        h={'Authorization':'Bearer test-only-server-gateway-token-32chars'}
        for bad in [{**payload,'model':'powershell'},{**payload,'command':'whoami'},{**payload,'messages':[{'role':'tool','content':'x'}]}]:
            assert c.post('/v1/chat/completions',headers=h,json=bad).status_code==422

def test_gateway_only_returns_bounded_text_and_sanitized_errors(monkeypatch):
    with gateway(monkeypatch) as c:
        from companion import cli_gateway
        seen=[]
        monkeypatch.setattr(cli_gateway,'run_cli',lambda provider,messages,model:(seen.append((provider,messages,model)) or '你好，小伴在这里'))
        h={'Authorization':'Bearer test-only-server-gateway-token-32chars'}
        payload={'model':'claude','messages':[{'role':'user','content':'合成测试'}]}
        r=c.post('/v1/chat/completions',headers=h,json=payload)
        assert r.status_code==200 and r.json()['choices'][0]['message']['content']=='你好，小伴在这里'
        assert seen[0][0]=='claude'
        def broken(*args):raise RuntimeError('secret-local-account-token')
        monkeypatch.setattr(cli_gateway,'run_cli',broken)
        r=c.post('/v1/chat/completions',headers=h,json=payload)
        assert r.status_code==503 and 'secret-local-account-token' not in r.text
        assert c.post('/v1/chat/completions',headers=h,content='x'*80001).status_code==413
