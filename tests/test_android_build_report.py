import hashlib
from pathlib import Path
import zipfile
import shutil
import subprocess
import os

import pytest

from scripts.android_build_report import verify_apk_report
from scripts import android_build_report

URL = 'https://xiaoban-companion.abloom-haven-3343.chatgpt.site'
CERT = 'a' * 64
BADGING = "package: name='com.changban.companion_mobile' versionCode='2' versionName='0.4.1'\nsdkVersion:'29'\ntargetSdkVersion:'36'\n"
SIGNER = f'Verifies\nVerified using v2 scheme (APK Signature Scheme v2): true\nNumber of signers: 1\nSigner #1 certificate SHA-256 digest: {CERT}\n'


def apk(tmp_path, *, cloud=True):
    path = tmp_path / 'client.apk'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('AndroidManifest.xml', b'manifest')
        archive.writestr('classes.dex', b'dex')
        archive.writestr('assets/flutter_assets/kernel_blob.bin',
                         b'dart kernel: ' + (URL.encode() if cloud else b'http://10.0.2.2:8765'))
    return path


def report(path, **changes):
    args = dict(apk=path, badging=BADGING, signer=SIGNER, previous_signer=SIGNER,
                version='0.4.1', version_code=2, default_url=URL,
                source_commit='1' * 40, source_inputs={'mobile/lib/main.dart': 'b' * 64})
    args.update(changes)
    return verify_apk_report(**args)


def test_report_binds_actual_apk_metadata_signature_default_url_and_inputs(tmp_path):
    path = apk(tmp_path)
    value = report(path)
    assert value['status'] == 'passed' and value['platform'] == 'android'
    assert value['version'] == '0.4.1' and value['version_code'] == 2
    assert value['signature_verified'] is True and value['certificate_sha256'] == CERT
    assert value['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert value['size'] == path.stat().st_size
    assert value['default_url'] == URL
    assert value['source_inputs'] == {'mobile/lib/main.dart': 'b' * 64}
    assert 'flutter-test' not in value['checks']
    assert 'flutter-analyze' not in value['checks']


@pytest.mark.parametrize('badging', [
    BADGING.replace("versionName='0.4.1'", "versionName='0.1.0'"),
    BADGING.replace("versionCode='2'", "versionCode='1'"),
    BADGING.replace('com.changban.companion_mobile', 'com.other.app'),
    BADGING.replace("sdkVersion:'29'", "sdkVersion:'23'"),
])
def test_mismatched_package_or_version_cannot_be_published(tmp_path, badging):
    with pytest.raises(ValueError):
        report(apk(tmp_path), badging=badging)


def test_changed_debug_key_cannot_break_existing_install_upgrade(tmp_path):
    with pytest.raises(ValueError, match='certificate'):
        report(apk(tmp_path), signer=SIGNER.replace(CERT, 'c' * 64))


def test_signature_claim_requires_actual_v2_verification_output(tmp_path):
    with pytest.raises(ValueError, match='signature'):
        report(apk(tmp_path), signer=SIGNER.replace(': true', ': false'))


def test_emulator_default_cannot_be_reported_as_cloud_default(tmp_path):
    with pytest.raises(ValueError, match='default URL'):
        report(apk(tmp_path, cloud=False))


@pytest.mark.parametrize('previous_code', [2, 3])
def test_android_version_code_must_increase_over_previous_apk(tmp_path, previous_code):
    with pytest.raises(ValueError, match='version code'):
        report(apk(tmp_path), previous_version_code=previous_code)


def test_actual_tool_arguments_avoid_aapt_unicode_absolute_path_bug(tmp_path, monkeypatch):
    folder = tmp_path / '中文目录'
    folder.mkdir()
    path = apk(folder)
    java_home = tmp_path / 'jdk'
    (java_home / 'bin').mkdir(parents=True)
    (java_home / 'bin/java.exe').write_bytes(b'fake executable tool boundary')
    monkeypatch.setenv('JAVA_HOME', str(java_home))
    calls = []
    def tool(arguments, **kwargs):
        assert arguments[-1] == path.name
        assert kwargs['cwd'] == path.parent
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, SIGNER if '-jar' in arguments else BADGING)
    monkeypatch.setattr(android_build_report.subprocess, 'run', tool)
    value = android_build_report.inspect_apk(path, tmp_path / 'aapt.exe', tmp_path / 'apksigner.jar')
    assert len(calls) == 2 and value['version_code'] == 2


@pytest.mark.parametrize('arguments,message', [
    (['-AppVersion', 'bad'], 'AppVersion'),
    (['-AppVersion', '0.4.1', '-PublicAppUrl', 'http://example.test'], 'HTTPS'),
    (['-AppVersion', '0.4.1', '-BuildNumber', '0'], 'BuildNumber'),
])
@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell build entrypoint')
def test_build_rejects_bad_release_inputs_before_running_any_tool(arguments, message):
    script = Path(__file__).resolve().parents[1] / 'scripts/build-android.ps1'
    assert script.is_file(), 'Android reproducible build entrypoint is missing'
    shell = shutil.which('pwsh')
    assert shell, 'PowerShell is required to exercise the build entrypoint'
    result = subprocess.run([shell, '-NoProfile', '-File', str(script), *arguments],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.skipif(os.name != 'nt', reason='Windows mapped-drive Flutter build entrypoint')
def test_build_cleans_stale_absolute_output_paths_before_dependency_restore(tmp_path):
    root = Path(__file__).resolve().parents[1]
    previous = root / 'artifacts/companion-android-debug.apk'
    if not previous.exists():
        pytest.skip('Local Android continuity fixture is not present')
    fake_flutter = tmp_path / 'flutter.ps1'
    trace = tmp_path / 'commands.txt'
    fake_flutter.write_text('''param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Forwarded)
Add-Content -LiteralPath $env:TASK_ANDROID_COMMAND_TRACE -Value ($Forwarded -join ' ')
if ($Forwarded[0] -eq 'pub') { exit 41 }
exit 0
''', encoding='utf-8')
    environment = dict(os.environ, TASK_ANDROID_COMMAND_TRACE=str(trace))
    result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-File', str(root / 'scripts/build-android.ps1'),
                             '-AppVersion', '0.4.1', '-BuildNumber', '2', '-FlutterPath', str(fake_flutter),
                             '-OutputDir', str(tmp_path / 'output'), '-PreviousApk', str(previous)],
                            env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode != 0 and 'dependency resolution failed' in result.stderr
    assert trace.read_text(encoding='utf-8').splitlines() == ['clean', 'pub get']
