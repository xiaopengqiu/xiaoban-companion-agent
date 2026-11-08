"""Idempotently configure generated Flutter iOS/Android projects.

Run from any directory: python tool/configure_platforms.py
If projects are absent, this invokes the official flutter create generator.
"""
from pathlib import Path
import argparse
import json
import plistlib
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ANDROID = '{http://schemas.android.com/apk/res/android}'
ET.register_namespace('android', 'http://schemas.android.com/apk/res/android')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ios-push-environment', choices=['development', 'production'], help='Must match the APNs entitlement in your chosen provisioning profile; also enable Push Notifications in Xcode.')
parser.add_argument('--disable-ios-push', action='store_true')
args = parser.parse_args()


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + '\n', encoding='utf-8')


if not (ROOT / 'android/app/build.gradle.kts').exists() or not (ROOT / 'ios/Runner/Info.plist').exists():
    subprocess.run(['flutter', 'create', '.', '--platforms', 'android,ios', '--org', 'com.changban', '--project-name', 'companion_mobile', '--no-pub'], cwd=ROOT, check=True)

manifest_file = ROOT / 'android/app/src/main/AndroidManifest.xml'
tree = ET.parse(manifest_file)
manifest = tree.getroot()
permissions = [
    'android.permission.INTERNET', 'android.permission.POST_NOTIFICATIONS',
    'android.permission.health.READ_STEPS', 'android.permission.health.READ_SLEEP',
    'android.permission.health.READ_EXERCISE', 'android.permission.health.READ_HYDRATION',
    'android.permission.health.READ_HEART_RATE',
    'android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND',
]
for permission in permissions:
    if not any(p.get(ANDROID + 'name') == permission for p in manifest.findall('uses-permission')):
        ET.SubElement(manifest, 'uses-permission', {ANDROID + 'name': permission})
application = manifest.find('application')
application.set(ANDROID + 'label', '小伴')
application.set(ANDROID + 'allowBackup', 'false')
application.set(ANDROID + 'usesCleartextTraffic', 'false')
for name, action, permission in [
    ('ViewPermissionUsageActivity', 'android.intent.action.VIEW_PERMISSION_USAGE', 'android.permission.START_VIEW_PERMISSION_USAGE'),
    ('ShowPermissionRationaleActivity', 'androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE', None),
]:
    if not any(e.get(ANDROID + 'name') == name for e in application.findall('activity-alias')):
        attrs = {ANDROID + 'name': name, ANDROID + 'exported': 'true', ANDROID + 'targetActivity': '.MainActivity'}
        if permission:
            attrs[ANDROID + 'permission'] = permission
        alias = ET.SubElement(application, 'activity-alias', attrs)
        intent = ET.SubElement(alias, 'intent-filter')
        ET.SubElement(intent, 'action', {ANDROID + 'name': action})
        if permission:
            ET.SubElement(intent, 'category', {ANDROID + 'name': 'android.intent.category.HEALTH_PERMISSIONS'})
queries = manifest.find('queries')
if queries is None:
    queries = ET.SubElement(manifest, 'queries')
for package in ['com.google.android.apps.healthdata', 'com.sec.android.app.shealth']:
    if not any(p.get(ANDROID + 'name') == package for p in queries.findall('package')):
        ET.SubElement(queries, 'package', {ANDROID + 'name': package})
ET.indent(tree, space='    ')
tree.write(manifest_file, encoding='utf-8', xml_declaration=True)
write(ROOT / 'android/app/src/debug/AndroidManifest.xml', '''<manifest xmlns:android="http://schemas.android.com/apk/res/android" xmlns:tools="http://schemas.android.com/tools">
  <uses-permission android:name="android.permission.INTERNET" />
  <application android:usesCleartextTraffic="true" tools:replace="android:usesCleartextTraffic" />
</manifest>
''')
main_activity = ROOT / 'android/app/src/main/kotlin/com/changban/companion_mobile/MainActivity.kt'
write(main_activity, '''package com.changban.companion_mobile

import io.flutter.embedding.android.FlutterFragmentActivity

class MainActivity : FlutterFragmentActivity()
''')
gradle_file = ROOT / 'android/app/build.gradle.kts'
gradle = gradle_file.read_text(encoding='utf-8')
gradle = re.sub(r'minSdk = [^\n]+', 'minSdk = 29', gradle)
if 'com.google.gms.google-services' not in gradle:
    gradle += '''
// Firebase is optional: a normal local build needs no Firebase project.
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
}
'''
write(gradle_file, gradle)
settings_file = ROOT / 'android/settings.gradle.kts'
settings = settings_file.read_text(encoding='utf-8')
settings = settings.replace('plugins { id("com.google.gms.google-services") version "4.4.4" apply false }\n\n', '')
if 'com.google.gms.google-services' not in settings:
    settings = settings.replace('id("dev.flutter.flutter-plugin-loader")', 'id("com.google.gms.google-services") version "4.4.4" apply false\n    id("dev.flutter.flutter-plugin-loader")')
write(settings_file, settings)
gradle_properties_file = ROOT / 'android/gradle.properties'
gradle_properties = gradle_properties_file.read_text(encoding='utf-8')
if 'kotlin.incremental=' not in gradle_properties:
    gradle_properties += '\n# Allow Windows mapped worktree drives and Pub Cache sources on different roots.\nkotlin.incremental=false\n'
write(gradle_properties_file, gradle_properties)

info_file = ROOT / 'ios/Runner/Info.plist'
info = plistlib.loads(info_file.read_bytes())
info.update({
    'CFBundleDisplayName': '小伴',
    'NSHealthShareUsageDescription': '小伴会读取你授权的步数、睡眠、运动、饮水和心率，用于生成个人生活观察与适度提醒。',
    'NSLocalNetworkUsageDescription': '调试时连接你在局域网自行部署的陪伴服务。',
    'UIBackgroundModes': ['fetch', 'processing', 'remote-notification'],
    'BGTaskSchedulerPermittedIdentifiers': ['com.openwearables.healthsdk.task.refresh', 'com.openwearables.healthsdk.task.process'],
    'NSAppTransportSecurity': {'NSAllowsArbitraryLoads': False},
})
info_file.write_bytes(plistlib.dumps(info, sort_keys=False))
debug_info = dict(info)
debug_info['NSAppTransportSecurity'] = {'NSAllowsArbitraryLoads': True}
(ROOT / 'ios/Runner/Info-Debug.plist').write_bytes(plistlib.dumps(debug_info, sort_keys=False))
for name in ['Debug', 'Release']:
    xcconfig = ROOT / f'ios/Flutter/{name}.xcconfig'
    content = xcconfig.read_text(encoding='utf-8')
    content = re.sub(r'^COMPANION_ALLOW_HTTP = .*\n?', '', content, flags=re.MULTILINE)
    pods = f'#include? "Pods/Target Support Files/Pods-Runner/Pods-Runner.{name.lower()}.xcconfig"'
    if pods not in content:
        content = pods + '\n' + content
    write(xcconfig, content)
entitlement_file = ROOT / 'ios/Runner/Runner.entitlements'
entitlements = plistlib.loads(entitlement_file.read_bytes()) if entitlement_file.exists() else {}
entitlements.update({
    'com.apple.developer.healthkit': True,
    'com.apple.developer.healthkit.background-delivery': True,
})
if args.ios_push_environment:
    entitlements['aps-environment'] = args.ios_push_environment
if args.disable_ios_push:
    entitlements.pop('aps-environment', None)
entitlement_file.write_bytes(plistlib.dumps(entitlements))
pbx_file = ROOT / 'ios/Runner.xcodeproj/project.pbxproj'
pbx = pbx_file.read_text(encoding='utf-8')
# Runner has one INFOPLIST_FILE entry per Debug/Profile/Release config.
pbx = pbx.replace('INFOPLIST_FILE = Runner/Info.plist;', 'INFOPLIST_FILE = Runner/Info.plist;\n\t\t\t\tCODE_SIGN_ENTITLEMENTS = Runner/Runner.entitlements;') if 'CODE_SIGN_ENTITLEMENTS = Runner/Runner.entitlements;' not in pbx else pbx
pbx = re.sub(r'IPHONEOS_DEPLOYMENT_TARGET = [0-9.]+;', 'IPHONEOS_DEPLOYMENT_TARGET = 15.0;', pbx)
pbx = re.sub(r'(baseConfigurationReference = [^\n]*Debug.xcconfig.*?INFOPLIST_FILE =) Runner/Info.plist;', r'\1 Runner/Info-Debug.plist;', pbx, flags=re.DOTALL)
# The current flutter create template inserts SwiftPM references even when
# this client uses CocoaPods. Remove only that generated empty package,
# otherwise Xcode looks for a nonexistent ephemeral Swift package on macOS.
pbx = re.sub(r'^\s*[A-F0-9]+ /\* [^\n]*FlutterGeneratedPluginSwiftPackage[^\n]*\*/ = \{\n.*?^\s*\};\n', '', pbx, flags=re.MULTILINE | re.DOTALL)
pbx = '\n'.join(line for line in pbx.split('\n') if 'FlutterGeneratedPluginSwiftPackage' not in line)
write(pbx_file, pbx)
print('Configured Android API29+, Health Connect permissions, iOS15+ HealthKit and background sync. Push remains opt-in.')
