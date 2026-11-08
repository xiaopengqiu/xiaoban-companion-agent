# Windows and Android client release plan

**Goal:** Backfill the current GitHub Release with a verified Android APK and enforce Windows/Android parity for subsequent client releases.

**Architecture:** Keep the existing0.4.1 tag, Windows assets, source archive, signing key and update feed immutable. Build Android from the same mobile production inputs as the tag with explicit Flutter version0.4.1/build2 and the existing cloud URL. Add local build evidence and one unified GitHub upload gate that requires both platforms before any mutation. Save the user requirement in repository instructions. No CI subscription, new signing identity or Android auto-updater is introduced.

**Tech Stack:** Flutter/Dart/Android SDK (aapt/apksigner), Python release validator with GitHub CLI, existing Windows packaging and Ed25519 feed.

## Android build

1. Test metadata/signature/manifest report validation and the release build wrapper before implementation.
2. Add reproducible Android build command with named version, incrementing versionCode, HTTPS cloud URL, production source fingerprints and strict Android metadata/signature verification.
3. Run existing Flutter tests and analysis, build the real APK with the preserved debug key, and inspect its actual version, package, signature and default URL.
4. Keep clear debug status and avoid claiming unperformed device validation.

## Unified release gate

1. Write failing cases for missing Android/Windows assets, hash/version mismatch and immutable remote conflicts.
2. Verify Windows reports/ZIP and Android report/APK before preparing a separate clients checksum file.
3. Read the complete remote asset list before uploading. Reject any different same-named file; upload only missing assets without clobber. Verify remote digests, allow idempotent retries, and expose a dry run.
4. Document draft creation, the two-client publish gate and separate Windows feed publication.

## Root acceptance and delivery

1. Review scripts and proof; independently verify APK package/version/signature/size against source/report and compare mobile production inputs with tag0.4.1.
2. Use the unified gate to append Android APK and clients checksum to the existing Release, preserving its original four assets. Update its body with Android scope and debug status.
3. Commit/push source scripts, documentation and repository instructions. Verify remote asset hashes, unchanged existing assets and clean Git state before reporting completion.
