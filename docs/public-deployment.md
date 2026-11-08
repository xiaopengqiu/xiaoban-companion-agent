# Public account deployment and runtime boundaries

The public website serves its own HTTPS API and stores accounts and personal records in a bound Cloudflare D1 database. It does not forward visitors' requests to a developer's `localhost`, and registration does not require a ChatGPT account. A cloud account and a desktop offline account are separate data domains unless an explicit future migration is implemented.

The cloud build inserts `<meta name="companion-runtime" content="cloud">`. The shared web interface then says **规则陪伴模式** for deterministic replies and explicitly explains that records are stored in the online service. The absence of an external language model does not mean that a cloud account's data stays on the visitor's computer.

## Actual runtime capabilities

| Runtime | Data and account storage | Companion implementation | External integrations |
| --- | --- | --- | --- |
| Public Cloudflare Worker | D1; independent email/password accounts; opaque revocable sessions | JavaScript implementation of the deterministic, attributable memory and reminder policy | Configurable HTTPS model API with per-user consent, remote Open Wearables HTTP/Svix adapters, and FCM HTTP v1 adapter. Each remains unconfigured until its server credentials and reachable services exist; status endpoints must say so. Mem0 is not deployed in this runtime. |
| Python service | Its own SQLite database and sessions | LangGraph workflow, deterministic policy, background scheduler | Configured Open Wearables, optional Mem0 and FCM adapters |
| Desktop offline mode | Its own local data directory | The bundled Python service | Offline mode blocks external integrations; opening a cloud window does not import local records. |

The constrained Worker does not run the Python/LangGraph process, Open Wearables PostgreSQL/Redis/Celery stack, or an embedded LLM. Matching API names do not prove those services are present. Real wearable synchronization and native push require separate configured deployments and real device testing.

**Current public-release scheduling boundary:** the selected Sites hosting does not install a Worker cron trigger, and this release does not create an additional external scheduler. Exporting a `scheduled` handler alone does not make it run. The protected `/api/internal/evaluate` endpoint remains available for an explicitly configured future scheduler. The public site therefore cannot currently guarantee timed reevaluation or snoozed reminders after the page closes. Users can request an evaluation in the message inbox; a configured, signed Open Wearables webhook can also trigger sync, evaluation and configured FCM delivery when new data arrives. Event-triggered evaluation does not replace scheduled checks when no event arrives. The Python/desktop service's background loop remains a separate capability.

For remote wearable access, configure the Worker-only secrets `OPEN_WEARABLES_API_KEY`, `OPEN_WEARABLES_APP_ID`, `OPEN_WEARABLES_APP_SECRET`, and `OPEN_WEARABLES_WEBHOOK_SECRET`, together with the reachable HTTPS service host `OPEN_WEARABLES_URL` and optional phone-facing `OPEN_WEARABLES_PUBLIC_URL`. The management/application credentials never enter the phone or browser. Cloud native push uses the deployment secret `FCM_SERVICE_ACCOUNT_JSON`; this differs from the Python adapter's service-account file path. The adapter code and synthetic network tests do not replace real upstream/device/OAuth and FCM verification.

The initial cloud pilot explicitly bounds each account to 2,000 behavior events and each event's metadata to 4 KiB. A full quota rejects a new event with a clear error; it does not silently hide new data behind the oldest records. Corrections and deletion remain available. The chat API returns the most recent 200 messages. These bounds protect the constrained Worker and differ from the desktop SQLite service's storage capacity.

## Public Worker secrets and identity

Set `AUTH_PEPPER` as a deployment secret with at least 32 unpredictable characters. It is never stored in D1, embedded in browser assets, checked into Git, returned by health endpoints, or printed in logs. The Worker applies HMAC-SHA256 with that server secret to the password, then salted PBKDF2-SHA256 at 100,000 iterations. Missing/short pepper must make registration and login return an explicit configuration failure rather than silently weakening hashing. Rotating it requires a deliberate account/password migration; replacing it arbitrarily prevents existing passwords from verifying. The Python service retains its separate PBKDF2-SHA256 600,000-iteration format.

Cloudflare's runtime source declares `DEFAULT_MAX_PBKDF2_ITERATIONS = 100'000`: [workerd limit enforcer](https://github.com/cloudflare/workerd/blob/main/src/workerd/io/limit-enforcer.h); its supported browser-compatible operations are described in [Workers Web Crypto](https://developers.cloudflare.com/workers/runtime-apis/web-crypto/). This is a runtime constraint, not a claim that the two password formats are interchangeable. Sessions store only token hashes, have an expiration, and `/api/auth/logout` revokes the current session. Account deletion cascades through personal records and sessions. The API resolves identity from the bearer token; caller-provided user IDs never select another person's data.

Abuse limits in the public Worker belong in D1 so parallel isolates cannot bypass an in-memory counter. Only the platform-controlled `CF-Connecting-IP` supplies the external peer on a deployed Worker. Do not switch to arbitrary caller `X-Forwarded-For`, `X-User-ID`, or a browser-supplied identity header. Local Miniflare may inject its own fixture headers; those fixtures do not prove an edge deployment's proxy policy. Account writes and reminder evaluation need database leases or equivalent atomic operations, not a JavaScript process-global lock. Global demo capacity also needs an atomic reservation.

## Python production options

The Python service remains useful for a controlled single-process deployment behind an HTTPS reverse proxy. It is not the runtime used by the public Worker. Local defaults preserve offline and development use; explicitly enable the following when exposing the Python service:

```dotenv
COMPANION_PUBLIC_MODE=true
COMPANION_ALLOWED_HOSTS=companion.example.com
COMPANION_AUTH_RATE_WINDOW_SECONDS=60
COMPANION_LOGIN_RATE_LIMIT=10
COMPANION_REGISTER_RATE_LIMIT=3
COMPANION_DEMO_RATE_LIMIT=2
COMPANION_PUBLIC_DEMO_ENABLED=false
COMPANION_PUBLIC_DEMO_MAX_ACCOUNTS=100
```

`COMPANION_ALLOWED_HOSTS` contains hostnames, not URLs, and public mode rejects missing or wildcard allowlists. API schema/developer docs are hidden in public mode. The unauthenticated health response only contains `{status:"ok"}`. API responses use `Cache-Control: no-store`; pages receive a self-origin CSP, no inline/eval script permission, framing denial, MIME sniffing prevention, restricted browser permissions, and HSTS for the public HTTPS deployment. Auth/validation errors do not echo submitted passwords. JSON request bodies are bounded to 64 KiB even without `Content-Length`.

Production auth limits are applied before password hashing. They are bounded in process memory and therefore protect a single Python process only. Multi-replica Python deployments need a shared/edge limiter; do not call the in-memory limiter a cluster-wide quota. Demo creation is disabled by default in public mode. If explicitly enabled, it has both an IP rate limit and an atomically checked account cap. A full cap returns a friendly failure and preserves real registration.

Proxy headers are ignored unless the actual socket peer belongs to `COMPANION_TRUSTED_PROXY_IPS`, an explicit comma-separated IP/CIDR allowlist. With trusted proxies, the application walks `X-Forwarded-For` from the right and stops at the nearest untrusted hop. For example, set `COMPANION_TRUSTED_PROXY_IPS=127.0.0.1/32` only when a trusted local reverse proxy overwrites/appends the header correctly and direct origin access is blocked. Never use a blanket trusted-proxy network. Configure Uvicorn consistently: an upstream server that already blindly trusts forwarded headers can destroy the actual peer information before this code sees it. See [Uvicorn proxy settings](https://uvicorn.dev/settings/#http) and [Starlette host validation](https://www.starlette.io/middleware/#trustedhostmiddleware).

`COMPANION_RATE_LIMIT_ENABLED` can explicitly control Python auth limiting, but disabling it on an exposed service removes the application-level brute-force bound. Do not copy local test settings into a public deployment.

## Browser behavior and verification

The browser calls same-origin `/api` endpoints. Ordinary requests time out after 15 seconds; chat and wearable sync allow 60 seconds. It cancels old requests when signing out, ignores responses from previous account sessions, synchronizes logout across tabs, and asks the server to revoke the current token. Password failures display a generic message and clear the password field. User notes, memories, model output and error text are escaped or inserted as text, not trusted HTML.

Opaque session tokens are held in origin-scoped browser storage. This is not an HttpOnly-cookie deployment: strict CSP, escaping and avoiding third-party scripts remain important. The API does not enable permissive cross-origin credentials. A future cookie conversion would require a separate CSRF design rather than merely changing the storage location.

Before claiming a public release is ready, verify it at the actual HTTPS URL: register an independent account, log out/in, confirm records persist, test a second account's isolation, correct/delete a memory, and delete the first account. Test the real D1 binding and migration, required secrets, auth throttling and malformed/chunked body bounds. Scheduled evaluation must use a protected server trigger or platform schedule and rotate through all accounts with a durable cursor when processing bounded batches. Do not infer a functioning cloud scheduler merely from a local Python background test.

Size the scheduler's batch for the actual hosting plan's D1 query allowance. Cloudflare lists 50 queries per invocation on the Free plan and 1,000 on Paid, with a batch counted as one query: [D1 platform limits](https://developers.cloudflare.com/d1/platform/limits/). A memory limit of 128 MiB alone does not establish a sufficient query budget. Demo cleanup must also fit that allowance. Larger user populations take multiple scheduled batches; do not promise every account a one-minute evaluation cycle without measuring that deployment.

Backend coverage for the Python public boundary is in `tests/test_public_access.py`; Worker/D1 coverage is separate under `hosted/tests/`. Tests with synthetic data and network boundaries do not establish real wearable authorization or native push delivery.

## Build dependency audit

The release build's `npm audit` reports eight advisories in development dependencies (Drizzle tooling and Miniflare dependency chains); `npm audit --omit=dev` reports zero. The deployed Worker bundles only its own JavaScript modules and embedded web assets, with no Drizzle, Miniflare or esbuild runtime import. This does not erase the development-tool advisories: keep build inputs trusted, avoid exposing local development services, and review compatible tool upgrades before the next build. No automatic breaking dependency upgrade was applied during publication.
