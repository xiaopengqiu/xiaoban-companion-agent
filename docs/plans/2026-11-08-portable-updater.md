# Portable client automatic updates implementation plan

> Execute in this session with writing-plans, TDD and parallel implementation; the user requested automatic client checks and updates.

**Goal:** The portable Windows client checks and downloads signed releases automatically, installs them on the next launch or explicit restart, and preserves its data/profile.

**Acceptance correction:** The original0.4.0 candidate exposed a real Windows terminated-process handle race before the candidate probe; it was never a GitHub release. The public immutable feed is superseded by0.4.1 after the regression is fixed. The first user-facing bootstrap release is0.4.1. Final verification must prove the happy path and that the invalid candidate reaches the probe, not merely that the old EXE remains unchanged.

**Architecture:** Keep source and GitHub releases private. Serve public signed update metadata and immutable EXE objects through the existing Site with a dedicated R2 binding. Only a separate operator publish token can upload/sign releases; app model administrators cannot publish executable updates. Pin an Ed25519 public key in the portable executable, stream/verify downloads, and use a detached trusted copy of the current EXE to replace a stopped program atomically.

**Tech stack:** Python/httpx/cryptography/pywebview/PyInstaller, Worker ESM/D1/R2/WebCrypto. No paid subscription or GitHub credential is provisioned in the client.

## Shared update contract

`desktop/update-config.json` is a bundled, tracked PUBLIC configuration: `{schema:1,version:"0.4.0",feed_url:"https://xiaoban-companion.abloom-haven-3343.chatgpt.site/api/desktop-updates/latest",public_key:"<base64 raw Ed25519 public key>"}`. Root generates/persists the private key only in ignored `.credentials/` and the Site's secret store. The build supports an explicit version and output directory without overwriting currently running/previous releases.

GET `/api/desktop-updates/latest` returns `{payload,signature}` or404 before the first publication. The payload has exactly `schema`(1), `version`(strict three nonnegative integers, e.g.0.4.0), `platform`("windows-x64"), `url`(same-origin absolute HTTPS immutable asset path), `sha256`(lowercase64hex), `size`(1–64MiB), `published_at`(UTC ISO), `notes`(max2000chars). Signature is standard base64 Ed25519 over UTF-8 JSON with recursively sorted keys, no whitespace, unescaped Unicode, no floats. Clients validate signature before trusting fields, reject downgrade/unknown schemas/platforms/extra fields/unsafe URLs, and verify exact byte size+SHA256 before staging and again before applying. No GitHub token is in the bundle/feed.

GET `/api/desktop-updates/assets/{version}/{sha256}.exe` streams the immutable published R2 object; headers prevent sniffing and use immutable caching. Never accept an arbitrary object key or URL.

PUT `/api/internal/desktop-updates/{version}` is operator-only: `Authorization: Bearer DESKTOP_UPDATE_PUBLISH_TOKEN` (random>=32chars), `Content-Type: application/octet-stream`, `X-Update-Metadata: <base64 UTF8 JSON {sha256,size,notes,platform}>`, body EXE. Require stream length/64MiB cap and R2 checksum verification, use R2PutOptions.sha256 and known-length streams/FixedLengthStream. Sign with secret `DESKTOP_UPDATE_SIGNING_KEY` (base64 PKCS8 Ed25519). Do not allow ordinary/app-admin bearer tokens to publish. Preserve all current auth/data routes; branch updater routes before generic JSON64KB parsing. Use existing D1 service_state for publish lease/latest manifest, monotonic version activation and immutable duplicate checks. Append no schema unless needed. R2 logical binding is `UPDATES`. Do not prune existing objects during this task.

## Client modules and integration contract

- `desktop/updater.py`: `UpdateManager(config, data_dir, executable=None)`, `status()` dict, `start()`/`stop()` background scheduler, `check_async()`/`download_async()`, `set_auto_download(bool)`, `has_pending()`, `launch_pending()` bool. Constructor accepts a validated config dict; executable defaults to sys.executable only when frozen. Source mode can check but cannot replace Python. Status fields: `current_version`, `latest_version`, `phase`(idle/checking/current/available/downloading/ready/error/applying), `progress`(0–100), `auto_download`(true default), `message`, `notes`. Methods thread-safe, non-overlapping, with bounded network total deadlines and cancellation on shutdown. Initial check in background after GUI startup, then every6h; default auto-download, no forced close while typing. Settings/state/staging are separate files under `<data_dir>/updates`; original SQLite, browser profile, desktop-settings untouched.
- `desktop/update_apply.py`: detached helper execution entry `apply_update_request(Path)`; launch via a copied trusted CURRENT EXE `--apply-update <requestfile>`. Wait only verified owning app processes, never kill unrelated processes. Revalidate signed manifest+hash/size, reject symlinks/reparse surprises/changed target, preflight writable target directory, self-test the new EXE using existing `--self-test --report` on disposable data, preserve rollback EXE, atomic same-directory replace, restart with the same `--data-dir`. Failure before replacement keeps old EXE; failed replacement/launch restores backup and restarts old, with a skip-update-once flag to avoid restart loops. Never overwrite/delete the data directory. Bound all waits and record actionable, nonsecret results.
- `desktop/main.py`: handle `--apply-update` BEFORE data dir/server/mutex/GUI initialization. Normal frozen startup applies an already verified pending update before opening the app, unless `--skip-update-once`. Wire manager to the menu “检查更新” and a child update window. `--self-test`/`--ui-smoke` must not hit public update servers. Retain existing local/cloud modes, single-instance and server shutdown semantics.
- `desktop/update_ui.py`: separate native child window, does not navigate/replace the main chat. Shows version/check/progress/error/ready and auto-download preference. “重启更新” explicitly requests restart; the main thread closes gracefully only after helper launch succeeds. No privileged bridge is exposed to the online page. Escape notes and messages; do not offer arbitrary update URLs.
- Packaging includes update config and UI assets, accepts `-AppVersion` and `-OutputDir`; existing artifacts left intact. Current release0.3 has no updater and needs one bootstrap download; later releases update automatically.

## Task1: Signed client and replacement engine (owner updater_core)

Write failing tests for signatures/tampering/downgrades/URL and size bounds, streamed truncation/hash mismatch/partial files/concurrent operations, offline checks, state persistence, pending validation, target change and rollback. Implement the smallest secure code; helper tests use temporary paths and owned subprocesses. Coordinate the exact manager API with desktop integration. Run focused tests and report freeze.

## Task2: Site delivery (owner update_delivery)

Write real Miniflare R2+D1 failing tests for unauthorized publishing, stream/hash/size mismatch, immutability and concurrent monotonic latest activation; verify Ed25519 canonical JSON with a Python fixture. Implement dedicated Worker module/route hooks and manifest r2 binding. Full existing cloud suite+build must pass; do not call native Sites tools or write real secrets.

## Task3: Native integration/build (owner updater_desktop)

Implement menu/child UI/startup/shutdown/config and versioned packaging against the manager API; meaningful lifecycle tests before behavior. Smoke code must skip public checks. Build fixture version0.3.99 and final0.4.0 only after source freeze/public key is supplied. Verify real packaged health/UI/restart and unchanged user data; no production files or existing user app are replaced during tests.

## Task4: Root publication and end-to-end acceptance

Generate/persist key and operator token safely; add publisher CLI with private stdin/secure env rather than command-line secrets. Reuse existing Sites project/audience/auth pepper. Open/push exact source/package/deploy with R2 binding. Publish final signed EXE to the feed. On an isolated portable/profile directory, use fixture0.3.99 to check/download0.4.0 from actual HTTPS, exercise the detached replacement and restart, then verify real version and SQLite/login/settings persistence. Also verify a bad signed candidate/probe fails safely without changing old target. Independently review update chain/process/path handling, run appropriate suites, publish GitHub0.4.0 assets/source/hashes, and push all changes. Report one-time bootstrap requirement honestly.
