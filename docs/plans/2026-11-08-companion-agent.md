# Wearable Companion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver a runnable Chinese companion application that turns wearable and manually recorded behavior into attributable memories, conversations, and respectful proactive reminders on iOS and Android.

**Architecture:** Preserve MIT Open Wearables as an independently deployable upstream data service, pin its revision, and implement a companion service with authenticated per-user state. A LangGraph workflow reads deterministic statistics and explicit memories; optional model and semantic-memory adapters augment a usable local baseline. A persistent scheduler evaluates fresh evidence, quiet hours, notification budgets and feedback before sending through configured delivery adapters.

**Tech Stack:** Python 3.12, FastAPI, SQLite for a single-process local deployment, LangGraph, httpx, vanilla responsive web UI, Flutter + Open Wearables SDK. Upstream PostgreSQL/Redis are isolated in its official Docker deployment. Native push and external models are enabled only when credentials are configured.

---

### Task 1: Data and API foundation
Files: `companion/app.py`, `companion/store.py`, `companion/agent.py`, `tests/test_api.py`.
Write and run failing API tests before implementation. Cover account auth, explicit data consent, scoped CRUD, timezone-aware records, idempotent ingestion and corrections, memory evidence, chat persistence, and full account deletion. Implement a FastAPI app factory and persistent SQLite store. Verify tests with `.venv/Scripts/python -m pytest tests/test_api.py -q`.

### Task 2: Proactive accompaniment
Files: `companion/policy.py`, `tests/test_policy.py`.
Test quiet hours across midnight, daily budgets by user timezone, stale data rejection, missing-data treatment, idempotency, feedback and task expiry. Implement a LangGraph workflow, deterministic statistics, an opt-in background scheduler, an inbox and delivery outbox. Keep medical diagnosis outside outputs. Check persistence over service restart.

### Task 3: Open Wearables and delivery adapters
Files: `companion/integrations.py`, `tests/test_integrations.py`, `deploy/*`, `scripts/*`.
Read pinned upstream source for exact route and schema contracts. Test normalization, deduplication, binding identity and webhook verification using representative upstream fixtures; mock transport only at the network boundary. Store administrator keys on the server, issue scoped SDK tokens through an authenticated endpoint, and provide upstream installation/Compose instructions. Implement optional credential-driven push with truthful status reporting and explicit failures.

### Task 4: Responsive experience
Files: `web/index.html`, `web/app.js`, `web/styles.css`.
Build a warm, clear Chinese dashboard: onboarding/demo, today, timeline, memories, reminders/settings and grounded chat. All edits persist through real API endpoints. Distinguish simulated data, local rules and configured model modes. Verify real actions in a browser and inspect narrow-screen layout.

### Task 5: iOS and Android client
Files: `mobile/pubspec.yaml`, `mobile/lib/*`, `mobile/test/*`, `mobile/README.md`.
Provide Flutter source with login/demo, health permission/sync, dashboard, conversation, manual records, memory and reminder feedback/settings. Use only scoped SDK tokens; platform data permissions and push configuration are documented. Generate native projects with Flutter tooling and run Flutter analyze/tests/build if a usable SDK is available; otherwise explicitly document the unverified build boundary.

### Task 6: Integration and review
Files: `README.md`, `.env.example`, `docs/architecture.md`, `tests/*`.
Run all backend tests, compile Python, validate integration fixtures, perform browser flow checks and independent code/spec review. Fix important findings and repeat the affected checks. Record exact commands, outcomes and remaining device/credential prerequisites. Supply a local launch command and operational limitations; never claim live wearable or push verification without hardware/credentials.
