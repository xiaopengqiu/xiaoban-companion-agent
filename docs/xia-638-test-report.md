# XIA-638 test report

Date: 2026-11-09

## Isolated runtimes

- Docker Engine 26.1.3 on Linux x86_64 was used so the host's GLIBC 2.32 did not need to be upgraded.
- Hosted tests used `node:24-bookworm-slim` at digest `sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9` (Node 24.22.2, npm 11.19.1, Debian bookworm/GLIBC 2.36). Python 3.11.2 was installed only inside the disposable container for the cross-language desktop-update test.
- The hosted baseline failure was also reproduced with the host's exact Node 24.15.0 binary mounted read-only into the bookworm container, retaining the compatible container GLIBC.
- Flutter tests used `ghcr.io/cirruslabs/flutter:3.35.7` at digest `sha256:d271a49ddd8ce1be6c7954f7eabe0e03766c8a1bf1430f0dce780888a21ed408`: Flutter 3.35.7, Dart 3.9.2, DevTools 2.48.0.
- Source was bind-mounted read-only and copied to disposable container storage before dependency resolution. No container cache, `.dart_tool`, build output, or dependency artifact was added to Git.

## Passed

- `PYTHONPATH=. /tmp/xia-638-py311/bin/pytest -q tests/test_care.py tests/test_care_review.py`: 38 passed.
- `node --test tests/*.mjs`: 50 passed.
- `docker run --rm --mount type=bind,src="$PWD",dst=/src,readonly --workdir /src/hosted node:24-bookworm-slim node --test --test-concurrency=1 tests/care-concurrency.test.mjs tests/care.test.mjs`: 32 passed after correcting two test-fixture defects exposed by the first container run.
- `cd hosted && npm run build`: built the self-contained Worker, 10 static assets, and schema migrations.
- `node --check hosted/worker/care.mjs && node --check hosted/tests/care.test.mjs && node --check hosted/tests/care-concurrency.test.mjs && node --check web/app.js && node --check web/care-ui.mjs`: passed.
- Disposable Flutter container: `flutter pub get` passed; `flutter analyze --no-pub` reported `No issues found`; `flutter test --no-pub` passed all 34 tests.
- `git diff --check`: passed.

## Hosted full-suite result

The reproducible container command installed the locked npm dependencies and the Python runtime required by `desktop-updates.test.mjs`, then ran the complete hosted suite:

```sh
docker run --rm --mount type=bind,src="$PWD",dst=/src,readonly --workdir /tmp node:24-bookworm-slim sh -lc '
  set -eu
  apt-get update
  apt-get install -y --no-install-recommends python3
  ln -s /usr/bin/python3 /usr/local/bin/python
  mkdir -p /tmp/repo
  cp -a /src/hosted /tmp/repo/hosted
  cp -a /src/web /tmp/repo/web
  rm -rf /tmp/repo/hosted/node_modules /tmp/repo/hosted/dist
  cd /tmp/repo/hosted
  npm ci
  npm test
'
```

Result: 113 passed, 1 failed out of 114. Every XIA-638 hosted test passed, including source snapshotting, final revision/status guards, delayed-push suppression, and generic notification coverage. Installing Python removed the earlier `spawnSync python ENOENT`; the Python-compatible Ed25519 test passed.

The sole failure was the unchanged baseline test `tests/chat-stream.test.mjs` → `cancelling the browser stream promptly releases its lease without storing partial chat`, which observed lease count `1` instead of `0`. It reproduced both alone (5/6 passed) and with exact Node 24.15.0 mounted into the bookworm container, so it is not an XIA-638 regression or a Node 24.22.2 tag drift. Neither that test nor the chat-stream implementation is changed by this worktree.

## Other known baseline result

- The earlier full `PYTHONPATH=. /tmp/xia-638-py311/bin/pytest -q` result remains 302 passed, 9 skipped, 2 failed. Both failures reproduce in `tests/test_updater.py` alone and are unrelated Linux/Windows updater assumptions: `test_probe_rejects_executable_with_wrong_embedded_version` uses a fake process without `poll()` on Linux, and `test_restart_waits_for_real_ready_ack_and_kills_only_owned_tree_on_failure` calls a Windows-only restart path on Linux. The XIA-638 focused Python suite passes.

No host libc upgrade, live credential use, notification send, migration, release, deployment, commit, staging, or push was performed.
