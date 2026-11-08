"""Server-only adapters pinned to Open Wearables 0.11.0.

The application must resolve the wearable UUID from its authenticated user's
persisted mapping. This transport layer cannot establish that ownership.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import math
import os
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from svix.webhooks import Webhook


UPSTREAM_VERSION = "0.11.0"
UPSTREAM_COMMIT = "183e8cc4f6de897dbd288c93d49a691f2cf721ae"


class IntegrationError(RuntimeError):
    """Actionable, sanitized integration failure safe to show to our client."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _timestamp(value: str | datetime) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing offset")
        return parsed
    except (TypeError, ValueError, AttributeError) as exc:
        raise IntegrationError("Timestamp must contain a UTC offset") from exc


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise IntegrationError("Invalid numeric value in Open Wearables response")
    return value


def _provenance(row: dict) -> dict:
    source = row.get("source") or {}
    if not isinstance(source, dict) or not source.get("provider"):
        raise IntegrationError("Open Wearables response is missing source provenance")
    return {
        "provider": source["provider"], "writer": source.get("source"),
        "device": source.get("device"), "device_id": source.get("device_id"),
        "zone_offset": row.get("zone_offset"), "upstream_version": UPSTREAM_VERSION,
    }


def normalize_activity(row: dict, *, as_of: str | datetime | None = None) -> list[dict]:
    """Daily summaries already apply upstream provider priority; never sum raw sources."""
    value = _number(row.get("steps"))
    if value is None:
        return []
    try:
        day = date.fromisoformat(row["date"])
    except (KeyError, ValueError, TypeError) as exc:
        raise IntegrationError("Invalid activity summary date") from exc
    boundary = _timestamp(as_of).astimezone(timezone.utc).date() if as_of is not None else None
    if boundary is not None and day > boundary:
        raise IntegrationError("Activity summary date is in the future")
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return [{"kind": "steps", "value": value, "unit": "steps", "start_at": start.isoformat(),
             "end_at": None, "source": "open-wearables",
             "external_id": f"ow:steps:{day.isoformat()}",
             "metadata": {**_provenance(row), "granularity": "daily_summary", "summary_date": day.isoformat(),
                          # The upstream date is a provider-local label but its ActivitySummary
                          # schema omits that offset. This timestamp is only a storage anchor;
                          # consumers must group by summary_date, not convert the anchor.
                          "day_boundary": "upstream_summary_date", "timestamp_is_anchor": True,
                          "is_complete_day": bool(boundary is not None and day < boundary),
                          "coverage_unknown": True, "note": "来源平台日汇总；日期锚点不是精确观测时刻，佩戴覆盖率未知"}}]


def _session_window(row: dict) -> tuple[datetime, datetime, float]:
    start, end = _timestamp(row["start_time"]), _timestamp(row["end_time"])
    if end <= start:
        raise IntegrationError("Session end must be later than start")
    return start, end, (end - start).total_seconds()


def _check_duration(seconds: float, span: float) -> None:
    if seconds > span + 1:
        raise IntegrationError("Reported session duration exceeds its time window")


def normalize_sleep(row: dict) -> list[dict]:
    start, end, span = _session_window(row)
    seconds = _number(row.get("sleep_duration_seconds"))
    measure = "time_asleep"
    if seconds is None:
        seconds = _number(row.get("duration_seconds"))
        measure = "session_duration"
    if seconds is None:
        return []
    _check_duration(seconds, span)
    return [{"kind": "sleep", "value": round(seconds / 3600, 4), "unit": "hours",
             "start_at": start.isoformat(), "end_at": end.isoformat(),
             "source": "open-wearables", "external_id": f"ow:sleep:{row['id']}",
             "metadata": {**_provenance(row), "granularity": "session", "is_nap": bool(row.get("is_nap")),
                          "duration_measure": measure, "upstream_id": row["id"]}}]


def normalize_workout(row: dict) -> list[dict]:
    start, end, span = _session_window(row)
    seconds = _number(row.get("duration_seconds"))
    if seconds is None:
        seconds = span
    _check_duration(seconds, span)
    return [{"kind": "workout", "value": round(seconds / 60, 4), "unit": "minutes", "start_at": start.isoformat(),
             "end_at": end.isoformat(), "source": "open-wearables", "external_id": f"ow:workout:{row['id']}",
             "metadata": {**_provenance(row), "granularity": "session", "workout_type": row.get("type"),
                          "note": row.get("name") or row.get("type") or "运动", "upstream_id": row["id"]}}]


def normalize_meal(row: dict) -> list[dict]:
    stamp = _timestamp(row["timestamp"]).isoformat()
    base = {"start_at": stamp, "source": "open-wearables", "metadata": {
        **_provenance(row), "granularity": "entry", "upstream_id": row["id"],
        "note": row.get("name") or "饮食记录", "macros": row.get("macros") or {},
        "meal_type": row.get("meal_type"),
    }}
    result = []
    calories, water = _number(row.get("calories_kcal")), _number(row.get("water_ml"))
    if calories is not None:
        result.append({**base, "kind": "meal", "value": calories, "unit": "kcal", "external_id": f"ow:meal:{row['id']}"})
    if water is not None:
        result.append({**base, "kind": "water", "value": water, "unit": "ml", "external_id": f"ow:water:{row['id']}"})
    return result


class WearablesClient:
    def __init__(self, base_url: str, api_key: str, transport=None):
        base = base_url.rstrip("/")
        if base.endswith("/api/v1"):
            base = base[:-7]
        parts = urlsplit(base)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.query or parts.fragment or parts.path:
            raise IntegrationError("OPEN_WEARABLES_URL must be an HTTP(S) host without credentials or a path")
        self.base_url, self.api_key, self.transport = base, api_key, transport

    @staticmethod
    def _user_id(user_id: str) -> str:
        try:
            return str(UUID(user_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise IntegrationError("Invalid mapped Open Wearables user UUID") from exc

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.api_key:
            raise IntegrationError("OPEN_WEARABLES_API_KEY is not configured")
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self.transport, timeout=25, follow_redirects=False) as client:
                response = await client.request(method, "/api/v1" + path, headers={"X-Open-Wearables-API-Key": self.api_key}, **kwargs)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise IntegrationError("Unexpected Open Wearables response schema")
            return value
        except httpx.HTTPStatusError as exc:
            raise IntegrationError(f"Open Wearables returned HTTP {exc.response.status_code}", status_code=exc.response.status_code) from exc
        except httpx.RequestError as exc:
            raise IntegrationError("Open Wearables is unreachable or timed out") from exc
        except ValueError as exc:
            raise IntegrationError("Open Wearables returned invalid JSON") from exc

    async def create_user(self, external_id: str, name: str, email: str) -> dict:
        # UserCreate at 0.11.0 has first_name/last_name, no name field. Preserve
        # the supplied display name without guessing a culturally-specific split.
        value = await self._request("POST", "/users", json={"external_user_id": external_id, "first_name": name[:100], "email": email})
        if not value.get("id"):
            raise IntegrationError("Open Wearables did not return the created user's id")
        self._user_id(value["id"])
        return value

    async def issue_sdk_tokens(self, wearable_user_id: str) -> dict:
        user_id = self._user_id(wearable_user_id)
        app_id, app_secret = os.getenv("OPEN_WEARABLES_APP_ID"), os.getenv("OPEN_WEARABLES_APP_SECRET")
        if not app_id or not app_secret:
            raise IntegrationError("Configure server-side OPEN_WEARABLES_APP_ID and OPEN_WEARABLES_APP_SECRET to issue SDK tokens")
        value = await self._request("POST", f"/users/{user_id}/token", json={"app_id": app_id, "app_secret": app_secret})
        if not value.get("access_token") or not value.get("refresh_token"):
            raise IntegrationError("Open Wearables did not return user-scoped SDK tokens")
        return {key: value[key] for key in ("access_token", "refresh_token", "token_type", "expires_in") if key in value}

    async def delete_user(self, wearable_user_id: str) -> dict:
        """Remove the mapped upstream profile when the application deletes it."""
        user_id = self._user_id(wearable_user_id)
        try:
            return await self._request("DELETE", f"/users/{user_id}")
        except IntegrationError as exc:
            # Recovery after a crash between upstream erasure and the local
            # deletion checkpoint must not strand an otherwise erasable account.
            if exc.status_code == 404:
                return {"id": user_id, "already_deleted": True}
            raise

    async def _pages(self, path: str, params: dict) -> list[dict]:
        rows, cursors = [], set()
        for _ in range(1000):
            value = await self._request("GET", path, params=params)
            if not isinstance(value.get("data"), list) or not isinstance(value.get("pagination"), dict):
                raise IntegrationError("Open Wearables pagination schema changed")
            rows.extend(value["data"])
            pagination = value["pagination"]
            if not pagination.get("has_more"):
                return rows
            cursor = pagination.get("next_cursor")
            if not cursor or cursor in cursors:
                raise IntegrationError("Open Wearables returned a missing or repeated cursor")
            cursors.add(cursor)
            params = {**params, "cursor": cursor}
        raise IntegrationError("Open Wearables cursor page limit exceeded")

    async def fetch_events(self, wearable_user_id: str, start: str | datetime, end: str | datetime) -> list[dict]:
        user_id = self._user_id(wearable_user_id)
        start_dt, end_dt = _timestamp(start), _timestamp(end)
        if start_dt >= end_dt:
            raise IntegrationError("Sync start must be earlier than end")
        params = {"start_date": start_dt.isoformat(), "end_date": end_dt.isoformat(), "limit": 100}
        # 0.11.0 daily_activity truncates end to its UTC date and excludes it.
        # Query the complete bucket to include today's partial summary, while
        # retaining the real as_of boundary for completeness and freshness.
        end_utc = end_dt.astimezone(timezone.utc)
        activity_end = datetime.combine(end_utc.date(), time.min, tzinfo=timezone.utc)
        if activity_end < end_utc:
            activity_end += timedelta(days=1)
        result = []
        for suffix, normalizer, extra in [
            ("summaries/activity", normalize_activity, {"end_date": activity_end.isoformat()}),
            ("events/sleep", normalize_sleep, {"filter_by_priority": "true"}),
            ("events/workouts", normalize_workout, {}),
            ("events/meals", normalize_meal, {}),
        ]:
            for row in await self._pages(f"/users/{user_id}/{suffix}", {**params, **extra}):
                try:
                    result.extend(normalizer(row, as_of=end_dt) if normalizer is normalize_activity else normalizer(row))
                except (KeyError, TypeError, AttributeError) as exc:
                    raise IntegrationError("Open Wearables event schema changed") from exc
        return result


def verify_webhook(payload: bytes, headers: Mapping[str, str], secret: str | None = None) -> dict:
    """Verify original bytes and Svix's replay window before reading any user id."""
    secret = secret or os.getenv("OPEN_WEARABLES_WEBHOOK_SECRET", "")
    if not secret:
        raise IntegrationError("OPEN_WEARABLES_WEBHOOK_SECRET is not configured")
    if not isinstance(payload, bytes):
        raise IntegrationError("Webhook verification requires raw request bytes")
    try:
        value = Webhook(secret).verify(payload, dict(headers))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except Exception as exc:
        raise IntegrationError("Invalid or expired Open Wearables webhook signature") from exc


def _load_fcm_credentials(path: str):
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    credentials = service_account.Credentials.from_service_account_file(path, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    credentials.refresh(Request())
    return credentials


async def send_push(device_tokens: list[str], title: str, body: str, data: dict | None = None, *, transport=None) -> dict:
    """FCM acceptance is reported as queued, never as confirmed device delivery."""
    path = os.getenv("FCM_SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    tokens = list(dict.fromkeys(token for token in device_tokens if token))
    result = {"configured": bool(path), "sent": 0, "failed": 0}
    if not tokens:
        return {**result, "status": "no_devices"}
    if not path:
        return {**result, "status": "not_configured"}
    try:
        credentials = await asyncio.to_thread(_load_fcm_credentials, path)
        project_id = os.getenv("FCM_PROJECT_ID") or credentials.project_id
        if not project_id or not credentials.token:
            raise IntegrationError("Invalid FCM service account")
    except Exception:
        return {**result, "failed": len(tokens), "status": "credentials_error"}
    async with httpx.AsyncClient(transport=transport, timeout=20, follow_redirects=False) as client:
        for token in tokens:
            try:
                response = await client.post(
                    f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
                    headers={"Authorization": f"Bearer {credentials.token}"},
                    json={"message": {"token": token, "notification": {"title": title, "body": body},
                                      "data": {str(k): str(v) for k, v in (data or {}).items()}}},
                )
                # 200 without a message name is not a valid FCM acceptance.
                if response.is_success and response.json().get("name"):
                    result["sent"] += 1
                else:
                    result["failed"] += 1
            except (httpx.HTTPError, ValueError):
                result["failed"] += 1
    return {**result, "status": "queued" if result["sent"] and not result["failed"] else "partial" if result["sent"] else "failed"}
