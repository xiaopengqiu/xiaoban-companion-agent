"""Independent API regressions discovered during the cross-runtime review."""
import json
from datetime import timedelta

import httpx
import pytest

from test_care import care_case, check


def test_pending_care_turn_must_finish_before_another_request_id_is_accepted(care_case, monkeypatch):
    client, headers, *_ = care_case
    care_id = check(care_case)['care_id']
    def unavailable(_):
        raise RuntimeError('fixture model unavailable')
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', unavailable)
    path = '/api/care/' + care_id + '/reply'
    first = client.post(path, headers=headers, json={'message': '第一条回复', 'request_id': 'review-request-first'})
    assert first.status_code == 200 and first.json()['reply_status'] == 'pending'
    second = client.post(path, headers=headers, json={'message': '第二条回复', 'request_id': 'review-request-second'})
    assert second.status_code == 409
    detail = client.get('/api/care/' + care_id, headers=headers).json()
    assert sum(m['role'] == 'user' for m in detail['messages']) == 1


def test_shorter_admin_analysis_interval_applies_to_existing_due_state(care_case):
    client, headers, _, _, clock, calls, decision = care_case
    decision.clear()
    decision.update(should_contact=False, reason='目前没有需要打扰的事')
    client.patch('/api/admin/care-config', headers=headers, json={'analysis_interval_minutes': 120})
    assert check(care_case)['reason_code'] == 'no_attention_needed'
    client.patch('/api/admin/care-config', headers=headers, json={'analysis_interval_minutes': 15})
    clock[0] += timedelta(minutes=16)
    result = client.app.state.care_service.evaluate(care_case[2])
    assert result['reason_code'] == 'no_attention_needed'
    assert len(calls) == 2


def test_vendor_success_status_without_acceptance_id_is_not_reported_queued(care_case, monkeypatch):
    from companion import native_push
    client, headers, *_ = care_case
    configured = client.patch('/api/admin/push-config', headers=headers, json={
        'enabled': True, 'provider': 'jpush', 'app_key': 'a' * 24, 'master_secret': 'b' * 24}).json()
    registered = client.post('/api/push/devices', headers=headers, json={
        'provider': 'jpush', 'registration_id': 'review-owned-rid', 'config_revision': configured['revision']})
    assert registered.status_code == 200
    original = httpx.Client
    monkeypatch.setattr(native_push, 'http_client', lambda **kw: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'error': {'code': 1002}})), **kw))
    result = client.post('/api/admin/push-test', headers=headers, json={})
    assert result.status_code == 200
    assert result.json()['status'] == 'failed'
    assert result.json()['sent'] == 0


def test_analysis_lease_expiry_discards_old_model_result(care_case, monkeypatch):
    client, _, _, _, clock, _, decision = care_case
    def delayed(_):
        clock[0] += timedelta(seconds=181)
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', delayed)
    assert check(care_case)['created'] == 0
    assert client.get('/api/care', headers=care_case[1]).json()['items'] == []


def test_care_optin_disable_then_reenable_invalidates_inflight_decision(care_case, monkeypatch):
    client, _, user, _, _, _, decision = care_case
    service = client.app.state.care_service
    def changed(_):
        service.save_preferences(user['id'], {'enabled': False})
        service.save_preferences(user['id'], {'enabled': True})
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', changed)
    assert check(care_case)['created'] == 0
    assert client.get('/api/care', headers=care_case[1]).json()['items'] == []


def test_evidence_deleted_during_analysis_cannot_create_care(care_case, monkeypatch):
    client, _, user, memory, _, _, decision = care_case
    def deleted(_):
        client.app.state.store.delete('memories', user['id'], memory['id'])
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', deleted)
    assert check(care_case)['created'] == 0
    assert client.get('/api/care', headers=care_case[1]).json()['items'] == []


@pytest.mark.parametrize('limited_by', ['cooldown', 'daily_limit'])
def test_due_analysis_still_runs_when_sending_budget_is_unavailable(care_case, limited_by):
    client, headers, _, _, clock, calls, decision = care_case
    if limited_by == 'daily_limit':
        client.patch('/api/admin/care-config', headers=headers, json={'daily_limit': 1})
    assert check(care_case)['created'] == 1
    clock[0] += timedelta(minutes=16 if limited_by == 'cooldown' else 31)
    decision.clear()
    decision.update(should_contact=False, reason='暂时无需继续打扰')
    assert client.app.state.care_service.evaluate(care_case[2])['reason_code'] == 'no_attention_needed'
    assert len(calls) == 2


def test_push_test_reports_accepted_counts_and_stale_revision_is_conflict(care_case, monkeypatch):
    from companion import native_push
    client, headers, *_ = care_case
    configured = client.patch('/api/admin/push-config', headers=headers, json={
        'enabled': True, 'provider': 'jpush', 'app_key': 'a' * 24, 'master_secret': 'b' * 24}).json()
    payload = {'provider': 'jpush', 'registration_id': 'review-owned-rid', 'config_revision': configured['revision']}
    assert client.post('/api/push/devices', headers=headers, json=payload).status_code == 200
    original = httpx.Client
    monkeypatch.setattr(native_push, 'http_client', lambda **kw: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'msg_id': 123})), **kw))
    result = client.post('/api/admin/push-test', headers=headers, json={}).json()
    assert result == {'status': 'queued', 'configured': True, 'queued': 1, 'sent': 1, 'failed': 0}
    client.patch('/api/admin/push-config', headers=headers, json={'app_key': 'c' * 24})
    assert client.post('/api/push/devices', headers=headers, json=payload).status_code == 409


def test_scheduler_processes_a_bounded_page_and_resumes_after_restart(care_case, monkeypatch):
    from companion.care import CareService
    client, _, user, _, clock, *_ = care_case
    service = client.app.state.care_service
    store = client.app.state.store
    for index in range(7):
        store.create_user('page user', f'page{index}@example.test', 'example-password-123', 'Asia/Shanghai', clock[0])
    seen = []
    monkeypatch.setattr(service, 'evaluate', lambda current: seen.append(current['id']))
    service.scheduler_tick()
    assert len(seen) == 6
    resumed = CareService(store, service.runtime, service.clock)
    monkeypatch.setattr(resumed, 'evaluate', lambda current: seen.append(current['id']))
    resumed.scheduler_tick()
    assert len(seen) == 8 and len(set(seen)) == 8 and user['id'] in seen


def test_model_consent_revoke_then_regrant_invalidates_inflight_analysis(care_case, monkeypatch):
    client, _, user, _, _, _, decision = care_case
    store = client.app.state.store
    def changed(_):
        prefs = store.singleton('preferences', user['id'])
        store.singleton('preferences', user['id'], {**prefs, 'model_consent': False})
        store.singleton('preferences', user['id'], prefs)
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', changed)
    assert check(care_case)['created'] == 0


def test_model_consent_revision_also_invalidates_an_inflight_reply(care_case, monkeypatch):
    client, headers, user, *_ = care_case
    care_id = check(care_case)['care_id']
    store = client.app.state.store
    def changed(_):
        prefs = store.singleton('preferences', user['id'])
        store.singleton('preferences', user['id'], {**prefs, 'model_consent': False})
        store.singleton('preferences', user['id'], prefs)
        return '这条旧授权下生成的回应不应提交'
    monkeypatch.setattr(client.app.state.model_runtime, 'complete', changed)
    value = client.post('/api/care/' + care_id + '/reply', headers=headers, json={
        'message': '我想聊一聊', 'request_id': 'review-consent-reply'}).json()
    assert value['reply_status'] == 'pending'
    assert len(value['messages']) == 2


def test_expired_analysis_cannot_overwrite_new_lease_owners_state(care_case, monkeypatch):
    client, _, user, _, clock, _, decision = care_case
    service = client.app.state.care_service
    def delayed(_):
        clock[0] += timedelta(seconds=181)
        monkeypatch.setattr(service.runtime, 'complete', lambda _: json.dumps(decision, ensure_ascii=False))
        assert service.evaluate(user, force=True)['created'] == 1
        return json.dumps(decision, ensure_ascii=False)
    monkeypatch.setattr(service.runtime, 'complete', delayed)
    assert check(care_case)['created'] == 0
    assert service._singleton('care_state', user['id'])['reason_code'] == 'created'


def test_enabling_care_without_explicit_model_consent_is_conflict(care_case):
    client, headers, *_ = care_case
    client.patch('/api/preferences', headers=headers, json={'model_consent': False})
    assert client.patch('/api/care/preferences', headers=headers, json={'enabled': True}).status_code == 409


def test_push_credential_rotation_invalidates_old_registration_revision(care_case):
    client, headers, *_ = care_case
    first = client.patch('/api/admin/push-config', headers=headers, json={
        'enabled': True, 'provider': 'jpush', 'app_key': 'a' * 24, 'master_secret': 'b' * 24}).json()
    payload = {'provider': 'jpush', 'registration_id': 'review-owned-rid', 'config_revision': first['revision']}
    assert client.post('/api/push/devices', headers=headers, json=payload).status_code == 200
    second = client.patch('/api/admin/push-config', headers=headers, json={'master_secret': 'c' * 24}).json()
    assert second['revision'] > first['revision']
    assert client.post('/api/push/devices', headers=headers, json=payload).status_code == 409
