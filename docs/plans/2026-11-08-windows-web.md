# Windows and Public Web Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish the repository to the user's GitHub, ship a runnable Windows application, and deploy an HTTPS Web application with public registration and isolated accounts.

**Architecture:** Keep the existing FastAPI service and responsive UI as the local application. A packaged Windows WebView2 host embeds the Python runtime and can select local data or the deployed HTTPS account service. A separate Cloudflare Worker adapter implements the shared API and deterministic policies over D1, because the managed Web runtime cannot execute the Python/LangGraph process. All clients can use the same hosted account endpoint; local and cloud data domains are explicitly separate.

**Tech Stack:** FastAPI/Python, PyInstaller/pywebview/WebView2, existing vanilla Web UI, Worker ESM, D1/Drizzle migrations, Miniflare, GitHub CLI and Sites publication.

---

1. **Remote repository:** verify credential availability and absence of secrets, create private `xiaopengqiu/xiaoban-companion-agent`, push the existing history to `main`, then push tested additions without force.
2. **Windows desktop:** test frozen resource lookup, persistent local data, safe local/cloud URLs, loopback port selection and clean server shutdown before implementing the launcher. Build the actual executable with the existing Web assets and Python dependencies. Test the executable, not only the Python source.
3. **Cloud API:** implement scoped registration/login, persistent records and sessions, memory, chat, reminders and preferences using D1. Use schema migrations, not request-time DDL. Test a real local D1 instance, including concurrent/idempotent operations, cross-account denial, deletion, timezone and reminder policies. Secrets stay in server runtime configuration.
4. **Public-access controls:** bounded authentication/demo rates, safe errors, request size/time limits, trusted proxy handling, origin/host and content-security policy. Hosted password hashing uses a server-only pepper and runtime-compatible PBKDF2; a missing pepper fails closed.
5. **Publication:** retain the exact Sites ID and public audience requested by the user. Copy the reviewed hosted source and shared UI to an ignored, isolated Sites checkout; build, push, package and deploy the same source commit. Verify actual HTTPS registration, login, isolation, persistence and UI actions.
6. **Unattended reminders:** implement a protected background evaluation writer and connect a supported scheduled runner if the hosting platform permits it. Do not describe page-load evaluation as always-on background work. Distinguish delivered notifications from inbox records.
7. **Delivery:** independently review the final changes, run relevant Python/Node/Worker tests, launch the Windows artifact, verify repository refs and deployment status, publish portable artifacts and update operational instructions and capability boundaries.

The user explicitly chose public registration with separate user logins. Source visibility remains private unless the user requests otherwise. No real health data or existing local accounts are uploaded during verification; online checks use disposable synthetic accounts.
