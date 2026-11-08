"""Validate and publish Windows plus Android clients as one GitHub release bundle."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
CHECKS = {"flutter-analyze", "flutter-test", "apk-signature", "apk-package", "apk-default-url",
          "debug-certificate-continuity", "android-version-code-increment"}


class ReleaseError(ValueError):
    pass


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def regular(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ReleaseError(f"Required release file is missing or not regular: {path.name}")
    return path.resolve()


def report(path: Path) -> dict:
    regular(path)
    if path.stat().st_size > 1024 * 1024:
        raise ReleaseError(f"Oversized verification report: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"Invalid verification report: {path.name}") from exc
    if not isinstance(data, dict):
        raise ReleaseError(f"Invalid verification report: {path.name}")
    return data


def inspect_android(apk: Path) -> dict:
    # The same read-only SDK inspection is used by the build and publication gate.
    # Import works both as `python scripts/publish_clients.py` and as a module.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.android_build_report import inspect_apk
    return inspect_apk(apk)


def inspect_windows_version(exe: Path) -> str:
    # PyInstaller depends on pefile in the documented desktop build environment.
    import pefile
    try:
        with pefile.PE(str(exe), fast_load=True) as image:
            image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
            info = image.VS_FIXEDFILEINFO[0]
            if info.FileVersionLS & 0xffff:
                raise ValueError("Unexpected Windows revision component")
            return f"{info.FileVersionMS >> 16}.{info.FileVersionMS & 0xffff}.{info.FileVersionLS >> 16}"
    except pefile.PEFormatError as exc:
        raise ValueError("Invalid Windows PE executable") from exc


def collect_android_inputs(root: Path) -> dict:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.android_build_report import production_inputs
    return production_inputs(root)


@dataclass(frozen=True)
class ReleaseBundle:
    version: str
    assets: tuple[Path, ...]
    hashes: dict[str, str]
    source_root: Path
    source_inputs: dict[str, str]


def prepare_release(version: str, windows_dir: Path, android_dir: Path, output_dir: Path,
                    *, source_root: Path = ROOT, inspector=inspect_android,
                    windows_inspector=inspect_windows_version,
                    input_collector=collect_android_inputs) -> ReleaseBundle:
    """Read real files and SDK metadata before allowing any GitHub operation."""
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ReleaseError("Version must use three integers, for example 0.4.1")
    windows_dir, android_dir = Path(windows_dir), Path(android_dir)
    exe = regular(windows_dir / "Xiaoban-Windows.exe")
    portable = regular(windows_dir / "Xiaoban-Windows-portable.zip")
    apk = regular(android_dir / "Xiaoban-Android-debug.apk")
    with exe.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ReleaseError("Windows executable format is invalid")
    try:
        if windows_inspector(exe) != version:
            raise ReleaseError("Windows file version differs from requested release")
    except (ValueError, OSError, AttributeError, ImportError) as exc:
        raise ReleaseError("Windows file version differs or could not be inspected; use the desktop build environment") from exc
    for name in ("packaged-smoke.json", "ui-smoke.json", "ui-restart-smoke.json"):
        data = report(windows_dir / name)
        if (data.get("status") != "passed" or data.get("frozen") is not True
                or data.get("server_stopped") is not True or data.get("update_version") != version):
            raise ReleaseError(f"Windows verification failed or version differs: {name}")
        if name == "ui-restart-smoke.json" and data.get("restored_login") is not True:
            raise ReleaseError("Windows restart login verification is missing")
    try:
        with zipfile.ZipFile(portable) as archive:
            if (sorted(archive.namelist()) != ["LICENSES.txt", "README.txt", "Xiaoban-Windows.exe"]
                    or archive.getinfo("Xiaoban-Windows.exe").file_size != exe.stat().st_size):
                raise ReleaseError("Windows portable ZIP contents are invalid")
            content = hashlib.sha256()
            with archive.open("Xiaoban-Windows.exe") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(chunk)
            if content.hexdigest() != digest(exe):
                raise ReleaseError("Windows portable ZIP contains a different executable")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ReleaseError("Windows portable ZIP is invalid") from exc
    data = report(android_dir / "build-report.json")
    if (type(data.get("schema")) is not int or data.get("schema") != 1
            or data.get("status") != "passed" or data.get("platform") != "android"
            or data.get("version") != version or type(data.get("version_code")) is not int
            or not 1 <= data["version_code"] <= 2100000000 or data.get("build_type") != "debug"
            or type(data.get("previous_version_code")) is not int
            or not 1 <= data["previous_version_code"] < data["version_code"]
            or data.get("package_name") != "com.changban.companion_mobile"
            or data.get("signature_verified") is not True
            or not isinstance(data.get("certificate_sha256"), str)
            or not HASH.fullmatch(data["certificate_sha256"])
            or type(data.get("min_sdk")) is not int or data["min_sdk"] < 29
            or type(data.get("target_sdk")) is not int or data["target_sdk"] < data["min_sdk"]
            or data.get("default_url") != "https://xiaoban-companion.abloom-haven-3343.chatgpt.site"
            or not isinstance(data.get("source_commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", data["source_commit"])
            or not isinstance(data.get("checks"), list) or not all(isinstance(c, str) for c in data["checks"])
            or not CHECKS <= set(data["checks"])):
        raise ReleaseError("Android build report is incomplete or has the wrong version")
    apk_sha = digest(apk)
    if type(data.get("size")) is not int or data["size"] != apk.stat().st_size or data.get("sha256") != apk_sha:
        raise ReleaseError("Android APK changed after verification")
    inputs = data.get("source_inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise ReleaseError("Android production source fingerprints are missing")
    for name, expected in inputs.items():
        if not isinstance(name, str):
            raise ReleaseError("Invalid Android production source fingerprint")
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                or relative.parts[0] != "mobile" or not isinstance(expected, str) or not HASH.fullmatch(expected)):
            raise ReleaseError("Invalid Android production source fingerprint")
        path = regular(Path(source_root) / relative)
        if digest(path) != expected.lower():
            raise ReleaseError(f"Android source changed after build: {name}")
    if input_collector(Path(source_root)) != inputs:
        raise ReleaseError("Android production source fingerprint list is incomplete or stale")
    try:
        with zipfile.ZipFile(apk) as archive:
            with archive.open("classes.dex") as classes:
                dex_header = classes.read(4)
            if archive.getinfo("AndroidManifest.xml").file_size == 0 or dex_header != b"dex\n":
                raise ReleaseError("Android APK structure is invalid")
        actual = inspector(apk)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ReleaseError("Android APK SDK signature/package inspection failed") from exc
    for key in ("version", "version_code", "package_name", "min_sdk", "target_sdk", "certificate_sha256", "signature_verified"):
        if actual.get(key) != data[key]:
            raise ReleaseError(f"Android APK SDK inspection differs from build report: {key}")
    if digest(apk) != apk_sha:
        raise ReleaseError("Android APK changed during SDK inspection")
    assets = (exe, portable, apk)
    hashes = {p.name: digest(p) for p in assets}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checksum = output_dir / f"SHA256SUMS-clients-v{version}.txt"
    if checksum.is_symlink():
        raise ReleaseError("Checksum destination must not be a link")
    checksum.write_text("".join(f"{hashes[p.name]}  {p.name}\n" for p in sorted(assets, key=lambda p: p.name)),
                        encoding="utf-8", newline="\n")
    hashes[checksum.name] = digest(checksum)
    return ReleaseBundle(version, assets + (checksum.resolve(),), hashes, Path(source_root).resolve(), dict(inputs))


def verify_target_source(bundle: ReleaseBundle, revision: str) -> None:
    """Match actual build inputs to the release tag; docs-only commits may differ."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ReleaseError("Invalid target release revision")
    tree = subprocess.run(["git", "-C", str(bundle.source_root), "ls-tree", "-rz", "--full-tree", revision,
                           "--", "mobile/lib", "mobile/android", "mobile/pubspec.yaml", "mobile/pubspec.lock"],
                          capture_output=True, timeout=30)
    if tree.returncode:
        raise ReleaseError("Target tag commit is missing locally; fetch it before publishing")
    target = {}
    for row in filter(None, tree.stdout.split(b"\0")):
        metadata, name = row.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise ReleaseError("Unsupported production source type in target tag")
        target[name.decode("utf-8")] = oid.decode("ascii")
    if set(target) != set(bundle.source_inputs):
        raise ReleaseError("Android production file list differs from target tag")
    for name, expected in bundle.source_inputs.items():
        path = regular(bundle.source_root / name)
        # First verify the exact built bytes, then compare Git clean-filtered
        # objects. This supports CRLF checkouts with core.autocrlf=input/true.
        if digest(path) != expected.lower():
            raise ReleaseError(f"Android production source differs from target tag: {name}")
        result = subprocess.run(["git", "-C", str(bundle.source_root), "hash-object", f"--path={name}", str(path)],
                                capture_output=True, timeout=30)
        if result.returncode or result.stdout.decode("ascii").strip() != target[name]:
            raise ReleaseError(f"Android production source differs from target tag: {name}")


def publish_bundle(bundle: ReleaseBundle, repository: str, *, runner=subprocess.run,
                   source_validator=verify_target_source) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReleaseError("Repository must be owner/name")

    def unchanged():
        if any(digest(regular(p)) != bundle.hashes[p.name] for p in bundle.assets):
            raise ReleaseError("Prepared release files changed; run validation again")

    def gh(arguments, *, allow_not_found=False):
        try:
            result = runner(["gh", *arguments], capture_output=True, text=True, encoding="utf-8", timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseError("GitHub CLI unavailable or timed out; inspect release before retrying") from exc
        if result.returncode:
            if allow_not_found:
                try:
                    error = json.loads(result.stdout)
                except (ValueError, TypeError):
                    error = None
                if ((isinstance(error, dict) and str(error.get("status")) == "404")
                        or re.search(r"\bHTTP\s+404\b", result.stderr or "")):
                    return None
            raise ReleaseError("GitHub CLI failed; check repository, existing release and gh authentication")
        return result.stdout

    def remote():
        try:
            tag = f"v{bundle.version}"
            raw = gh(["api", f"repos/{repository}/releases/tags/{tag}"], allow_not_found=True)
            release = None if raw is None else json.loads(raw)
            # Drafts can be visible in the authenticated list while the by-tag
            # endpoint returns 404. Never guess from a partial or first-page list.
            if release is None or (isinstance(release, dict) and "assets" not in release
                    and str(release.get("status")) == "404"):
                pages = json.loads(gh(["api", f"repos/{repository}/releases?per_page=100",
                                      "--paginate", "--slurp"]))
                if (not isinstance(pages, list) or any(not isinstance(page, list) for page in pages)
                        or any(not isinstance(item, dict) for page in pages for item in page)):
                    raise ValueError()
                matches = [item for page in pages for item in page if item.get("tag_name") == tag]
                if len(matches) != 1:
                    raise ReleaseError("GitHub target release was not found uniquely")
                release = matches[0]
                if type(release.get("id")) is not int or release["id"] <= 0:
                    raise ValueError()
            if not isinstance(release, dict) or release.get("tag_name", tag) != tag:
                raise ValueError()
            assets = release["assets"]
            if not isinstance(assets, list):
                raise ValueError()
            indexed = {a["name"]: a for a in assets}
            if len(indexed) != len(assets):
                raise ValueError()
            return indexed
        except ReleaseError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise ReleaseError("GitHub release metadata is invalid") from exc

    def verify(existing):
        for path in bundle.assets:
            asset = existing.get(path.name)
            if asset and (asset.get("digest") != "sha256:" + bundle.hashes[path.name]
                          or asset.get("size") != path.stat().st_size or asset.get("state") != "uploaded"):
                raise ReleaseError(f"Release assets are immutable; conflicting file: {path.name}")

    unchanged()
    existing = remote()
    verify(existing)  # Check every existing name before uploading any missing file.
    try:
        revision = json.loads(gh(["api", f"repos/{repository}/commits/v{bundle.version}"]))["sha"]
    except (ValueError, TypeError, KeyError) as exc:
        raise ReleaseError("Target release tag does not resolve to a commit") from exc
    source_validator(bundle, revision)
    missing = [path for path in bundle.assets if path.name not in existing]
    if missing:
        unchanged()
        gh(["release", "upload", f"v{bundle.version}", "--repo", repository, *map(str, missing)])
        final = remote()
        verify(final)
        if any(path.name not in final for path in bundle.assets):
            raise ReleaseError("Release upload incomplete; rerun to safely resume")
    return {"release_url": f"https://github.com/{repository}/releases/tag/v{bundle.version}",
            "uploaded": [p.name for p in missing],
            "already_present": [p.name for p in bundle.assets if p.name in existing]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo", default="xiaopengqiu/xiaoban-companion-agent")
    parser.add_argument("--windows-dir", type=Path)
    parser.add_argument("--android-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Run all local/SDK checks, do not contact GitHub")
    args = parser.parse_args(argv)
    try:
        bundle = prepare_release(args.version,
            args.windows_dir or ROOT / "desktop/dist" / args.version,
            args.android_dir or ROOT / "artifacts/android" / args.version,
            args.output_dir or ROOT / "artifacts/releases" / args.version)
        result = ({"dry_run": True, "assets": [{"name": p.name, "size": p.stat().st_size,
                  "sha256": bundle.hashes[p.name]} for p in bundle.assets]}
                  if args.dry_run else publish_bundle(bundle, args.repo))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ReleaseError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Client release stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
