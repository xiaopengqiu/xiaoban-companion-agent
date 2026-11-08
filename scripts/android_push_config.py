"""Validate the four public SDK identifiers that require Android rebuilding.

This file deliberately rejects API/MasterSecret/server account configurations.
"""
import argparse
import json
from pathlib import Path
import re
import os
import subprocess
import zipfile

FIELDS = ('huawei_app_id', 'vivo_app_id', 'vivo_app_key', 'honor_app_id')
GRADLE = dict(zip(FIELDS, ('xiaobanHuaweiAppId', 'xiaobanVivoAppId', 'xiaobanVivoAppKey', 'xiaobanHonorAppId')))
METADATA = dict(zip(FIELDS, ('com.huawei.hms.client.appid', 'com.vivo.push.app_id', 'com.vivo.push.api_key', 'com.hihonor.push.app_id')))


def load_config(path: Path | None) -> dict[str, str]:
    value = {}
    if path is not None:
        if path.stat().st_size > 4096:
            raise ValueError('Public SDK config must be at most 4096 bytes')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or set(value) - set(FIELDS):
        raise ValueError('Only public Huawei/vivo/Honor client identifiers are allowed; never supply server secrets')
    for field, item in value.items():
        if not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{0,128}', item):
            raise ValueError('Public SDK identifiers must be bounded alphanumeric strings')
    return {field: value.get(field, '') for field in FIELDS}


def verify_manifest(manifest: str, expected: dict[str, str]) -> dict[str, str]:
    metadata = {}
    for block in re.findall(r'E: meta-data[^\n]*\n((?:[ \t]+A:[^\n]*\n?)+)', manifest):
        name = re.search(r'android:name\([^)]*\)=("[^"\n]*")', block)
        value = re.search(r'android:value\([^)]*\)=("[^"\n]*"|\(type 0x10\)0x[0-9a-f]+)', block)
        if name and value:
            raw = value[1]
            metadata[json.loads(name[1])] = json.loads(raw) if raw.startswith('"') else str(int(raw.split(')0x')[1], 16))
    actual = {}
    for field, name in METADATA.items():
        if name not in metadata:
            raise ValueError('APK is missing required manufacturer metadata')
        actual[field] = metadata[name].removeprefix('appid=') if field == 'huawei_app_id' else metadata[name]
    if actual != expected:
        raise ValueError('APK manufacturer metadata does not match public push build inputs')
    return actual


def inspect_push_apk(apk: Path, config: dict[str, str]) -> dict:
    sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path(os.environ['LOCALAPPDATA']) / 'CodexTools/AndroidSdk')
    aapt = next(iter(sorted((sdk / 'build-tools').glob('*/aapt.exe'), reverse=True)), None)
    if aapt is None:
        raise ValueError('Android aapt is required for push manifest verification')
    apk = apk.resolve(strict=True)
    manifest = subprocess.run([str(aapt), 'dump', 'xmltree', apk.name, 'AndroidManifest.xml'], cwd=apk.parent,
                              check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60).stdout
    actual = verify_manifest(manifest, config)
    for forbidden in ('io.flutter.plugins.firebase.messaging.FlutterFirebaseMessagingService',
                      'io.flutter.plugins.firebase.messaging.FlutterFirebaseMessagingReceiver',
                      'android.permission.READ_PHONE_STATE', 'android.permission.ACCESS_FINE_LOCATION'):
        if forbidden in manifest:
            raise ValueError('APK has a conflicting FCM handler or unexpected sensitive permission')
    classes = ('PluginHuaweiPlatformsService', 'PluginXiaomiPlatformsReceiver', 'PluginOppoPushService',
               'PluginVivoMessageReceiver', 'JHonorService', 'PluginFCMMessagingService')
    with zipfile.ZipFile(apk) as archive:
        dex = b''.join(archive.read(name) for name in archive.namelist() if re.fullmatch(r'classes\d*\.dex', name))
    if any(f'Lcn/jpush/android/service/{name};'.encode() not in dex for name in classes) or b'xiaoban_care' not in dex:
        raise ValueError('APK is missing a bundled native push channel or notification channel')
    return {'provider': 'jpush', 'flutter_sdk': '3.6.0', 'jpush_sdk': '6.3.0', 'jcore_sdk': '5.6.0',
            'manufacturer_extensions': ['huawei', 'xiaomi', 'oppo', 'vivo', 'honor', 'fcm'],
            'compiled_manufacturers': actual, 'notification_channel': 'xiaoban_care',
            'manifest_and_dex_verified': True, 'real_device_delivery_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path)
    parser.add_argument('--gradle', action='store_true')
    args = parser.parse_args()
    config = load_config(args.file)
    print(json.dumps({GRADLE[k]: v for k, v in config.items()} if args.gradle else config))
