# AI proactive care implementation plan

**Goal:** AI periodically decides whether a consenting user needs personal attention; only a justified decision creates an inbox conversation that Web/Windows/Android users can reply to. Add free-tier Android external notifications and administrator-only configuration in all clients.

**Architecture:** Work in the isolated `codex/proactive-care` checkout based on f03884c. Preserve the original checkout's ongoing background/Web Push/streaming work and merge completed upstream changes before publication. Reuse the administrator model runtime and existing account/quiet-hour protections. Keep AI care conversations separate from deterministic lifestyle reminders. Store write-only encrypted push credentials server-side. Native Android initializes the free JPush transport only after the user opts in and receives public SDK configuration; never embed its master secret or activate paid features. No purchased plan or external credentials are invented.

**Tech Stack:** Worker/D1/Drizzle, Python FastAPI/SQLite, shared vanilla Web UI for browser/Windows, Flutter/Android JPush SDK, existing dual-client publication and signed Windows feed.

## Shared API contract (prefix /api)

- `GET/PATCH /admin/care-config`: admin only. Editable `{enabled,analysis_interval_minutes,cooldown_minutes,daily_limit}`. Defaults false/120/240/2; interval15..1440 (hosted scheduler enforces60-minute multiples), cooldown15..10080, daily_limit1..5. GET adds masked operational health/model readiness, not other users' personal summaries.
- `POST /admin/care-check`: `{}` analyzes the administrator's own account immediately, bypassing only the due-time check, never consent/quiet hours/budget/deduplication. Return `{status,created,care_id?,reason_code}`. Background uses the existing server-authenticated evaluation/scheduled path; do not require user sessions for background work.
- `GET/PATCH /care/preferences`: `{enabled:false}` default. Enabling requires existing `preferences.model_consent=true`; clients explain the consent and obtain it explicitly. Disabling care does not disable ordinary model chat.
- `GET /care`: `{items:[thread summaries],unread_count,status:{enabled,model_allowed,available,last_checked_at,next_check_at,reason_code}}`.
- `GET /care/{id}`: `{thread,messages:[{id,role,content,created_at,request_id?,reply_status?}]}`. Every read/write is scoped to authenticated user; cross-user IDs return404.
- `POST /care/{id}/read`: marks that user's thread read; return `{read:true}`.
- `POST /care/{id}/reply`: `{message,request_id}` (1..2000 chars, unique request ID). Persist the user's reply exactly once, respond using the care thread context plus authorized personal summary. Return the complete thread snapshot plus `reply_status:'complete'|'pending'`; on model failure preserve the user turn, return pending without fabricating an assistant answer. Same ID/content is safe to retry; same ID/different content409.
- Thread summary fields: `{id,title,preview,created_at,updated_at,unread,message_count,source:'ai'}`. Thread detail adds evidence IDs owned by that user; model secrets and raw prompt are never returned.
- `GET/PATCH /admin/push-config`: administrator only. Primary provider `disabled|jpush` (free/basic tier). Fields `enabled,app_key,master_secret` (write-only blank-preserves, explicit clear supported), and bounded public Android SDK options as agreed with the mobile owner. Master secret is encrypted at rest with a purpose-separated key. GET returns secret presence, never secret value. No custom arbitrary push URLs or purchase/upgrade API.
- `GET /push/native-config`: authenticated user's public SDK bootstrap `{provider,enabled,configured,app_key,revision,client_options}`; no server secret. Client options are strictly limited to public parameters needed for native SDK initialization. SDK readiness/partial manufacturer coverage is reported honestly.
- `POST /push/devices`: `{provider:'jpush',registration_id,config_revision}` registers current account's own Android device (max5); `DELETE /push/devices` unregisters its registration ID. Old native FCM routes continue to work. Credentials/config changes invalidate stale project registrations.
- `POST /admin/push-test`: test only the current administrator's registered devices after an explicit click. Default body generic; payload only care ID/type. Do not send test notifications during implementation without actual owner credentials/device action.

## AI decision and disturbance controls

Use existing configured API/CLI model; no configured/consented model means no proactive message and visible setup status. Send bounded active memories, boundaries, important threads, recent structured events and prior care topic/time metadata for the same user. Require strict JSON `{should_contact:boolean,title:string,message:string,topic_key:string,evidence_ids:string[],reason:string}`; when false, store analysis timing only and produce no inbox or external message. True requires real owned active evidence. Invalid JSON, timeouts, stale/deleted evidence, changed consent/preferences/config, or model errors fail quietly without a fabricated care message.

Persist next due time even for no-contact decisions. Separate analysis interval from send cooldown/daily limit. Enforce quiet hours and timezone, opt-in, duplicate topic/evidence suppression and bounded concurrency. Use a dedicated analysis lease without blocking normal conversation for the whole model call; revalidate the current authorization/context before committing. Durable per-account state, unread/read markers, idempotent replies, account cascade deletion and restart behavior are required. Background model work is bounded and paginated; report scheduler health without claiming a trigger exists until a real timer is verified.

## Tasks and ownership

1. **Cloud backend:** new care/push modules and real Miniflare D1/model/HTTP contract tests, strict admin authorization and encryption, safe server background hooks. Reserve an additive care migration after the ongoing upstream0004 migration; do not edit deployed migrations. Coordinate exact client_options with mobile. Freeze APIs and give fixture examples early.
2. **Web/Windows UI:** inbox care tab/thread/reply/read state; opt-in and pending replies; administrator care frequency and push settings with masked secret controls, no ordinary-user admin requests; preserve per-account/draft guards. Shared Web UI covers Windows online mode. Add real behavioral JS tests and inspect rendered app where supported.
3. **Android:** same care/reply/admin workflows, free native push bootstrap/consent/token/logout/tap lifecycle, native SDK actual integration, defaults without credentials remain usable with inbox. Use official SDK dynamic AppKey support; include necessary vendor extensions when feasible, clearly identify unconfigured channels. Run Flutter tests/analyze and actual APK build/manifest checks.
4. **Root Python/integration/delivery:** equivalent FastAPI/SQLite care/push routes, runtime scheduling and tests; merge upstream completed work without overwriting its uncommitted checkout, independent review and E2E controlled-model tests. Deploy the existing Site, verify real scheduling/readback, build Windows+Android0.5.0/code3, upload both through the release gate, publish signed Windows feed, push all this feature's source. Preserve existing admin credentials/auth pepper and signing identity.

## Acceptance

- A controlled model returns should_contact=false: no message or push after repeated scheduled checks, next due advances.
- True with real evidence: one care thread, visible on both clients; user reply receives a contextual model response; refresh/restart does not lose or duplicate messages. Read/unread is shared per account.
- Ordinary users cannot change frequency or read/update push secrets; blank admin secrets preserve existing values; cross-account data/notifications are isolated.
- Quiet time/cooldown/budget/consent changes during inference prevent delivery. No model configured, malformed responses and unavailable services remain quiet and show truthful readiness.
- Native adapter registers only after opt-in, receives public configuration, renders/taps the correct care thread and unregisters on logout. Test transport failures and duplicates. Real third-party delivery requires administrator credentials and real devices; absence is reported, never fabricated.
- Existing features, updater, dual-client release artifacts and other ongoing branch changes remain intact.
