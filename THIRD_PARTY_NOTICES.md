# Third-party project provenance

This companion extension integrates independent open-source projects; their names and marks remain with their owners.

| Component | Version/reference | License | Use |
| --- | --- | --- | --- |
| [Open Wearables](https://github.com/the-momentum/open-wearables) | 0.11.0 / 183e8cc4f6de897dbd288c93d49a691f2cf721ae | MIT, Copyright 2025 Momentum | Independently deployed wearable data service; pinned checkout in ignored vendor directory. Original license retained in deploy/LICENSE.open-wearables. |
| [Open Wearables Flutter SDK](https://github.com/the-momentum/open_wearables_health_sdk) | 0.0.26, resolved by mobile/pubspec.lock | MIT | Mobile health ingestion through its platform SDK dependencies. |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Installed version captured in requirements.lock.txt | MIT | Stateful observation, trend discovery and conversation graphs. |
| [Mem0 OSS](https://github.com/mem0ai/mem0) | Optional mem0ai 2.2.1 | Apache-2.0 | Explicitly enabled semantic memory index; no hosted-service claims. |
| [FastAPI](https://github.com/fastapi/fastapi) | Installed version captured in requirements.lock.txt | MIT | API and application serving. |
| [Flutter](https://github.com/flutter/flutter) | See mobile/README.md | BSD-3-Clause | Native application framework. |
| [pywebview](https://github.com/r0x0r/pywebview) | 6.2.1 | BSD-3-Clause | Windows WebView2 application shell. |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | 6.22.3 | GPL-2.0-or-later with distribution exception | Windows packaging tool and bootloader; its exception permits distributing the bundled application. |
| [Drizzle](https://github.com/drizzle-team/drizzle-orm) | Resolved by hosted/package-lock.json | Apache-2.0 | D1 schema and migration generation during development. |
| [Miniflare](https://github.com/cloudflare/workers-sdk) | 4.20260730.0 | MIT | Local Worker/D1 verification only. |
| [esbuild](https://github.com/evanw/esbuild) | Resolved by hosted/package-lock.json | MIT | Worker bundling during development. |

HiMe and OpenVitals were research references only. Their noncommercial source code is not copied into this implementation. Omi sources are not copied. Python and Flutter package dependencies retain their own licenses; use their resolved distributions for complete transitive notices before redistribution. Model, hosting, vendor APIs and push service terms are separate from source licenses.
