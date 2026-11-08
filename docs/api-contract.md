# Companion API contract (implementation shared by web and Flutter)

Base `/api`, JSON, Authorization `Bearer <token>`. All paths other than health/auth/demo are scoped to the authenticated user. Errors use HTTP status + `detail`.

- `GET /health` -> `{status, mode, integrations}`.
- `POST /auth/register` `{name,email,password,timezone,consent_health_data:true}` -> `{token,user}`. Password >= 10 chars.
- `POST /auth/login` `{email,password}` -> `{token,user}`.
- `POST /demo` `{}` -> `{token,user}`; creates isolated, clearly marked synthetic account.
- `GET /me` -> user `{id,name,email,timezone,is_demo,consent_health_data}`.
- `DELETE /me` deletes all personal data, derived memories/reminders and sessions.
- `GET /dashboard` -> `{user,metrics:{steps,sleep_hours,workout_minutes,water_ml},series:[{date,steps,sleep_hours,workout_minutes}],insights:[{title,body,evidence_ids}],last_sync_at,mode}`.
- `GET /events` -> `{items:[event]}`; `POST /events` creates/upserts `{kind,value,unit,start_at,end_at?,source,external_id?,metadata:{note?}}`. kind: steps/sleep/workout/meal/water/mood. Timestamps must be offset-aware. Response `{event,created}`. Corrections invalidate derived dependent memories/reminders.
- `DELETE /events/{id}` removes event and its derived evidence. A persisted `(user,source,external_id)` suppression prevents automatic reimport of a deleted source record; direct POST of that deleted key returns 409. It does not delete records from the phone's health platform.
- `GET /memories` -> `{items:[memory]}`; `POST /memories` `{text,category}`; `PATCH /memories/{id}` `{text}`; `DELETE /memories/{id}`. Memory `{id,text,category,origin,confidence,evidence_ids,created_at}`; category preference/goal/fact/pattern; user edits are authoritative.
- `GET /chat` -> `{items:[{id,role,content,created_at}]}`; `POST /chat` `{message}` -> `{reply,evidence:[{id,label}],mode}`. Agent has no unscoped tools.
- `GET /preferences`, `PATCH /preferences` -> `{enabled,model_consent,quiet_start,quiet_end,daily_limit,reminder_categories:[sleep,activity,meal],timezone}`; HH:MM local time. `model_consent` defaults false and separately gates external model/semantic processing; revocation clears a previously used semantic mirror.
- `POST /evaluate` -> `{created,skipped}`; `GET /reminders` -> `{items:[{id,category,title,body,status,evidence_ids,created_at,scheduled_at,delivery_status}]}`.
- `POST /reminders/{id}/feedback` `{action:done|snooze|dismiss|disable,minutes?:30}` -> reminder.
- `POST /integrations/open-wearables/connect` `{}` -> `{configured,wearable_user_id?,access_token?,refresh_token?,host?,detail?}`; upstream service creates and binds identity, never caller-supplied foreign user ids.
- `POST /integrations/open-wearables/sync` `{}` -> `{imported,detail}`.
- `POST /integrations/open-wearables/webhook` receives original JSON bytes signed with Svix headers; no user bearer token. The verified `data.user_id` is resolved to a persisted account binding; `svix-id` is durably deduplicated and authoritative events are fetched from upstream.
- `GET /integrations/status` -> `{open_wearables:{configured,connected,last_sync_at},model:{configured},push:{configured}}`.
- `POST /devices` `{token,platform:android|ios}` registers a user's FCM device token.

Integration adapter exposes `WearablesClient(base_url,api_key,transport=None)` with `create_user(external_id,name,email)`, `issue_sdk_tokens(wearable_user_id)`, `fetch_events(wearable_user_id,start,end)` and `normalize_*` helpers; client owns transport calls only. Core app owns persisted per-user mapping. `send_push` receives only server-selected recipient tokens and server-controlled configuration.

The demo and deterministic local agent require no external account. A local response is labeled local, not presented as a real LLM response. Background service only sends push if server credentials and device tokens exist; otherwise messages remain in the inbox with explicit delivery state.

Meal records can use `value:null, unit:entry` with nonempty `metadata.note`; the mobile client's `value:1, unit:meal` records portions without inventing calories. Changes may return `memory_sync: disabled|consent_required|synced|cleared|failed`. Failed external synchronization must be surfaced; SQL data remains authoritative. Native push state `queued` means FCM accepted a request, not confirmed device delivery. Persisted sending claims expire after five minutes, and daily push attempt budgets use the user's actual local attempt date.
