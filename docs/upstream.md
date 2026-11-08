# Open Wearables integration and provenance

This project integrates the existing Open Wearables data service. It adds a user-scoped companion application, ingestion normalization, evidence-backed memory and notification policy above that service. It does not rebrand a simulated local dataset as a working hardware connection.

## Exact upstream baseline

- Source: https://github.com/the-momentum/open-wearables
- Release: **0.11.0**, published 2026-11-02.
- Inspected commit: **183e8cc4f6de897dbd288c93d49a691f2cf721ae**.
- [Original MIT LICENSE](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/LICENSE), copyright 2025 Momentum. The bootstrap keeps the entire upstream checkout and LICENSE in ignored `vendor/open-wearables`; no upstream sources are silently copied into our code.
- Deployment adapts the MIT [official deployment guide at this commit](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/docs/deployment/docker.mdx), pinning the official API/worker/beat and portal images to `0.11.0` rather than `latest`.
- The integration contract below was checked against pinned Python route and Pydantic schema source, not inferred from README prose. These tests use synthetic examples shaped to that schema; no real user's records or real credentials are included.

## Verified server contract

Base `/api/v1`. Administrative API calls use **`X-Open-Wearables-API-Key`**, not `Authorization: Bearer <api-key>`.

| Operation | Verified upstream request | Response used here |
| --- | --- | --- |
| Create profile | `POST /users`, `{external_user_id, first_name, email}` | `{id,...}`. Persist the returned UUID in our authenticated user's mapping. `external_user_id` is deprecated and is not accepted by data-fetching routes. |
| Issue phone tokens | `POST /users/{uuid}/token`, body `{app_id,app_secret}` | `{access_token,refresh_token,token_type,expires_in}` |
| Read daily steps | `GET /users/{uuid}/summaries/activity?start_date=...&end_date=...&limit=100` | `{data:[{date,steps,source}],pagination:{has_more,next_cursor},metadata}` |
| Read sleep | `GET /users/{uuid}/events/sleep?...&filter_by_priority=true` | `data` rows with `id,start_time,end_time,sleep_duration_seconds,duration_seconds,is_nap,source` |
| Read workouts | `GET /users/{uuid}/events/workouts?...` | `data` rows with `id,type,start_time,end_time,duration_seconds,source` |
| Read meals | `GET /users/{uuid}/events/meals?...` | `data` rows with `id,timestamp,calories_kcal,water_ml,macros,source` |
| Delete upstream profile | `DELETE /users/{uuid}` | Deleted user object. Caller must coordinate with local account deletion. |

All list routes use offset-aware `start_date`/`end_date` and cursor pagination. The pinned activity repository truncates its end to a UTC date and excludes that date; the adapter rounds a non-midnight activity-query end up to the next UTC midnight to include the in-progress day. Other event queries retain the exact end. Normalization retains the real sync boundary and never marks today's partial summary as complete. Steps `null` are skipped; they are never turned into a zero. Daily step summaries already use upstream source priority. Sleep sessions request source-priority filtering to avoid mirrored copies. Workout IDs deduplicate repeated syncs; different provider IDs for the same mirrored workout are **not yet merged by this adapter**. Prefer one workout data source per user in the first version.

Normalization emits steps/steps, sleep/hours, workout/minutes, meal/kcal and water/ml. Source provenance, device, provider, UTC offset and granularity remain in event metadata. Daily steps use the upstream summary date as their stable external ID. These are daily buckets, not a current live activity signal. Upstream aggregates use a provider-local date label but their activity response omits its offset. We retain that `metadata.summary_date` for grouping; `start_at` is explicitly marked as a storage anchor and there is no fabricated next-day `end_at`. `is_complete_day` means the summary's date window has closed relative to the sync boundary, not that the device was worn continuously (`coverage_unknown=true`). Consumers must not rezone the anchor to infer a different day. Sleep uses time asleep when available, otherwise the reported session duration and explicitly marks that fallback. Reversed/equal session instants, offset-free times and reported durations longer than their window are rejected. Food records only exist when upstream data actually supplies them; a watch does not automatically identify meals.

Pinned sources: [users route](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/api/routes/v1/users.py), [user schema](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/schemas/model_crud/user_management/user.py), [SDK token route](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/api/routes/v1/sdk_token.py), [API key header](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/services/api_key_service.py), [summaries](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/api/routes/v1/summaries.py), [events](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/api/routes/v1/events.py), [event schemas](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/schemas/responses/activity/events.py), [pagination](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/schemas/utils/pagination.py).

## Start the real data service

From this project's root in PowerShell:

```powershell
./scripts/bootstrap-open-wearables.ps1
docker compose --env-file deploy/.env -f deploy/compose.open-wearables.yml up -d
docker compose --env-file deploy/.env -f deploy/compose.open-wearables.yml ps
```

Bootstrap clones the pinned upstream release into ignored `vendor/`, checks its commit, and generates ignored `deploy/.env`. It never resets or deletes an existing checkout and never overwrites existing configuration. It does not start services. The generated admin password is in `deploy/.env`; it is not printed. The API is at `http://localhost:8000`, developer portal at `http://localhost:3000`; ports are bound to loopback for this local stack.

Sign in to the developer portal with `ADMIN_EMAIL` and `ADMIN_PASSWORD` from `deploy/.env`. Create an API key and an application in its credentials/settings interface. The application's secret is shown once. Put these **only in the companion backend's ignored `.env`**:

```dotenv
OPEN_WEARABLES_URL=http://localhost:8000
OPEN_WEARABLES_API_KEY=<management-api-key>
OPEN_WEARABLES_APP_ID=<application-id>
OPEN_WEARABLES_APP_SECRET=<application-secret>
```

The server-side token mint route accepts application credentials or a developer Bearer JWT; an ordinary API key alone is insufficient. The phone receives only user-scoped SDK tokens after our backend resolves its persisted UUID mapping. Never put the management API key or app secret in Flutter, web JavaScript, a user prompt, or model context. Source: [application creation](https://github.com/the-momentum/open-wearables/blob/183e8cc4f6de897dbd288c93d49a691f2cf721ae/backend/app/api/routes/v1/applications.py).

If the phone is physical, `localhost` points at the phone. Configure a reachable development HTTPS host or a local network proxy before testing on hardware. This stack's loopback bindings must be deliberately adjusted for that setup. HealthKit/Health Connect require the phone app's explicit data permissions; SDK availability does not guarantee every brand exports every metric. Mobile background synchronization is opportunistic, so live-state reminders must check freshness and cannot promise second-level detection.

## Optional signed webhooks and FCM

Polling through the authenticated sync endpoint works without a webhook service. For webhooks, enable `OUTGOING_WEBHOOKS_ENABLED=true` in `deploy/.env`, start Compose with `--profile webhooks`, register our reachable HTTPS receiver in the Open Wearables webhook UI, and put the **endpoint signing secret** in the companion backend's `OPEN_WEARABLES_WEBHOOK_SECRET`. That secret is different from the Svix server JWT secret. `verify_webhook` verifies the **original request bytes**, all Svix headers and the timestamp window; missing configuration and tampered/expired payloads fail closed. Core must persist message IDs to deduplicate valid retries, map the signed user ID, and re-fetch authoritative records; do not ingest arbitrary webhook JSON as user events.

FCM is optional. Set `FCM_SERVICE_ACCOUNT_FILE` (or `GOOGLE_APPLICATION_CREDENTIALS`) to a server-only service-account JSON file and optionally `FCM_PROJECT_ID`. Enable Firebase Cloud Messaging API for that project and configure the real mobile app for the same project. Keep the JSON outside Git. `send_push` exchanges service-account credentials for an OAuth token and calls FCM HTTP v1 only for server-selected device tokens. It reports `not_configured`, `credentials_error`, `no_devices`, `failed`, `partial` or `queued`. FCM acceptance is not proof that a phone displayed the message. Keep notification content generic (for example, open the App to view a reminder) to avoid exposing health details on lock screens.

## Verification limits

`tests/test_integrations.py` exercises real HTTP requests against `httpx.MockTransport`, including exact upstream paths/auth, pagination, null/missing values, duration conversions, credential absence, signed raw webhooks/tampering/replay-window rejection and FCM acceptance/failure. It does not prove vendor OAuth approval, mobile permission/background behavior, a real hardware sync, FCM delivery, or a running Docker stack. Those require the corresponding runtime and real accounts/devices. Upstream 0.11.0 includes schema migrations; back up upstream data before any future upgrade.
