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
- `GET /memories` -> `{items:[memory]}`; `POST /memories` `{text,category,expires_at?}`; `PATCH /memories/{id}` `{text?,category?,expires_at?}`; `DELETE /memories/{id}`. Categories: preference/goal/fact/pattern/context/relationship/boundary. Context defaults to7-day expiry; explicitly null is permitted. GET adds `is_active`; expired records stay editable and are excluded from future model context and derived reminders.
- `GET /chat` -> `{items:[{id,role,content,created_at}]}`; `POST /chat` `{message}` -> `{reply,evidence:[{id,label}],mode,suggestions}`. Suggestions are editable confirmation inputs, not stored facts or scheduled actions. Agent has no unscoped tools.
- `GET /preferences`, `PATCH /preferences` additionally expose `support_style` (listen/reflect/plan, default reflect) and `life_followups_enabled` (default true). Every thread separately requires follow-up opt-in. Existing enabled/model_consent/quiet_start/quiet_end/daily_limit/reminder_categories/timezone fields retain their semantics. Model consent defaults false.
- `GET/POST /threads`, `PATCH/DELETE /threads/{id}` manage owner-scoped important things. Fields: title/details/status(active|paused|completed)/event_at/follow_up_at/follow_up_enabled/outcome. Dates require offset-aware ISO strings. New follow-up dates require explicit confirmation, within180days. Server supplies id/revision/created_at/updated_at, limits100/account, and cancels dependent reminders on change, pause, completion or deletion. Life reminders share global quiet hours/budget and expire24hours after the requested time.
- `POST /evaluate` -> `{created,skipped}`; `GET /reminders` -> `{items:[{id,category,title,body,status,evidence_ids,created_at,scheduled_at,delivery_status}]}`.
- `POST /reminders/{id}/feedback` `{action:done|snooze|dismiss|disable,minutes?:30}` -> reminder.
- `POST /integrations/open-wearables/connect` `{}` -> `{configured,wearable_user_id?,access_token?,refresh_token?,host?,detail?}`; upstream service creates and binds identity, never caller-supplied foreign user ids.
- `POST /integrations/open-wearables/sync` `{}` -> `{imported,detail}`.
- `POST /integrations/open-wearables/webhook` receives original JSON bytes signed with Svix headers; no user bearer token. The verified `data.user_id` is resolved to a persisted account binding; `svix-id` is durably deduplicated and authoritative events are fetched from upstream.
- `GET /integrations/status` -> `{open_wearables:{configured,connected,last_sync_at},model:{configured},push:{configured}}`.
- `POST /devices` `{token,platform:android|ios}` registers a user's FCM device token.
- `GET/PUT /admin/model-config`, `POST /admin/model-test` require database-authorized administrators. Supports disabled/API/local-CLI modes; keys are write-only and responses contain configured flags. See [model services](model-services.md).
- `GET /admin/users?email=...` lists at most100 identity/role summaries; `POST /admin/users/{id}/role` accepts exactly `{is_admin:boolean}`. Requires current admin authority. Demo promotion and removal of the final admin are rejected. No other user's health/chat data is included.
- `POST /internal/admin/grant` uses a server-only one-time setup token to grant the first administrator to an existing formal account. Ordinary session tokens never authorize this operation.

Integration adapter exposes `WearablesClient(base_url,api_key,transport=None)` with `create_user(external_id,name,email)`, `issue_sdk_tokens(wearable_user_id)`, `fetch_events(wearable_user_id,start,end)` and `normalize_*` helpers; client owns transport calls only. Core app owns persisted per-user mapping. `send_push` receives only server-selected recipient tokens and server-controlled configuration.

The demo and deterministic local agent require no external account. A local response is labeled local, not presented as a real LLM response. Background service only sends push if server credentials and device tokens exist; otherwise messages remain in the inbox with explicit delivery state.

Meal records can use `value:null, unit:entry` with nonempty `metadata.note`; the mobile client's `value:1, unit:meal` records portions without inventing calories. Changes may return `memory_sync: disabled|consent_required|synced|cleared|failed`. Failed external synchronization must be surfaced; SQL data remains authoritative. Native push state `queued` means FCM accepted a request, not confirmed device delivery. Persisted sending claims expire after five minutes, and daily push attempt budgets use the user's actual local attempt date.
