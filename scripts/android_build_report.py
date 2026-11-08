"""Read-only Android artifact verification used by build and release commands."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'com.changban.companion_mobile'


def production_inputs(root: Path = ROOT) -> dict[str, str]:
    names = subprocess.check_output([
        'git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', 'mobile/lib', 'mobile/android',
        'mobile/pubspec.yaml', 'mobile/pubspec.lock'], cwd=root).decode('utf-8').split('\0')
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sorted(filter(None, names))}


def parse_metadata(badging: str, signer: str) -> dict:
    package = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", badging, re.M)
    minimum = re.search(r"^sdkVersion:'([0-9]+)'", badging, re.M)
    target = re.search(r"^targetSdkVersion:'([0-9]+)'", badging, re.M)
    if not package or not minimum or not target:
        raise ValueError('APK package metadata is incomplete')
    if (not re.search(r'^Verifies\s*$', signer, re.M)
            or not re.search(r'^Verified using v2 scheme .*: true\s*$', signer, re.M)
            or not re.search(r'^Number of signers: 1\s*$', signer, re.M)):
        raise ValueError('APK v2 signature verification did not succeed')
    certs = re.findall(r'^Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]{64})\s*$', signer, re.M)
    if len(certs) != 1:
        raise ValueError('APK signing certificate is missing or ambiguous')
    return {'package_name': package[1], 'version_code': int(package[2]), 'version': package[3],
            'min_sdk': int(minimum[1]), 'target_sdk': int(target[1]),
            'certificate_sha256': certs[0].lower(), 'signature_verified': True}


def inspect_apk(apk: Path, aapt: Path | None = None, apksigner: Path | None = None) -> dict:
    """Run actual Android tools without building or changing any project files."""
    apk = Path(apk).resolve(strict=True)
    sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
               or Path(os.environ.get('LOCALAPPDATA', '')) / 'CodexTools/AndroidSdk')
    if aapt is None or apksigner is None:
        directories = sorted((sdk / 'build-tools').glob('*'),
                             key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.name)), reverse=True)
        tools = next((p for p in directories if (p / 'aapt.exe').is_file()
                      and (p / 'lib/apksigner.jar').is_file()), None)
        if tools is None:
            raise ValueError('Android build-tools with aapt and apksigner are required')
        aapt = aapt or tools / 'aapt.exe'
        apksigner = apksigner or tools / 'lib/apksigner.jar'
    apksigner = Path(apksigner)
    if apksigner.suffix.lower() == '.bat':
        apksigner = apksigner.parent / 'lib/apksigner.jar'
    java = Path(os.environ['JAVA_HOME']) / 'bin/java.exe' if os.environ.get('JAVA_HOME') else None
    if java is None or not java.is_file():
        found = shutil.which('java')
        if not found:
            fallback = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Java/jdk-21/bin/java.exe'
            found = str(fallback) if fallback.is_file() else None
        if not found:
            raise ValueError('Java is required to verify the APK signature')
        java = Path(found)
    try:
        # aapt's Windows argv decoder rejects a Unicode absolute filename. The
        # subprocess cwd uses Win32 Unicode APIs; the published ASCII asset name
        # works without a drive mapping, copying the APK or relying on 8.3 names.
        badging = subprocess.run([str(aapt), 'dump', 'badging', apk.name], cwd=apk.parent, check=True,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60).stdout
        signer = subprocess.run([str(java), '-jar', str(apksigner), 'verify', '--verbose',
                                 '--print-certs', apk.name], cwd=apk.parent, check=True, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=60).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Android package or signature tool rejected this APK') from exc
    return {**parse_metadata(badging, signer), 'badging': badging, 'signer': signer}


def verify_apk_report(*, apk: Path, badging: str, signer: str, version: str,
                      version_code: int, default_url: str, source_commit: str,
                      source_inputs: dict[str, str], previous_signer: str | None = None,
                      previous_version_code: int | None = None,
                      verified_checks: tuple[str, ...] = ()) -> dict:
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        raise ValueError('Invalid application version')
    if type(version_code) is not int or not 1 <= version_code <= 2100000000:
        raise ValueError('Android version code must be 1..2100000000')
    if previous_version_code is not None and (type(previous_version_code) is not int or version_code <= previous_version_code):
        raise ValueError('Android version code must increase over the previous APK')
    url = urlsplit(default_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Android default URL must be an absolute HTTPS URL')
    value = parse_metadata(badging, signer)
    expected = {'version': version, 'version_code': version_code, 'package_name': PACKAGE, 'min_sdk': 29}
    if any(value[key] != expected_value for key, expected_value in expected.items()) or value['target_sdk'] < 29:
        raise ValueError('APK package, version, code or SDK metadata does not match the requested build')
    if previous_signer is not None:
        previous = parse_metadata(badging, previous_signer)
        if previous['certificate_sha256'] != value['certificate_sha256']:
            raise ValueError('APK signing certificate changed; existing installations cannot upgrade')
    with zipfile.ZipFile(apk) as archive:
        if not {'AndroidManifest.xml', 'classes.dex', 'assets/flutter_assets/kernel_blob.bin'} <= set(archive.namelist()):
            raise ValueError('APK is missing expected Android/Flutter debug content')
        if default_url.encode('utf-8') not in archive.read('assets/flutter_assets/kernel_blob.bin'):
            raise ValueError('APK kernel does not contain the configured default URL')
    raw_digest = hashlib.sha256()
    with Path(apk).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            raw_digest.update(chunk)
    if not set(verified_checks) <= {'flutter-analyze', 'flutter-test'}:
        raise ValueError('Unknown externally verified build check')
    checks = [*verified_checks, 'apk-signature', 'apk-package', 'apk-default-url']
    if previous_signer is not None:
        checks.append('debug-certificate-continuity')
    if previous_version_code is not None:
        checks.append('android-version-code-increment')
    return {'schema': 1, 'status': 'passed', 'platform': 'android', 'build_type': 'debug', **value,
            'apk_path': str(Path(apk).resolve()), 'sha256': raw_digest.hexdigest(),
            'size': Path(apk).stat().st_size, 'default_url': default_url, 'source_commit': source_commit,
            'source_inputs': source_inputs, 'previous_version_code': previous_version_code, 'checks': checks,
            'built_at': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--apk', type=Path)
    parser.add_argument('--version')
    parser.add_argument('--version-code', type=int)
    parser.add_argument('--default-url')
    parser.add_argument('--source-commit')
    parser.add_argument('--before-inputs', type=Path)
    parser.add_argument('--previous-apk', type=Path)
    parser.add_argument('--push-client-config', type=Path)
    parser.add_argument('--verify-native-push', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--flutter-analyze-passed', action='store_true')
    parser.add_argument('--flutter-test-passed', action='store_true')
    parser.add_argument('--validate-previous-version-code', action='store_true')
    args = parser.parse_args()
    if args.snapshot:
        args.snapshot.write_text(json.dumps(production_inputs(), sort_keys=True, indent=2) + '\n', encoding='utf-8')
        return
    if args.validate_previous_version_code:
        previous = inspect_apk(args.previous_apk)
        if args.version_code <= previous['version_code']:
            raise ValueError('Android version code must increase over the previous APK')
        return
    before = json.loads(args.before_inputs.read_text(encoding='utf-8'))
    after = production_inputs()
    if before != after:
        raise ValueError('Production mobile inputs changed while building; rebuild the APK')
    inspected = inspect_apk(args.apk)
    previous = inspect_apk(args.previous_apk) if args.previous_apk else None
    report = verify_apk_report(apk=args.apk, badging=inspected['badging'], signer=inspected['signer'],
                               version=args.version, version_code=args.version_code, default_url=args.default_url,
                               source_commit=args.source_commit, source_inputs=after,
                               previous_signer=previous['signer'] if previous else None,
                               previous_version_code=previous['version_code'] if previous else None,
                               verified_checks=tuple(name for name, passed in [
                                   ('flutter-analyze', args.flutter_analyze_passed),
                                   ('flutter-test', args.flutter_test_passed)] if passed))
    if args.verify_native_push:
        from android_push_config import inspect_push_apk, load_config
        report['native_push'] = inspect_push_apk(args.apk, load_config(args.push_client_config))
        report['checks'].append('native-push-manifest-and-sdk')
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('version', 'version_code', 'size', 'sha256', 'certificate_sha256')}))


if __name__ == '__main__':
    main()
