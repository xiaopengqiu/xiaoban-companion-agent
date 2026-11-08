import json
import pytest
from scripts.android_push_config import load_config
from scripts.android_push_config import verify_manifest


def test_only_explicit_public_manifest_identifiers_can_be_injected(tmp_path):
    path = tmp_path / 'public-push.json'
    path.write_text(json.dumps({'huawei_app_id': '123', 'vivo_app_key': 'publicClientKey'}), encoding='utf-8')
    assert load_config(path) == {'huawei_app_id': '123', 'vivo_app_id': '', 'vivo_app_key': 'publicClientKey', 'honor_app_id': ''}


@pytest.mark.parametrize('value', [{'master_secret': 'never-embed'}, {'api_key': 'server-secret'}, {'huawei_app_id': '../file'}, {'vivo_app_id': 123}, {'honor_app_id': 'x' * 129}])
def test_private_unknown_and_invalid_values_fail_before_build(tmp_path, value):
    path = tmp_path / 'public-push.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError):
        load_config(path)


def test_manifest_identifier_must_match_requested_build_input():
    manifest = '''E: manifest (line=1)
  E: meta-data (line=2)
    A: android:name(0x1)="com.huawei.hms.client.appid" (Raw: "com.huawei.hms.client.appid")
    A: android:value(0x2)="appid=123" (Raw: "appid=123")
  E: meta-data (line=3)
    A: android:name(0x1)="com.vivo.push.app_id" (Raw: "com.vivo.push.app_id")
    A: android:value(0x2)="" (Raw: "")
  E: meta-data (line=4)
    A: android:name(0x1)="com.vivo.push.api_key" (Raw: "com.vivo.push.api_key")
    A: android:value(0x2)="" (Raw: "")
  E: meta-data (line=5)
    A: android:name(0x1)="com.hihonor.push.app_id" (Raw: "com.hihonor.push.app_id")
    A: android:value(0x2)="" (Raw: "")
'''
    expected = {'huawei_app_id': '123', 'vivo_app_id': '', 'vivo_app_key': '', 'honor_app_id': ''}
    assert verify_manifest(manifest, expected)['huawei_app_id'] == '123'
    with pytest.raises(ValueError):
        verify_manifest(manifest.replace('appid=123', 'appid=456'), expected)
