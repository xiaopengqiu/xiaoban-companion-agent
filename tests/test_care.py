import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from companion.app import create_app
from companion.care import parse_decision


def test_decision_contract_requires_exact_thread_selection_and_typed_evidence():
    evidence = {
        'm': {'type': 'memory', 'id': 'm'},
        't': {'type': 'thread', 'id': 't', 'title': '面试', 'status': 'active', 'revision': 3},
        't2': {'type': 'thread', 'id': 't2', 'title': '搬家', 'status': 'active', 'revision': 1},
    }
    false = {'should_contact': False, 'reason': '安静陪伴'}
    assert parse_decision(json.dumps(false), evidence) == false
    selected = {'should_contact': True, 'title': '还好吗', 'message': '愿意聊聊吗？',
                'topic_key': 'interview', 'thread_id': 't', 'evidence_ids': ['t', 'm'], 'reason': '继续关注'}
    assert parse_decision(json.dumps(selected), evidence) == selected
    no_thread = {**selected, 'thread_id': None, 'evidence_ids': ['m']}
    assert parse_decision(json.dumps(no_thread), evidence) == no_thread
    for invalid in [{**false, 'title': '多余'}, {**selected, 'thread_id': None},
                    {**selected, 'thread_id': 't2'}, {**selected, 'evidence_ids': ['t', 't2']}]:
        with pytest.raises(ValueError, match='invalid_decision'):
            parse_decision(json.dumps(invalid), evidence)


@pytest.fixture
def care_case(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTH_PEPPER', 'care-test-pepper-' + 'x' * 40)
    monkeypatch.setenv('COMPANION_MODEL_BASE_URL', 'https://model.example/v1')
    monkeypatch.setenv('COMPANION_MODEL_API_KEY', 'test-key')
    monkeypatch.setenv('COMPANION_MODEL', 'fixture')
    clock = [datetime(2026, 11, 8, 4, tzinfo=timezone.utc)]
    app = create_app(db_path=tmp_path / 'care.sqlite3', clock=lambda: clock[0])
    with TestClient(app) as client:
        auth = client.post('/api/auth/register', json={
            'name': '关心测试', 'email': 'care@example.test', 'password': 'example-password-123',
            'timezone': 'Asia/Shanghai', 'consent_health_data': True}).json()
        headers = {'Authorization': 'Bearer ' + auth['token']}
        uid = auth['user']['id']
        with app.state.store.transaction():
            app.state.store.connection.execute("UPDATE users SET data=json_set(data,'$.is_admin',json('true')) WHERE id=?", (uid,))
        assert client.patch('/api/admin/care-config', headers=headers, json={
            'enabled': True, 'analysis_interval_minutes': 15, 'cooldown_minutes': 30, 'daily_limit': 2}).status_code == 200
        assert client.patch('/api/preferences', headers=headers, json={'model_consent': True}).status_code == 200
        assert client.patch('/api/care/preferences', headers=headers, json={'enabled': True}).status_code == 200
        memory = client.post('/api/memories', headers=headers, json={
            'text': '明天有面试，今晚想早点休息，请温和地关心我。', 'category': 'context'}).json()
        calls = []
        decision = {'should_contact': True, 'title': '面试前的准备',
                    'message': '明天要面试了，现在准备得怎么样？想一起理理重点吗？',
                    'topic_key': 'interview-preparation', 'thread_id': None,
                    'evidence_ids': [memory['id']], 'reason': '用户确认的近期处境'}
        def complete(messages):
            calls.append(messages)
            return json.dumps(decision, ensure_ascii=False)
        monkeypatch.setattr(app.state.model_runtime, 'complete', complete)
        yield client, headers, auth['user'], memory, clock, calls, decision


def check(case):
    client, headers, *_ = case
    response = client.post('/api/admin/care-check', headers=headers, json={})
    assert response.status_code == 200, response.text
    return response.json()


def inbox(case):
    return case[0].get('/api/care', headers=case[1]).json()


def test_no_attention_decision_advances_due_without_messages(care_case):
    case = care_case
    case[-1].clear()
    case[-1].update(should_contact=False, reason='最近没有值得打扰的新情况')
    assert check(case)['reason_code'] == 'no_attention_needed'
    assert inbox(case)['items'] == []
    assert inbox(case)['unread_count'] == 0
    status = inbox(case)['status']
    assert status['last_checked_at'] and status['next_check_at']
    assert len(case[-2]) == 1
    service = case[0].app.state.care_service
    assert service.evaluate(case[2])['reason_code'] == 'not_due'
    assert len(case[-2]) == 1


def test_true_decision_creates_one_owned_thread_and_read_state(care_case):
    created = check(care_case)
    assert created['created'] == 1 and created['reason_code'] == 'created'
    items = inbox(care_case)
    assert items['unread_count'] == 1 and len(items['items']) == 1
    thread_id = created['care_id']
    client, headers, *_ = care_case
    value = client.get('/api/care/' + thread_id, headers=headers).json()
    assert value['thread']['source'] == 'ai'
    assert value['messages'][0]['content'] == care_case[-1]['message']
    assert client.post('/api/care/' + thread_id + '/read', headers=headers, json={}).json()['read']
    assert inbox(care_case)['unread_count'] == 0
    assert check(care_case)['created'] == 0
    assert len(inbox(care_case)['items']) == 1


def test_selected_active_thread_is_snapshotted_and_legacy_source_is_normalized(care_case):
    client, headers, _, memory, _, calls, decision = care_case
    source = client.post('/api/threads', headers=headers, json={'title': '<面试 & 准备>', 'details': '周五'}).json()
    decision.update(thread_id=source['id'], evidence_ids=[source['id'], memory['id']])
    created = check(care_case)['care_id']
    sent = json.loads(calls[0][1]['content'])['evidence'][source['id']]
    assert sent['type'] == 'thread' and sent['status'] == 'active' and sent['revision'] == 1
    detail = client.get('/api/care/' + created, headers=headers).json()
    assert detail['thread']['source_thread'] == {'id': source['id'], 'title': source['title'], 'revision': 1}
    with client.app.state.store.transaction():
        row = client.app.state.store.connection.execute('SELECT data FROM care_threads WHERE id=?', (created,)).fetchone()
        malformed = json.loads(row[0]); malformed['source_thread'] = {'id': source['id'], 'title': 5, 'revision': 1}
        client.app.state.store.connection.execute('UPDATE care_threads SET data=? WHERE id=?', (json.dumps(malformed), created))
    assert client.get('/api/care/' + created, headers=headers).json()['thread']['source_thread'] is None
    assert inbox(care_case)['items'][0]['source_thread'] is None


def test_source_revision_change_during_inference_prevents_care_commit(care_case, monkeypatch):
    client, headers, user, memory, _, _, decision = care_case
    source = client.post('/api/threads', headers=headers, json={'title': '面试', 'details': '第一版'}).json()
    decision.update(thread_id=source['id'], evidence_ids=[source['id'], memory['id']])
    def changed(_):
        client.patch('/api/threads/' + source['id'], headers=headers, json={'details': '第二版'})
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', changed)
    assert client.app.state.care_service.evaluate(user, force=True)['reason_code'] == 'context_changed'
    assert inbox(care_case)['items'] == []


@pytest.mark.parametrize('mutation', ['foreign_evidence', 'bad_boolean', 'unknown_field', 'bad_json'])
def test_invalid_decisions_never_produce_messages(care_case, mutation, monkeypatch):
    decision = care_case[-1]
    if mutation == 'foreign_evidence':
        decision['evidence_ids'] = ['not-owned-by-this-user']
    elif mutation == 'bad_boolean':
        decision['should_contact'] = 'yes'
    elif mutation == 'unknown_field':
        decision['execute_command'] = 'do something'
    else:
        monkeypatch.setattr(care_case[0].app.state.model_runtime, 'complete', lambda _: 'not JSON')
    assert check(care_case)['reason_code'] == 'invalid_decision'
    assert inbox(care_case)['items'] == []


def test_consent_and_quiet_hours_prevent_model_calls(care_case):
    client, headers, _, _, clock, calls, _ = care_case
    client.patch('/api/preferences', headers=headers, json={'model_consent': False})
    assert check(care_case)['reason_code'] == 'model_consent_required'
    assert calls == []
    client.patch('/api/preferences', headers=headers, json={'model_consent': True})
    clock[0] = datetime(2026, 11, 8, 15, tzinfo=timezone.utc)
    assert check(care_case)['reason_code'] == 'quiet_hours'
    assert calls == []


def test_changed_consent_during_inference_discards_decision(care_case, monkeypatch):
    client, _, user, _, _, _, decision = care_case
    store = client.app.state.store
    def complete(_):
        prefs = store.singleton('preferences', user['id'])
        prefs['model_consent'] = False
        store.singleton('preferences', user['id'], prefs)
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', complete)
    assert check(care_case)['created'] == 0
    assert inbox(care_case)['items'] == []


def test_reply_preserves_context_idempotency_and_pending_failure(care_case, monkeypatch):
    client, headers, *_ = care_case
    thread_id = check(care_case)['care_id']
    seen = []
    def answer(messages):
        seen.append(messages)
        return '先从你最想表达的一件事开始，好吗？'
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', answer)
    payload = {'message': '我有点紧张。', 'request_id': 'reply-request-1'}
    url = '/api/care/' + thread_id + '/reply'
    reply = client.post(url, headers=headers, json=payload)
    assert reply.status_code == 200, reply.text
    assert reply.json()['reply_status'] == 'complete'
    assert len(reply.json()['messages']) == 3
    assert any(care_case[-1]['message'] in message['content'] for message in seen[0])
    assert client.post(url, headers=headers, json=payload).json()['messages'] == reply.json()['messages']
    assert len(seen) == 1
    assert client.post(url, headers=headers, json={**payload, 'message': 'changed'}).status_code == 409
    def failure(_):
        raise RuntimeError('provider secret must not escape')
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', failure)
    pending = client.post(url, headers=headers, json={'message': '我先想一想。', 'request_id': 'reply-request-2'})
    assert pending.status_code == 200 and pending.json()['reply_status'] == 'pending'
    assert 'provider secret' not in pending.text
    assert sum(m['content'] == '我先想一想。' for m in pending.json()['messages']) == 1


def test_admin_and_thread_data_are_isolated(care_case):
    client, headers, *_ = care_case
    thread_id = check(care_case)['care_id']
    other = client.post('/api/auth/register', json={
        'name': '另一个用户', 'email': 'other@example.test', 'password': 'example-password-123',
        'timezone': 'Asia/Shanghai', 'consent_health_data': True}).json()
    h = {'Authorization': 'Bearer ' + other['token']}
    assert client.get('/api/admin/care-config', headers=h).status_code == 403
    assert client.patch('/api/admin/care-config', headers=h, json={'enabled': False}).status_code == 403
    assert client.get('/api/admin/push-config', headers=h).status_code == 403
    assert client.get('/api/care/' + thread_id, headers=h).status_code == 404
    assert client.post('/api/care/' + thread_id + '/read', headers=h, json={}).status_code == 404
    assert client.post('/api/care/' + thread_id + '/reply', headers=h, json={
        'message': 'foreign', 'request_id': 'foreign-request'}).status_code == 404
    assert client.get('/api/care', headers=h).json()['items'] == []


def test_native_push_secrets_are_encrypted_write_only_and_blank_preserves(care_case):
    client, headers, *_ = care_case
    secret = 'b' * 24
    response = client.patch('/api/admin/push-config', headers=headers, json={
        'enabled': True, 'provider': 'jpush', 'app_key': 'a' * 24,
        'master_secret': secret, 'client_options': {}})
    assert response.status_code == 200, response.text
    assert secret not in response.text and response.json()['master_secret_configured']
    assert client.patch('/api/admin/push-config', headers=headers, json={'master_secret': ''}).json()['master_secret_configured']
    public = client.get('/api/push/native-config', headers=headers)
    assert public.status_code == 200 and secret not in public.text
    assert public.json()['app_key'] == 'a' * 24
    with client.app.state.store.lock:
        raw = '\n'.join(r[0] for r in client.app.state.store.connection.execute('SELECT value FROM service_state').fetchall())
    assert secret not in raw


def test_daily_limit_and_duplicate_topics_suppress_repeated_care(care_case):
    assert check(care_case)['created'] == 1
    care_case[4][0] += timedelta(minutes=31)
    assert check(care_case)['reason_code'] == 'duplicate'
    care_case[-1]['topic_key'] = 'another-real-followup'
    assert check(care_case)['created'] == 1
    care_case[4][0] += timedelta(minutes=31)
    care_case[-1]['topic_key'] = 'third-topic'
    assert check(care_case)['reason_code'] == 'daily_limit'


def test_missing_model_stays_quiet(care_case, monkeypatch):
    monkeypatch.delenv('COMPANION_MODEL_API_KEY')
    assert check(care_case)['reason_code'] == 'model_unconfigured'
    assert inbox(care_case)['items'] == [] and care_case[-2] == []


def test_deleted_or_expired_facts_do_not_return_through_prior_care(care_case, monkeypatch):
    client, headers, _, memory, clock, calls, decision = care_case
    check(care_case)
    clock[0] += timedelta(minutes=31)
    client.patch('/api/memories/' + memory['id'], headers=headers,
                 json={'expires_at': (clock[0] - timedelta(minutes=1)).isoformat()})
    other = client.post('/api/memories', headers=headers, json={
        'text': '我喜欢在公园散步。', 'category': 'preference'}).json()
    seen = []
    def quiet(messages):
        seen.append(json.dumps(messages, ensure_ascii=False))
        return '{"should_contact":false,"reason":"没有新的关心依据"}'
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', quiet)
    assert check(care_case)['created'] == 0
    assert memory['text'] not in seen[0] and decision['message'] not in seen[0]
    assert other['text'] in seen[0]


def test_account_deleted_during_inference_is_discarded(care_case, monkeypatch):
    client, _, user, _, _, _, decision = care_case
    def erase(_):
        with client.app.state.store.transaction():
            client.app.state.store.connection.execute('DELETE FROM users WHERE id=?', (user['id'],))
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', erase)
    result = client.app.state.care_service.evaluate(user, force=True)
    assert result['created'] == 0
    with client.app.state.store.lock:
        assert client.app.state.store.connection.execute('SELECT count(*) FROM care_threads').fetchone()[0] == 0
        assert client.app.state.store.connection.execute('SELECT count(*) FROM care_state').fetchone()[0] == 0


def test_simultaneous_checks_do_not_hold_normal_user_data_lock(care_case, monkeypatch):
    import concurrent.futures
    import threading
    client, _, user, _, _, _, decision = care_case
    entered, finish = threading.Event(), threading.Event()
    def delayed(_):
        entered.set()
        assert finish.wait(3)
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', delayed)
    service = client.app.state.care_service
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.evaluate, user, force=True)
        assert entered.wait(2)
        assert service.evaluate(user, force=True)['reason_code'] == 'busy'
        # An ordinary request can still read and edit user data while the model waits.
        assert client.get('/api/events', headers=care_case[1]).status_code == 200
        finish.set()
        assert pending.result(timeout=3)['created'] == 1
    assert len(inbox(care_case)['items']) == 1


def test_native_only_queues_for_own_devices_and_never_sends_personal_body(care_case, monkeypatch):
    import httpx
    from companion import native_push
    client, headers, *_ = care_case
    config = client.patch('/api/admin/push-config', headers=headers, json={
        'enabled': True, 'provider': 'jpush', 'app_key': 'a' * 24, 'master_secret': 'b' * 24}).json()
    registration = {'provider': 'jpush', 'registration_id': 'own-device-123456', 'config_revision': config['revision']}
    assert client.post('/api/push/devices', headers=headers, json=registration).status_code == 200
    sent = []
    original = httpx.Client
    def transport(request):
        sent.append(request)
        return httpx.Response(200, json={'msg_id': 123})
    monkeypatch.setattr(native_push, 'http_client', lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    result = check(care_case)
    assert result['created'] == 1 and len(sent) == 1
    body = json.loads(sent[0].content)
    assert body['audience']['registration_id'] == ['own-device-123456']
    assert body['notification']['android']['extras']['care_id'] == result['care_id']
    assert care_case[3]['text'] not in sent[0].content.decode()
    assert care_case[-1]['message'] not in sent[0].content.decode()
    assert client.request('DELETE', '/api/push/devices', headers=headers,
                          json={'registration_id': registration['registration_id']}).status_code == 200
    assert client.post('/api/admin/push-test', headers=headers, json={}).json()['status'] == 'no_devices'
    assert len(sent) == 1


def test_push_clear_and_project_rotation_invalidate_old_registrations(care_case):
    client, headers, *_ = care_case
    c = client.patch('/api/admin/push-config', headers=headers, json={
        'enabled': True, 'provider': 'jpush', 'app_key': 'a' * 24, 'master_secret': 'b' * 24}).json()
    payload = {'provider': 'jpush', 'registration_id': 'own-device-123456', 'config_revision': c['revision']}
    assert client.post('/api/push/devices', headers=headers, json=payload).status_code == 200
    newer = client.patch('/api/admin/push-config', headers=headers, json={'app_key': 'c' * 24}).json()
    assert newer['revision'] > c['revision']
    assert client.post('/api/push/devices', headers=headers, json=payload).status_code == 409
    cleared = client.patch('/api/admin/push-config', headers=headers, json={'clear_master_secret': True}).json()
    assert cleared['enabled'] is False and cleared['master_secret_configured'] is False


def test_malformed_admin_settings_are_rejected(care_case):
    client, headers, *_ = care_case
    for value in [{'analysis_interval_minutes': True}, {'daily_limit': 0}, {'enabled': 'yes'}, {'analysis_interval_minutes': 1}]:
        assert client.patch('/api/admin/care-config', headers=headers, json=value).status_code == 422
    for value in [{'provider': []}, {'enabled': 'yes'}, {'master_secret': None}, {'client_options': {'unknown': {}}}]:
        assert client.patch('/api/admin/push-config', headers=headers, json=value).status_code == 422
