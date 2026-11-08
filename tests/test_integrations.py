"""Network-boundary contracts pinned to Open Wearables 0.11.0 (synthetic data)."""
import asyncio
import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest

from companion import integrations


USER_ID = "f0671c92-dfaa-4992-9635-fd80be651bfe"
SOURCE = {"provider": "apple", "source": "Apple Watch", "device": "Watch7,1"}


def run(awaitable):
    return asyncio.run(awaitable)


def page(rows, cursor=None):
    return {"data": rows, "pagination": {"next_cursor": cursor, "has_more": bool(cursor)}, "metadata": {}}


def test_create_user_uses_real_upstream_header_and_payload():
    def handler(request):
        assert request.url.path == "/api/v1/users"
        assert request.headers["X-Open-Wearables-API-Key"] == "server-key"
        assert "authorization" not in request.headers
        assert json.loads(request.content) == {"external_user_id": "local-user", "first_name": "小林", "email": "lin@example.com"}
        return httpx.Response(201, json={"id": USER_ID, "first_name": "小林"})
    client = integrations.WearablesClient("https://wearables.example", "server-key", transport=httpx.MockTransport(handler))
    assert run(client.create_user("local-user", "小林", "lin@example.com"))["id"] == USER_ID


def test_sdk_token_requires_application_credentials_and_keeps_them_server_side(monkeypatch):
    monkeypatch.setenv("OPEN_WEARABLES_APP_ID", "app-test")
    monkeypatch.setenv("OPEN_WEARABLES_APP_SECRET", "secret-test")
    def handler(request):
        assert request.url.path == f"/api/v1/users/{USER_ID}/token"
        assert json.loads(request.content) == {"app_id": "app-test", "app_secret": "secret-test"}
        return httpx.Response(200, json={"access_token": "user-scoped-token", "refresh_token": "rotate-me", "token_type": "bearer", "expires_in": 3600})
    client = integrations.WearablesClient("https://wearables.example/api/v1/", "server-key", transport=httpx.MockTransport(handler))
    tokens = run(client.issue_sdk_tokens(USER_ID))
    assert tokens["access_token"] == "user-scoped-token"
    assert "app_secret" not in tokens


def test_missing_sdk_credentials_do_not_fall_back_to_admin_api_key(monkeypatch):
    monkeypatch.delenv("OPEN_WEARABLES_APP_ID", raising=False)
    monkeypatch.delenv("OPEN_WEARABLES_APP_SECRET", raising=False)
    client = integrations.WearablesClient("https://wearables.example", "server-key", transport=httpx.MockTransport(lambda _: pytest.fail("must not call upstream")))
    with pytest.raises(integrations.IntegrationError, match="APP_ID"):
        run(client.issue_sdk_tokens(USER_ID))


def test_fetch_normalizes_pagination_nulls_duration_and_provenance():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["X-Open-Wearables-API-Key"] == "server-key"
        assert request.url.params["start_date"] == "2026-11-01T00:00:00+00:00"
        assert request.url.params["end_date"] == "2026-11-08T00:00:00+00:00"
        path = request.url.path
        if path.endswith("/summaries/activity"):
            if request.url.params.get("cursor") == "second":
                return httpx.Response(200, json=page([{"date": "2026-11-02", "source": SOURCE, "steps": None}]))
            return httpx.Response(200, json=page([{"date": "2026-11-01", "source": SOURCE, "steps": 4200}], "second"))
        if path.endswith("/events/sleep"):
            assert request.url.params["filter_by_priority"] == "true"
            return httpx.Response(200, json=page([{"id": "sleep-1", "source": SOURCE, "start_time": "2026-11-01T15:00:00Z", "end_time": "2026-11-01T23:00:00Z", "duration_seconds": 28800, "sleep_duration_seconds": 25200, "is_nap": False}]))
        if path.endswith("/events/workouts"):
            return httpx.Response(200, json=page([{"id": "workout-1", "source": SOURCE, "type": "running", "start_time": "2026-11-02T00:00:00Z", "end_time": "2026-11-02T00:30:00Z", "duration_seconds": 1800}]))
        if path.endswith("/events/meals"):
            return httpx.Response(200, json=page([{"id": "meal-1", "source": SOURCE, "timestamp": "2026-11-02T04:00:00Z", "name": "午餐", "calories_kcal": 520, "water_ml": 250, "macros": {"protein_g": 20}}]))
        pytest.fail(f"unexpected upstream endpoint: {path}")
    client = integrations.WearablesClient("https://wearables.example", "server-key", transport=httpx.MockTransport(handler))
    rows = run(client.fetch_events(USER_ID, "2026-11-01T00:00:00Z", "2026-11-08T00:00:00Z"))
    assert len(requests) == 5
    assert {r["kind"] for r in rows} == {"steps", "sleep", "workout", "meal", "water"}
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["steps"]["value"] == 4200
    assert by_kind["sleep"]["value"] == 7
    assert by_kind["sleep"]["unit"] == "hours"
    assert by_kind["workout"]["value"] == 30
    assert by_kind["meal"]["metadata"]["macros"]["protein_g"] == 20
    assert by_kind["water"]["value"] == 250
    assert all(r["metadata"]["provider"] == "apple" for r in rows)
    assert len({r["external_id"] for r in rows}) == len(rows)


def test_pagination_cycle_is_rejected_instead_of_infinite_sync():
    def handler(request):
        return httpx.Response(200, json=page([], "same"))
    client = integrations.WearablesClient("https://wearables.example", "server-key", transport=httpx.MockTransport(handler))
    with pytest.raises(integrations.IntegrationError, match="cursor"):
        run(client.fetch_events(USER_ID, "2026-11-01T00:00:00Z", "2026-11-08T00:00:00Z"))


def test_invalid_foreign_id_and_naive_window_fail_before_network():
    client = integrations.WearablesClient("https://wearables.example", "server-key", transport=httpx.MockTransport(lambda _: pytest.fail("must not call upstream")))
    with pytest.raises(integrations.IntegrationError):
        run(client.fetch_events("../other-user", "2026-11-01", "2026-11-08"))
    with pytest.raises(integrations.IntegrationError, match="offset"):
        run(client.fetch_events(USER_ID, "2026-11-01", "2026-11-08"))


def test_upstream_failure_redacts_provider_body():
    client = integrations.WearablesClient("https://wearables.example", "server-key", transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"detail": "private-token-data"})))
    with pytest.raises(integrations.IntegrationError) as err:
        run(client.create_user("local", "Lin", "lin@example.com"))
    assert "401" in str(err.value)
    assert "private-token-data" not in str(err.value)


def test_webhook_requires_secret_and_validates_raw_bytes_and_replay_window():
    secret_bytes = b"test-webhook-signing-key"
    secret = "whsec_" + base64.b64encode(secret_bytes).decode()
    payload = b'{"type":"sleep.created","data":{"user_id":"test-user"}}'
    timestamp = str(int(time.time()))
    message_id = "msg_test1"
    signature = base64.b64encode(hmac.new(secret_bytes, message_id.encode() + b"." + timestamp.encode() + b"." + payload, hashlib.sha256).digest()).decode()
    headers = {"svix-id": message_id, "svix-timestamp": timestamp, "svix-signature": "v1," + signature}
    assert integrations.verify_webhook(payload, headers, secret)["type"] == "sleep.created"
    with pytest.raises(integrations.IntegrationError):
        integrations.verify_webhook(payload, headers, "")
    with pytest.raises(integrations.IntegrationError):
        integrations.verify_webhook(payload + b" ", headers, secret)
    with pytest.raises(integrations.IntegrationError):
        integrations.verify_webhook(payload, {**headers, "svix-timestamp": "1"}, secret)


def test_push_without_credentials_never_claims_delivery(monkeypatch):
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    result = run(integrations.send_push(["device-token"], "提醒", "出去走走"))
    assert result["configured"] is False
    assert result["sent"] == 0
    assert result["status"] == "not_configured"


def test_push_empty_recipients_never_reports_sent(monkeypatch):
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_FILE", "missing.json")
    result = run(integrations.send_push([], "提醒", "出去走走"))
    assert result["sent"] == 0
    assert result["status"] == "no_devices"


def test_fcm_boundary_queues_only_acknowledged_messages_without_leaking_tokens(monkeypatch):
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_FILE", "test-only.json")
    class Credentials:
        project_id = "test-project"
        token = "oauth-access-token"
    monkeypatch.setattr(integrations, "_load_fcm_credentials", lambda _: Credentials())
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url == "https://fcm.googleapis.com/v1/projects/test-project/messages:send"
        assert request.headers["authorization"] == "Bearer oauth-access-token"
        msg = json.loads(request.content)["message"]
        assert msg["data"] == {"reminder_id": "r1"}
        if msg["token"] == "good-token":
            return httpx.Response(200, json={"name": "projects/test-project/messages/123"})
        return httpx.Response(404, json={"error": {"message": "private-bad-token"}})
    result = run(integrations.send_push(["good-token", "good-token", "bad-token"], "提醒", "打开 App 查看", {"reminder_id": "r1"}, transport=httpx.MockTransport(handler)))
    assert len(requests) == 2
    assert result == {"configured": True, "sent": 1, "failed": 1, "status": "partial"}
    assert "token" not in json.dumps(result)


def test_deleted_upstream_user_can_be_removed_with_real_endpoint():
    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path == f"/api/v1/users/{USER_ID}"
        return httpx.Response(200, json={"id": USER_ID})
    client = integrations.WearablesClient("https://wearables.example", "server-key", transport=httpx.MockTransport(handler))
    assert run(client.delete_user(USER_ID))["id"] == USER_ID


def test_erasure_retry_treats_upstream_missing_user_as_already_deleted():
    client = integrations.WearablesClient("https://wearables.example", "server-key", transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"detail": "User not found"})))
    result = run(client.delete_user(USER_ID))
    assert result == {"id": USER_ID, "already_deleted": True}


@pytest.mark.parametrize("normalizer,extra", [
    (integrations.normalize_sleep, {"duration_seconds": 3600, "sleep_duration_seconds": 3600}),
    (integrations.normalize_workout, {"duration_seconds": 3600}),
])
def test_session_rejects_reversed_or_equal_instants_even_with_valid_reported_duration(normalizer, extra):
    row = {"id": "bad", "source": SOURCE, "start_time": "2026-11-01T08:00:00+08:00", "end_time": "2026-11-01T00:00:00Z", **extra}
    with pytest.raises(integrations.IntegrationError, match="end"):
        normalizer(row)
    row["end_time"] = "2026-10-31T23:59:00Z"
    with pytest.raises(integrations.IntegrationError, match="end"):
        normalizer(row)


@pytest.mark.parametrize("normalizer,extra", [
    (integrations.normalize_sleep, {"duration_seconds": 7200, "sleep_duration_seconds": 7200}),
    (integrations.normalize_workout, {"duration_seconds": 7200}),
])
def test_reported_duration_cannot_exceed_observed_session_window(normalizer, extra):
    row = {"id": "bad", "source": SOURCE, "start_time": "2026-11-01T00:00:00Z", "end_time": "2026-11-01T01:00:00Z", **extra}
    with pytest.raises(integrations.IntegrationError, match="duration"):
        normalizer(row)


def test_workout_duration_fallback_uses_instants_across_offset_change():
    row = {"id": "dst", "source": SOURCE, "start_time": "2026-11-01T01:30:00-04:00", "end_time": "2026-11-01T01:30:00-05:00", "duration_seconds": None}
    assert integrations.normalize_workout(row)[0]["value"] == 60
    row["end_time"] = "2026-11-01T02:30:00"
    with pytest.raises(integrations.IntegrationError, match="offset"):
        integrations.normalize_workout(row)


def test_daily_summary_keeps_its_date_label_without_fabricated_next_day_end():
    result = integrations.normalize_activity({"date": "2026-11-01", "source": SOURCE, "steps": 4200}, as_of="2026-11-02T00:00:00Z")[0]
    assert result.get("end_at") is None
    assert result["metadata"]["summary_date"] == "2026-11-01"
    assert result["metadata"]["day_boundary"] == "upstream_summary_date"
    assert result["metadata"]["is_complete_day"] is True
    assert integrations.normalize_activity({"date": "2026-11-02", "source": SOURCE, "steps": 0}, as_of="2026-11-02T12:00:00Z")[0]["metadata"]["is_complete_day"] is False


def test_activity_query_includes_in_progress_day_without_marking_it_complete():
    def handler(request):
        if request.url.path.endswith('/summaries/activity'):
            assert request.url.params['end_date'] == '2026-11-09T00:00:00+00:00'
            return httpx.Response(200, json=page([{'date':'2026-11-08','steps':1234,'source':SOURCE}]))
        assert request.url.params['end_date'] == '2026-11-08T06:00:00+00:00'
        return httpx.Response(200, json=page([]))
    client=integrations.WearablesClient('https://wearables.example','server-key',transport=httpx.MockTransport(handler))
    rows=run(client.fetch_events(USER_ID,'2026-11-01T00:00:00Z','2026-11-08T06:00:00Z'))
    assert rows[0]['value']==1234
    assert rows[0]['metadata']['is_complete_day'] is False
