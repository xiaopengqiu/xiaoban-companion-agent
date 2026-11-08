# Personal companion v0.3 Implementation Plan

> Execute with writing-plans, test-driven-development and subagent-driven-development in the existing checkout; the user has authorized implementation and publication.

**Goal:** Make Xiaoban understand confirmed personal context, follow important life events across days, and adapt its conversational support style.

**Architecture:** Preserve the existing Python/SQLite and Worker/D1 API contract, append one durable user-scoped `threads` table, enrich memories with category and expiry, and integrate both into grounded conversation and the existing reminder policy. Share the responsive web UI across web and desktop; preserve existing accounts/data and append D1 migrations. Model credentials remain server-only and optional; no model service is provisioned or paid service enabled.

**Tech Stack:** FastAPI/SQLite/LangGraph, Worker ESM/D1/Drizzle, vanilla accessible HTML/CSS/JS, pywebview/PyInstaller.

**Authorized scope addition:** The user requested two administrator-controlled model modes (compatible API or server-local Claude/Codex), initial administrator registration, and delegation to other registered users. Implement encrypted global settings, persisted role checks and final-admin protection, a one-time server-secret bootstrap, and a stateless authenticated CLI gateway for the Worker. Public model service remains unconfigured until an administrator supplies its real endpoint/credentials; no developer machine is made a permanent public backend. Account credentials must never enter source, logs or release notes.

## Product and shared API contract

1. Memory categories: existing `fact`, `preference`, `goal`, `pattern`, plus user-confirmed `context`, `relationship`, `boundary`. POST/PATCH memories accept `expires_at` (ISO with offset or null). `context` defaults to seven days if expiry omitted on creation; other categories default null. An explicitly null context expiry is permitted by the user. PATCH preserves existing expiry unless explicitly supplied. GET includes computed `is_active`; expired records remain editable, but must not enter chat, derived reminders, or model context. User edits bump context revision. Discard pre-expiry chat context that could reintroduce expired information. The UI labels context expiry and allows correction/renewal.
2. Preferences add `support_style` enum `listen|reflect|plan` (default reflect), and `life_followups_enabled` boolean (default true). Old preferences work through defaults. Model prompt receives style and active confirmed context; local free-form replies should respect style instead of defaulting to step counts. Known health queries keep existing deterministic facts. No emotion/personality diagnosis or invented explanations.
3. Important things: GET `/api/threads` -> `{items:[...]}`; POST `/api/threads`; PATCH/DELETE `/api/threads/{id}`. Fields: `id`, `title` (1–120), `details` (0–2000), `status` (`active|paused|completed`, default active), `event_at` (nullable ISO with offset), `follow_up_at` (nullable ISO with offset), `follow_up_enabled` (boolean, default false), `outcome` (0–2000), `revision` (server integer), `created_at`, `updated_at`. POST/PATCH validate known fields and types. Opt-in follow-up requires an explicitly supplied valid date when newly enabled/rescheduled, within the next 180 days (allow <=5min clock skew). Do not reject an unrelated edit solely because its unchanged follow-up date is now past. Limit 100 threads/account. Identity always comes from bearer session. Completed/paused/deleted or changed threads cancel pending dependent reminders immediately and bump conversational context revision.
4. Follow-up uses existing evaluate/delivery path: only active opted-in threads, global enabled, life follow-ups enabled, quiet hours, global user-local-day budget, and freshness of follow-up time. A due thread produces one generic invitation with dedupe `life:{thread_id}:{revision}`, evidence id=thread id, category `life`, expiry=follow_up_at+24h. Never infer that the event succeeded/finished. Life candidates precede health candidates; health-data freshness does not gate user-requested life follow-ups. Completion/revocation must prevent delivery and cancelled reminders must not resurrect. Existing FCM lockscreen text remains generic. No automatic cloud timer is claimed: browser-open polling may request evaluation when a known thread becomes due; local Python scheduler works while running.
5. Chat response additionally includes `suggestions` array. Deterministic suggestions only for substantive first-person life/context statements (e.g. 最近/这周/面试/备考/项目/准备/担心), not measurement questions, hypothetical examples, or explicit remember commands already saved. Maximum two: `{type:'memory', text:<verbatim bounded user statement>, category:'context', expires_at:<7 days>}` and `{type:'thread', title:<bounded user statement>, details:<verbatim>, follow_up_enabled:false}`. No inferred fact is automatically persisted. UI opens editable confirmation forms before POST; no promise to follow up before explicit opt-in and successful save. `suggestions:[]` remains valid. Model generation continues with existing configured compatible API and user consent; active threads (max 6), active memories, support style and current facts are bounded context.

## Task 1: Python personal context and important things

Files: `companion/store.py`, `companion/app.py`, `companion/agent.py`, optional new `companion/life.py`, `tests/test_personal_companion.py`.

- Write failing real API tests: tenant CRUD, context default/explicit expiry, expiry absent from model context/history, style prompt/local fallback, thread opt-in validation, due/quiet/budget/dedupe/cancellation, future/overdue edits, and account cascade.
- Run focused tests and capture missing-feature failures before implementation.
- Append schema safely and implement shared contract, keeping health policy semantics intact.
- Run focused plus existing Python tests; inspect actual HTTP/model boundary payloads. No live provider call.

## Task 2: Worker/D1 equivalent

Files: `hosted/worker/index.js`, optional `hosted/worker/life.mjs`, `hosted/db/schema.js`, append Drizzle 0003 with metadata, new `hosted/tests/personal.test.mjs` or extend existing tests.

- Write and run equivalent failing real Miniflare/D1 API tests.
- Implement contract with D1 ownership predicates/leases and batches, preserving auth/rate/body/security bounds and existing network budgets.
- Append migrations using Drizzle generation; do not edit 0000–0002.
- Run complete Worker suite and build. Include scoped outbound model tests, expiry/history checks and old-account defaults.

## Task 3: Shared web experience

Files: `web/app.js`, `web/styles.css`, optional new `web/companion-ui.mjs`, tests `tests/test_companion_ui.mjs`.

- Add meaningful pure state/format tests where useful before helper behavior, not snapshot tests that duplicate markup.
- Home foregrounds personal conversation and active important things; health remains available as secondary information. Add an important-things page with real CRUD, status/outcome and opt-in follow-up controls. Add grouped personal understanding, expiry status/editing, and style selection.
- Chat suggestion cards open editable confirmation forms; successful save refreshes state. Preserve escaping, request cancellation, cross-account response guards and mobile layout.
- Due checks use existing server evaluate when page is open and an opted-in known thread is due; avoid concurrent overlapping polls. Explain runtime background availability honestly.
- Verify actual web interactions and narrow/mobile layout using the supported browser/native UI tools.

## Task 4: Independent review, desktop, documentation and publication

- Check API parity, stale context, scheduling consent and cross-account leakage independently; resolve material issues and rerun affected checks.
- Rebuild Windows using current Python/web and run packaged HTTP/WebView/restart checks. Publish v0.3.0 assets with hashes and third-party notices.
- Use existing Sites project `appgprj_6af025a94c788191a68000c658aa552a`, preserve audience and AUTH_PEPPER. Copy final source to isolated Sites checkout, push exact source, package/save/deploy. Read skill and refresh credentials as required; never expose secrets or create a replacement Site.
- Run live HTTPS disposable-account tests for new fields/thread CRUD/isolation/confirmation and clear only those fixtures. Verify ordinary prior accounts/schema remain compatible.
- Commit/push source to GitHub main, verify remote SHA/assets and update docs with actual verification and remaining model/timer/device boundaries.
