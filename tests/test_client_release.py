"""Both-platform publication gate: real local files, mocked GitHub CLI boundary."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from scripts.publish_clients import ReleaseBundle, ReleaseError, prepare_release, publish_bundle, verify_target_source
from scripts import publish_clients


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def built(tmp_path):
    windows, android, source = (tmp_path / n for n in ("windows", "android", "source"))
    windows.mkdir()
    android.mkdir()
    (source / "mobile/lib").mkdir(parents=True)
    (source / "mobile/lib/main.dart").write_text("void main() {}", encoding="utf-8")
    (windows / "Xiaoban-Windows.exe").write_bytes(b"MZvalidated Windows binary")
    with zipfile.ZipFile(windows / "Xiaoban-Windows-portable.zip", "w") as archive:
        archive.write(windows / "Xiaoban-Windows.exe", "Xiaoban-Windows.exe")
        archive.writestr("README.txt", "how to run")
        archive.writestr("LICENSES.txt", "third party licenses")
    common = {"status": "passed", "frozen": True, "server_stopped": True, "update_version": "0.4.1"}
    for name in ("packaged-smoke.json", "ui-smoke.json", "ui-restart-smoke.json"):
        (windows / name).write_text(json.dumps(common | {"restored_login": True}), encoding="utf-8")
    apk = android / "Xiaoban-Android-debug.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"binary manifest")
        archive.writestr("classes.dex", b"dex\n035\x00example classes")
    report = {"schema": 1, "status": "passed", "platform": "android", "version": "0.4.1",
              "version_code": 2, "build_type": "debug", "package_name": "com.changban.companion_mobile",
              "previous_version_code": 1,
              "sha256": sha(apk), "size": apk.stat().st_size, "signature_verified": True,
              "certificate_sha256": "a" * 64, "min_sdk": 29, "target_sdk": 36,
              "default_url": "https://xiaoban-companion.abloom-haven-3343.chatgpt.site",
              "source_commit": "b" * 40,
              "source_inputs": {"mobile/lib/main.dart": sha(source / "mobile/lib/main.dart")},
              "checks": ["flutter-analyze", "flutter-test", "apk-signature", "apk-package", "apk-default-url", "debug-certificate-continuity", "android-version-code-increment"]}
    (android / "build-report.json").write_text(json.dumps(report), encoding="utf-8")
    return windows, android, source, tmp_path / "prepared"


def prepare(built):
    windows, android, source, output = built
    return prepare_release("0.4.1", windows, android, output, source_root=source,
        inspector=lambda path: {"version": "0.4.1", "version_code": 2,
            "package_name": "com.changban.companion_mobile", "min_sdk": 29, "target_sdk": 36,
            "certificate_sha256": "a" * 64, "signature_verified": True},
        windows_inspector=lambda path: "0.4.1", input_collector=fixture_inputs)


def fixture_inputs(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in (root / "mobile").rglob("*") if p.is_file()}


def edit_report(built, change):
    path = built[1] / "build-report.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(change)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_prepares_both_platforms_and_deterministic_new_checksum_without_touching_binaries(built):
    bundle = prepare(built)
    names = [p.name for p in bundle.assets]
    assert names == ["Xiaoban-Windows.exe", "Xiaoban-Windows-portable.zip", "Xiaoban-Android-debug.apk",
                     "SHA256SUMS-clients-v0.4.1.txt"]
    before = {p.name: sha(p) for p in bundle.assets}
    assert (built[3] / names[-1]).read_text(encoding="utf-8") == "".join(
        f"{sha(p)}  {p.name}\n" for p in sorted(bundle.assets[:-1], key=lambda p: p.name))
    assert {p.name: sha(p) for p in prepare(built).assets} == before


@pytest.mark.parametrize("missing", ["apk", "windows", "report"])
def test_missing_either_platform_stops_before_any_publish(built, missing):
    paths = {"apk": built[1] / "Xiaoban-Android-debug.apk",
             "windows": built[0] / "Xiaoban-Windows.exe", "report": built[1] / "build-report.json"}
    paths[missing].unlink()
    with pytest.raises(ReleaseError):
        prepare(built)


@pytest.mark.parametrize("change", [{"version": "0.1.0"}, {"version_code": 0},
    {"previous_version_code": 2}, {"previous_version_code": 0},
    {"signature_verified": False}, {"size": 1}, {"sha256": "0" * 64},
    {"certificate_sha256": "invalid"}, {"checks": ["flutter-test"]},
    {"default_url": "http://10.0.2.2:8765"}, {"source_inputs": {"../outside": "a" * 64}}])
def test_rejects_wrong_android_version_unverified_or_tampered_package(built, change):
    edit_report(built, change)
    with pytest.raises(ReleaseError):
        prepare(built)


def test_rejects_apk_bytes_changed_since_verification(built):
    with (built[1] / "Xiaoban-Android-debug.apk").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ReleaseError, match="Android"):
        prepare(built)


def test_rejects_windows_zip_with_other_executable(built):
    with zipfile.ZipFile(built[0] / "Xiaoban-Windows-portable.zip", "w") as archive:
        archive.writestr("Xiaoban-Windows.exe", b"MZother program")
        archive.writestr("README.txt", "readme")
        archive.writestr("LICENSES.txt", "licenses")
    with pytest.raises(ReleaseError, match="Windows"):
        prepare(built)


def test_rejects_changed_production_source_after_android_build(built):
    (built[2] / "mobile/lib/main.dart").write_text("new code", encoding="utf-8")
    with pytest.raises(ReleaseError, match="source"):
        prepare(built)


def remote_asset(path, **extra):
    return {"name": path.name, "digest": "sha256:" + sha(path), "size": path.stat().st_size,
            "state": "uploaded"} | extra


def source_check(bundle, revision):
    assert revision == "b" * 40


def test_publisher_uploads_missing_android_and_checksum_but_never_replaces_windows(built):
    bundle = prepare(built)
    assets = [remote_asset(p) for p in bundle.assets[:2]]
    commands = []

    def gh(argv, **kwargs):
        commands.append(argv)
        if argv[1] == "api":
            if "/commits/" in argv[2]:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"sha": "b" * 40}), "")
            return subprocess.CompletedProcess(argv, 0, json.dumps({"assets": assets}), "")
        assert argv[:4] == ["gh", "release", "upload", "v0.4.1"]
        assert "--clobber" not in argv
        assert not any(str(p) in argv for p in bundle.assets[:2])
        for path in bundle.assets[2:]:
            assert str(path) in argv
            assets.append(remote_asset(path))
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = publish_bundle(bundle, "owner/repo", runner=gh, source_validator=source_check)
    assert result["uploaded"] == [p.name for p in bundle.assets[2:]]
    assert len(commands) == 4


def test_conflicting_remote_asset_rejects_before_any_upload(built):
    bundle = prepare(built)
    calls = []
    def gh(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"assets": [remote_asset(bundle.assets[0], digest="sha256:" + "0" * 64)]}), "")
    with pytest.raises(ReleaseError, match="immutable"):
        publish_bundle(bundle, "owner/repo", runner=gh, source_validator=source_check)
    assert len(calls) == 1 and calls[0][1] == "api"


def test_exact_retry_is_read_only(built):
    bundle = prepare(built)
    calls = []
    def gh(argv, **kwargs):
        calls.append(argv)
        if "/commits/" in argv[2]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sha": "b" * 40}), "")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"assets": [remote_asset(p) for p in bundle.assets]}), "")
    result = publish_bundle(bundle, "owner/repo", runner=gh, source_validator=source_check)
    assert result["uploaded"] == []
    assert len(calls) == 2


def test_sdk_metadata_must_match_even_when_report_claims_success(built):
    with pytest.raises(ReleaseError, match="SDK inspection differs"):
        prepare_release("0.4.1", *built[:2], built[3], source_root=built[2],
            windows_inspector=lambda path: "0.4.1", inspector=lambda path: {"version": "0.1.0"},
            input_collector=fixture_inputs)


def test_actual_windows_file_version_must_match_report(built):
    with pytest.raises(ReleaseError, match="Windows file version"):
        prepare_release("0.4.1", *built[:2], built[3], source_root=built[2],
            windows_inspector=lambda path: "0.1.0")


def test_wrong_target_tag_production_source_stops_before_upload(built):
    bundle = prepare(built)
    calls = []
    def gh(argv, **kwargs):
        calls.append(argv)
        value = {"sha": "b" * 40} if "/commits/" in argv[2] else {"assets": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
    def reject(bundle, revision):
        raise ReleaseError("Android production inputs differ from target tag")
    with pytest.raises(ReleaseError, match="target tag"):
        publish_bundle(bundle, "owner/repo", runner=gh, source_validator=reject)
    assert all(argv[1] == "api" for argv in calls)


def test_real_git_tag_source_check_handles_crlf_and_rejects_new_files(built):
    source = built[2]
    code = source / "mobile/lib/main.dart"
    code.write_bytes(b"void main() {}\r\n")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], stderr=subprocess.DEVNULL).decode().strip()
    git("init")
    git("config", "core.autocrlf", "input")
    git("add", "mobile")
    git("-c", "user.name=Release Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    revision = git("rev-parse", "HEAD")
    bundle = ReleaseBundle("0.4.1", (), {}, source, {"mobile/lib/main.dart": sha(code)})
    verify_target_source(bundle, revision)
    extra = source / "mobile/lib/new.dart"
    extra.write_text("changed", encoding="utf-8")
    wrong = ReleaseBundle("0.4.1", (), {}, source, {**bundle.source_inputs, "mobile/lib/new.dart": sha(extra)})
    with pytest.raises(ReleaseError, match="file list differs"):
        verify_target_source(wrong, revision)
    code.write_text("different code", encoding="utf-8")
    with pytest.raises(ReleaseError, match="source differs"):
        verify_target_source(bundle, revision)


def test_cli_dry_run_prepares_both_clients_without_github(built, monkeypatch, capsys):
    monkeypatch.setattr(publish_clients, "prepare_release", lambda *args, **kwargs: prepare(built))
    monkeypatch.setattr(publish_clients, "publish_bundle", lambda *args, **kwargs: pytest.fail("Dry run contacted GitHub"))
    assert publish_clients.main(["--version", "0.4.1", "--dry-run"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["dry_run"] and len(value["assets"]) == 4


def test_omitting_new_production_input_invalidates_android_report(built):
    extra = built[2] / "mobile/lib/new.dart"
    extra.write_text("void added() {}", encoding="utf-8")
    with pytest.raises(ReleaseError, match="fingerprint list"):
        prepare(built)


def test_changing_prepared_file_never_reaches_github(built):
    bundle = prepare(built)
    with bundle.assets[-1].open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ReleaseError, match="Prepared release files changed"):
        publish_bundle(bundle, "owner/repo", runner=lambda *args, **kwargs: pytest.fail("Changed file reached GitHub"))
