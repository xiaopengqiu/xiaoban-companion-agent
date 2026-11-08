"""Pinned, signed portable releases. No application data or credentials are used."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import httpx

MAX_SIZE = 64 * 1024 * 1024
CHECK_DEADLINE = 30
DOWNLOAD_DEADLINE = 180
CHECK_INTERVAL = 6 * 60 * 60
INITIAL_DELAY = 2
PAYLOAD_FIELDS = {'schema', 'version', 'platform', 'url', 'sha256', 'size', 'published_at', 'notes'}
VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or len(value) > 50 or not VERSION.fullmatch(value):
        raise ValueError('版本号无效')
    numbers = tuple(int(part) for part in value.split('.'))
    if any(part > 9007199254740991 for part in numbers):
        raise ValueError('版本号无效')
    return numbers


def canonical_json(value) -> bytes:
    def validate(item, depth=0):
        if depth > 16:
            raise ValueError('更新元数据嵌套过深')
        if item is None or type(item) in {str, int, bool}:
            return
        if type(item) is list:
            for child in item:
                validate(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child, depth + 1)
            return
        raise ValueError('更新元数据类型无效')
    validate(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict) or set(config) != {'schema', 'version', 'feed_url', 'public_key'} or type(config['schema']) is not int or config['schema'] != 1:
        raise ValueError('更新配置无效')
    version_tuple(config['version'])
    try:
        parsed = urlsplit(config['feed_url'])
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != '/api/desktop-updates/latest'
                or parsed.port not in {None, 443} or '\\' in config['feed_url']):
            raise ValueError()
        key = base64.b64decode(config['public_key'], validate=True)
        if len(key) != 32:
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(key)
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError('更新配置无效') from error
    return dict(config)


def verify_manifest(config: dict, envelope: dict) -> dict:
    """Verify the signature before interpreting any release-supplied field."""
    config = validate_config(config)
    if not isinstance(envelope, dict) or set(envelope) != {'payload', 'signature'}:
        raise ValueError('更新签名格式无效')
    try:
        signature = base64.b64decode(envelope['signature'], validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(config['public_key'], validate=True))
        key.verify(signature, canonical_json(envelope['payload']))
    except (InvalidSignature, ValueError, TypeError) as error:
        raise ValueError('更新签名校验失败') from error
    payload = envelope['payload']
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
        raise ValueError('更新字段无效')
    if type(payload['schema']) is not int or payload['schema'] != 1 or payload['platform'] != 'windows-x64':
        raise ValueError('不支持的更新格式或平台')
    if version_tuple(payload['version']) < version_tuple(config['version']):
        raise ValueError('拒绝安装较旧版本')
    if type(payload['size']) is not int or not 1 <= payload['size'] <= MAX_SIZE:
        raise ValueError('更新文件大小无效')
    if not isinstance(payload['sha256'], str) or not SHA.fullmatch(payload['sha256']):
        raise ValueError('更新文件摘要无效')
    if not isinstance(payload['notes'], str) or len(payload['notes']) > 2000:
        raise ValueError('更新说明过长')
    try:
        stamp = payload['published_at']
        if not isinstance(stamp, str) or len(stamp) > 40 or 'T' not in stamp:
            raise ValueError()
        timestamp = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError()
    except (ValueError, TypeError) as error:
        raise ValueError('更新时间无效') from error
    feed = urlsplit(config['feed_url'])
    expected = f'{feed.scheme}://{feed.netloc}/api/desktop-updates/assets/{payload["version"]}/{payload["sha256"]}.exe'
    if payload['url'] != expected:
        raise ValueError('更新下载地址无效')
    return dict(payload)


def plain_path(path: Path) -> Path:
    """Reject link/reparse components instead of following them during writes."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('更新路径不能包含链接或重解析点')
    if str(path.resolve()).casefold() != str(path).casefold():
        raise ValueError('更新路径无效')
    return path


def within(path: Path, parent: Path) -> Path:
    path, parent = plain_path(path), plain_path(parent)
    if path == parent or parent not in path.parents:
        raise ValueError('更新文件必须位于专用更新目录')
    return path


def read_json(path: Path, limit=65536) -> dict:
    plain_path(path)
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('更新状态文件过大')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('更新状态无效')
    return value


def atomic_json(path: Path, value: dict) -> None:
    plain_path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(canonical_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_sha256(path: Path) -> str:
    plain_path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(256 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, payload: dict) -> None:
    plain_path(path)
    if not path.is_file() or path.stat().st_size != payload['size'] or file_sha256(path) != payload['sha256']:
        raise ValueError('更新文件大小或 SHA256 校验失败')
    with path.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('更新文件不是 Windows 可执行程序')


class UpdateManager:
    def __init__(self, config: dict, data_dir: Path, executable: Path | None = None, *, _transport=None):
        self.config = validate_config(config)
        self.data_dir = plain_path(data_dir)
        self.directory = plain_path(self.data_dir / 'updates')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.executable = Path(executable) if executable else (Path(sys.executable) if getattr(sys, 'frozen', False) else None)
        self._transport = _transport
        self._lock = threading.RLock()
        self._operation = threading.Lock()
        self._stop = threading.Event()
        self._scheduler = None
        self._worker = None
        self._loop = None
        self._task = None
        self._envelope = None
        self._preferences = {'auto_download': True}
        try:
            saved = read_json(self.directory / 'state.json')
            if type(saved.get('auto_download')) is bool:
                self._preferences['auto_download'] = saved['auto_download']
            if isinstance(saved.get('failed_version'), str):
                self._preferences['failed_version'] = saved['failed_version']
        except (OSError, ValueError, TypeError):
            pass
        self._status = {'current_version': self.config['version'], 'latest_version': '', 'phase': 'idle',
                        'progress': 0, 'auto_download': self._preferences['auto_download'], 'message': '更新将在后台检查', 'notes': ''}
        try:
            pending, payload, _ = self._pending()
            self._envelope = pending['manifest']
            self._status.update(latest_version=payload['version'], phase='ready', progress=100,
                                notes=payload['notes'], message='更新已下载，下次启动自动安装')
        except (OSError, ValueError, TypeError, KeyError):
            pass

    @property
    def busy(self) -> bool:
        return self._operation.locked()

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set(self, **values) -> None:
        with self._lock:
            self._status.update(values)

    def set_auto_download(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError('自动下载设置无效')
        with self._lock:
            self._preferences['auto_download'] = enabled
            self._status['auto_download'] = enabled
            atomic_json(self.directory / 'state.json', self._preferences)

    def start(self) -> None:
        with self._lock:
            if self._scheduler and self._scheduler.is_alive():
                return
            self._stop.clear()
            self._scheduler = threading.Thread(target=self._schedule, name='xiaoban-update-scheduler', daemon=True)
            self._scheduler.start()

    def _schedule(self):
        if self._stop.wait(INITIAL_DELAY):
            return
        while not self._stop.is_set():
            self.check_async()
            if self._stop.wait(CHECK_INTERVAL):
                return

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            loop, task = self._loop, self._task
        if loop and task and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        for thread in (self._worker, self._scheduler):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2)

    def _launch(self, operation) -> bool:
        if self._stop.is_set() or not self._operation.acquire(blocking=False):
            return False
        self._worker = threading.Thread(target=self._run, args=(operation,), name='xiaoban-update-transfer', daemon=True)
        self._worker.start()
        return True

    def check_async(self) -> bool:
        return self._launch(self._check)

    def download_async(self) -> bool:
        return self._launch(self._download)

    def _run(self, operation) -> None:
        async def run():
            with self._lock:
                self._loop, self._task = asyncio.get_running_loop(), asyncio.current_task()
            if self._stop.is_set():
                return
            await operation()
        try:
            asyncio.run(run())
        except asyncio.CancelledError:
            self._set(phase='idle', message='更新检查已停止')
        except (TimeoutError, httpx.TimeoutException):
            self._set(phase='error', message='更新请求超时，稍后可重新检查')
        except httpx.HTTPError:
            self._set(phase='error', message='暂时无法连接更新服务，稍后可重新检查')
        except (OSError, ValueError, TypeError, KeyError) as error:
            self._set(phase='error', message=str(error) if isinstance(error, ValueError) else '更新文件无法读取或保存，请检查磁盘空间和权限')
        except Exception:
            self._set(phase='error', message='更新服务返回的数据无效，请稍后重新检查')
        finally:
            with self._lock:
                self._loop, self._task = None, None
            self._operation.release()

    def _client(self):
        # Respect the user's normal HTTPS proxy and certificate environment;
        # TLS verification remains enabled and redirects remain forbidden.
        return httpx.AsyncClient(timeout=httpx.Timeout(10), follow_redirects=False, trust_env=True, transport=self._transport)

    async def _check(self):
        self._set(phase='checking', progress=0, message='正在检查更新')
        async def request():
            async with self._client() as client:
                async with client.stream('GET', self.config['feed_url']) as response:
                    if response.status_code == 404:
                        return None
                    if response.status_code != 200:
                        raise ValueError('更新服务暂时不可用')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ValueError('更新元数据过大')
                    return json.loads(data)
        envelope = await asyncio.wait_for(request(), CHECK_DEADLINE)
        if envelope is None:
            self._set(phase='current', message='当前没有可用更新')
            return
        payload = verify_manifest(self.config, envelope)
        self._envelope = envelope
        self._set(latest_version=payload['version'], notes=payload['notes'])
        if version_tuple(payload['version']) == version_tuple(self.config['version']):
            self._set(phase='current', message='已是最新版本')
            return
        if self.has_pending():
            self._set(phase='ready', progress=100, message='更新已下载，下次启动自动安装')
            return
        self._set(phase='available', message='有新版本可下载')
        if self.status()['auto_download'] and payload['version'] != self._preferences.get('failed_version'):
            await self._download()
        elif payload['version'] == self._preferences.get('failed_version'):
            self._set(message='上次安装未成功，可手动重新下载后重试')

    async def _download(self):
        if not self._envelope:
            raise ValueError('请先检查可用更新')
        payload = verify_manifest(self.config, self._envelope)
        if version_tuple(payload['version']) <= version_tuple(self.config['version']):
            raise ValueError('当前已是最新版本')
        stage = within(self.directory / 'staging' / payload['version'], self.directory)
        stage.mkdir(parents=True, exist_ok=True)
        candidate = within(stage / (payload['sha256'] + '.exe'), self.directory)
        part = within(stage / (uuid.uuid4().hex + '.part'), self.directory)
        self._set(phase='downloading', progress=0, message='正在后台下载，完成后可重启安装')
        async def transfer():
            total, digest = 0, hashlib.sha256()
            async with self._client() as client:
                async with client.stream('GET', payload['url']) as response:
                    if response.status_code != 200 or response.headers.get('content-encoding', 'identity') != 'identity':
                        raise ValueError('更新下载响应无效')
                    length = response.headers.get('content-length')
                    if length is not None and length != str(payload['size']):
                        raise ValueError('更新文件大小不符')
                    with part.open('xb') as stream:
                        async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                            total += len(chunk)
                            if total > payload['size']:
                                raise ValueError('更新文件超过声明大小')
                            digest.update(chunk)
                            stream.write(chunk)
                            self._set(progress=min(99, int(total * 100 / payload['size'])))
                        stream.flush()
                        os.fsync(stream.fileno())
            if total != payload['size'] or digest.hexdigest() != payload['sha256']:
                raise ValueError('更新文件大小或 SHA256 校验失败')
            verify_file(part, payload)
        try:
            await asyncio.wait_for(transfer(), DOWNLOAD_DEADLINE)
            plain_path(candidate)
            os.replace(part, candidate)
            atomic_json(self.directory / 'pending.json', {'schema': 1, 'manifest': self._envelope, 'staged': str(candidate)})
            with self._lock:
                self._preferences.pop('failed_version', None)
                atomic_json(self.directory / 'state.json', self._preferences)
            self._set(phase='ready', progress=100, message='更新已下载，下次启动自动安装')
        finally:
            part.unlink(missing_ok=True)

    def _pending(self):
        pending = read_json(self.directory / 'pending.json')
        if set(pending) != {'schema', 'manifest', 'staged'} or type(pending['schema']) is not int or pending['schema'] != 1:
            raise ValueError('待安装更新格式无效')
        payload = verify_manifest(self.config, pending['manifest'])
        if version_tuple(payload['version']) <= version_tuple(self.config['version']):
            raise ValueError('待安装版本不是更新版本')
        if payload['version'] == self._preferences.get('failed_version'):
            raise ValueError('上次安装失败，等待手动重试')
        staged = within(Path(pending['staged']), self.directory)
        verify_file(staged, payload)
        return pending, payload, staged

    def has_pending(self) -> bool:
        try:
            self._pending()
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def launch_pending(self) -> bool:
        if not getattr(sys, 'frozen', False) or not self.executable:
            self._set(phase='error', message='源码运行模式不能替换 Python；请使用 Windows 便携版')
            return False
        if not self._operation.acquire(blocking=False):
            return False
        try:
            from desktop.update_apply import owner_processes
            pending, _, staged = self._pending()
            target = plain_path(self.executable)
            if target.suffix.lower() != '.exe' or target.name.lower().startswith(('python', 'pypy')):
                raise ValueError('不能替换 Python 解释器')
            if self.directory == target.parent or self.directory in target.parents:
                raise ValueError('程序不能从更新暂存目录运行')
            helper = within(self.directory / ('helper-' + uuid.uuid4().hex + '.exe'), self.directory)
            old_sha = file_sha256(target)
            shutil.copyfile(target, helper)
            if file_sha256(helper) != old_sha:
                raise ValueError('更新助手复制校验失败')
            request = self.directory / ('apply-' + uuid.uuid4().hex + '.json')
            atomic_json(request, {'schema': 1, 'data_dir': str(self.data_dir), 'target': str(target),
                                  'staged': str(staged), 'helper': str(helper), 'manifest': pending['manifest'],
                                  'old_sha256': old_sha, 'processes': owner_processes(target)})
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
            flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
            subprocess.Popen([str(helper), '--apply-update', str(request)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
                             env=env, creationflags=flags, close_fds=True)
            self._set(phase='applying', message='更新助手已启动，正在等待程序正常退出')
            return True
        except (OSError, ValueError, TypeError, KeyError):
            self._set(phase='error', message='无法启动更新助手，请检查程序目录权限后重试')
            return False
        finally:
            self._operation.release()
