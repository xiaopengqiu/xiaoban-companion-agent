"""Administrator-controlled free/basic JPush transport; never exposes server secrets."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
import httpx

PURPOSE = b'xiaoban-native-push-v1'
FIREBASE_FIELDS = {'api_key', 'app_id', 'messaging_sender_id', 'project_id'}
MANUFACTURER_FIELDS = {'huawei_app_id', 'xiaomi_app_id', 'xiaomi_app_key', 'oppo_app_key',
                       'oppo_app_secret', 'vivo_app_id', 'vivo_app_key', 'honor_app_id'}
http_client = httpx.Client


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def options(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'firebase', 'manufacturers'}:
        raise ValueError('客户端推送参数无效')
    result = {}
    for group, fields in [('firebase', FIREBASE_FIELDS), ('manufacturers', MANUFACTURER_FIELDS)]:
        data = value.get(group, {})
        if not isinstance(data, dict) or set(data) - fields:
            raise ValueError('客户端推送参数无效')
        result[group] = {}
        for name in sorted(fields):
            item = data.get(name, '')
            if not isinstance(item, str) or len(item) > 500 or any(ord(c) < 32 for c in item):
                raise ValueError('客户端推送参数无效')
            result[group][name] = item.strip()
    return result


def _key():
    secret = os.getenv('COMPANION_CONFIG_ENCRYPTION_KEY') or os.getenv('AUTH_PEPPER', '')
    if len(secret) < 32:
        raise ValueError('服务端尚未配置设置加密密钥')
    return hashlib.sha256(PURPOSE + b'\0' + secret.encode()).digest()


def _encrypt(value):
    iv = os.urandom(12)
    encrypted = AESGCM(_key()).encrypt(iv, compact(value).encode(), PURPOSE)
    return compact({'version': 1, 'iv': base64.b64encode(iv).decode(), 'data': base64.b64encode(encrypted).decode()})


def _decrypt(value):
    try:
        data = json.loads(value)
        if data['version'] != 1:
            raise ValueError()
        return json.loads(AESGCM(_key()).decrypt(base64.b64decode(data['iv']),
                                               base64.b64decode(data['data']), PURPOSE))
    except Exception as exc:
        raise ValueError('推送配置暂时无法读取，请检查服务器设置') from exc


class NativePush:
    def __init__(self, store):
        self.store = store
        with store.transaction():
            store.connection.executescript('''
                CREATE TABLE IF NOT EXISTS service_state (state_key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS native_push_devices (
                    registration_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL, config_revision INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS native_push_devices_user ON native_push_devices(user_id);
            ''')

    def config(self):
        with self.store.lock:
            row = self.store.connection.execute("SELECT value FROM service_state WHERE state_key='native_push_settings'").fetchone()
        if row:
            return _decrypt(row[0])
        return {'enabled': False, 'provider': 'disabled', 'app_key': '', 'master_secret': '',
                'client_options': options(), 'revision': 1}

    def admin_config(self):
        c = self.config()
        return {k: v for k, v in c.items() if k != 'master_secret'} | {
            'master_secret_configured': bool(c['master_secret']), 'plan': 'free',
            'client_config_scope': {'runtime': ['app_key', 'firebase', 'xiaomi', 'oppo'],
                                    'requires_rebuild': ['huawei_app_id', 'vivo_app_id', 'vivo_app_key', 'honor_app_id']}}

    def public_config(self):
        try:
            c = self.config()
        except ValueError:
            c = {'enabled': False, 'provider': 'disabled', 'app_key': '', 'master_secret': '',
                 'client_options': options(), 'revision': 0}
        return {'enabled': c['enabled'], 'provider': c['provider'], 'app_key': c['app_key'],
                'configured': bool(c['enabled'] and c['provider'] == 'jpush' and c['app_key'] and c['master_secret']),
                'revision': c['revision'], 'client_options': c['client_options']}

    def save(self, changes):
        fields = {'enabled', 'provider', 'app_key', 'master_secret', 'clear_master_secret', 'client_options'}
        if not isinstance(changes, dict) or set(changes) - fields:
            raise ValueError('推送设置字段无效')
        with self.store.transaction():
            old = self.config()
            c = {**old, **{k: v for k, v in changes.items() if k not in {'master_secret', 'clear_master_secret'}}}
            if type(c['enabled']) is not bool or not isinstance(c['provider'], str) or c['provider'] not in {'disabled', 'jpush'}:
                raise ValueError('推送开关或渠道无效')
            if not isinstance(c['app_key'], str) or c['app_key'] and not re.fullmatch(r'[0-9a-fA-F]{24}', c['app_key']):
                raise ValueError('请填写有效的极光 AppKey')
            if 'clear_master_secret' in changes and type(changes['clear_master_secret']) is not bool:
                raise ValueError('清除密钥开关无效')
            if 'master_secret' in changes:
                secret = changes['master_secret']
                if not isinstance(secret, str) or len(secret) > 8192:
                    raise ValueError('推送服务密钥无效')
                if secret.strip():
                    c['master_secret'] = secret.strip()
            if changes.get('clear_master_secret'):
                c['master_secret'] = ''
                c['enabled'] = False
            c['client_options'] = options(c['client_options'])
            c['revision'] = old['revision'] + 1
            self.store.connection.execute("INSERT INTO service_state VALUES('native_push_settings',?) ON CONFLICT(state_key) DO UPDATE SET value=excluded.value", (_encrypt(c),))
        return self.admin_config()

    @staticmethod
    def registration_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,200}', value):
            raise ValueError('设备推送标识无效')
        return value

    def register(self, user_id, data, now):
        if not isinstance(data, dict) or set(data) != {'provider', 'registration_id', 'config_revision'} or data['provider'] != 'jpush':
            raise ValueError('设备注册参数无效')
        registration = self.registration_id(data['registration_id'])
        with self.store.transaction():
            config = self.public_config()
            if not config['configured']:
                raise HTTPException(503, '系统通知尚未配置')
            if type(data['config_revision']) is not int or data['config_revision'] != config['revision']:
                raise HTTPException(409, '推送配置已改变，请重新开启通知')
            count = self.store.connection.execute('SELECT count(*) FROM native_push_devices WHERE user_id=? AND registration_id<>?', (user_id, registration)).fetchone()[0]
            if count >= 5:
                raise ValueError('每个账号最多启用五台推送设备')
            self.store.connection.execute('INSERT INTO native_push_devices VALUES(?,?,?,?,?) ON CONFLICT(registration_id) DO UPDATE SET user_id=excluded.user_id,provider=excluded.provider,config_revision=excluded.config_revision,created_at=excluded.created_at',
                                          (registration, user_id, 'jpush', config['revision'], now.isoformat()))
        return {'registered': True, 'provider': 'jpush', 'config_revision': config['revision']}

    def unregister(self, user_id, data):
        if not isinstance(data, dict) or set(data) != {'registration_id'}:
            raise ValueError('设备注销参数无效')
        registration = self.registration_id(data['registration_id'])
        with self.store.transaction():
            self.store.connection.execute('DELETE FROM native_push_devices WHERE registration_id=? AND user_id=?', (registration, user_id))
        return {'unregistered': True}

    def send(self, user_id, care_id='', *, test=False):
        configured, registrations = False, []
        def result(status, sent=0):
            return {'status': status, 'configured': configured, 'queued': sent, 'sent': sent,
                    'failed': len(registrations) if status == 'failed' else 0}
        try:
            c = self.config()
            configured = bool(c['enabled'] and c['provider'] == 'jpush' and c['app_key'] and c['master_secret'])
            if not configured:
                return result('not_configured')
            with self.store.lock:
                registrations = [r[0] for r in self.store.connection.execute(
                    'SELECT registration_id FROM native_push_devices WHERE user_id=? AND config_revision=? LIMIT 5', (user_id, c['revision']))]
            if not registrations:
                return result('no_devices')
            payload = {'platform': 'android', 'audience': {'registration_id': registrations},
                       'notification': {'android': {'alert': '有一条新的关心，打开小伴查看。' if not test else '这是一条小伴通知测试。',
                           'title': '小伴', 'channel_id': 'xiaoban_care', 'extras': {'type': 'care' if not test else 'test', 'care_id': care_id}}},
                       'options': {'time_to_live': 3600}}
            with http_client(timeout=10, follow_redirects=False) as client:
                with client.stream('POST', 'https://api.jpush.cn/v3/push', json=payload,
                                   auth=httpx.BasicAuth(c['app_key'], c['master_secret'])) as response:
                    if not response.is_success:
                        return result('failed')
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 16000:
                            return result('failed')
            accepted = json.loads(raw)
            if not isinstance(accepted, dict) or accepted.get('error') or type(accepted.get('msg_id')) not in (int, float, str):
                return result('failed')
            return result('queued', len(registrations))
        except Exception:
            return result('failed')
