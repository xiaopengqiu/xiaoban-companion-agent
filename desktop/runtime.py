"""Resource, identity and lifecycle boundaries for the Windows shell."""
from __future__ import annotations

import html
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit


def resource_root() -> Path:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[1]


def data_directory() -> Path:
    path = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Xiaoban'
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_url(value: str, *, allow_loopback: bool = False) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError('地址的端口无效') from error
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError('地址须为不含凭证、查询参数或片段的 HTTPS 页面地址')
    if parsed.scheme == 'https':
        return value
    if allow_loopback and parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'} and port:
        return value
    raise ValueError('在线账户只能使用 HTTPS；HTTP 仅允许明确的本机 loopback 端口')


class Settings:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'desktop-settings.json'

    @property
    def public_url(self) -> str:
        candidates = [self.path, resource_root() / 'desktop/defaults.json']
        for path in candidates:
            try:
                value = json.loads(path.read_text(encoding='utf-8')).get('public_app_url', '')
                if value:
                    return validate_url(value)
            except (OSError, ValueError, TypeError):
                continue
        return ''

    def set_public_url(self, value: str) -> None:
        value = validate_url(value)
        self._save({'public_app_url': value})

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def local_port(self) -> int:
        value = self._read().get('local_port', 0)
        return value if type(value) is int and 1024 <= value <= 65535 else 0

    def set_local_port(self, port: int) -> None:
        if not 1024 <= port <= 65535:
            raise ValueError('无效的本机端口')
        self._save({'local_port': port})

    def _save(self, updates: dict) -> None:
        current = {key: value for key, value in self._read().items() if key in {'public_app_url', 'local_port'}}
        current.update(updates)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(current, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)


class SingleInstance:
    """A Windows per-user/session mutex; no registry or global system edits."""
    def __init__(self, directory: Path):
        identity = hashlib.sha256(str(Path(directory).resolve()).casefold().encode()).hexdigest()[:24]
        self.name = 'Local\\XiaobanDesktop-' + identity
        self._handle = None
        self._kernel = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        if sys.platform != 'win32':
            return True
        import ctypes
        from ctypes import wintypes
        self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel.CreateMutexW.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        handle = self._kernel.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError('无法创建小伴单实例锁')
        if ctypes.get_last_error() == 183:
            self._kernel.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


_environment_lock = threading.Lock()
_environment_users = 0
_saved_environment: dict[str, str] = {}


def _external_setting(key: str) -> bool:
    return key.startswith(('COMPANION_', 'OPEN_WEARABLES_', 'MEM0_', 'FCM_', 'LANGCHAIN_', 'LANGSMITH_', 'OTEL_')) or key == 'GOOGLE_APPLICATION_CREDENTIALS'


def _enter_offline() -> None:
    global _environment_users, _saved_environment
    with _environment_lock:
        if _environment_users == 0:
            _saved_environment = {key: value for key, value in os.environ.items() if _external_setting(key)}
            for key in _saved_environment:
                os.environ.pop(key, None)
            os.environ['MEM0_ENABLED'] = 'false'
            os.environ['LANGSMITH_TRACING'] = 'false'
            os.environ['LANGCHAIN_TRACING_V2'] = 'false'
        _environment_users += 1


def _leave_offline() -> None:
    global _environment_users
    with _environment_lock:
        _environment_users = max(0, _environment_users - 1)
        if _environment_users == 0:
            for key in list(os.environ):
                if _external_setting(key):
                    os.environ.pop(key, None)
            os.environ.update(_saved_environment)


class LocalServer:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory is not None else data_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.port: int | None = None
        self.settings_key = secrets.token_urlsafe(32)
        self._server = None
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._error: BaseException | None = None
        self._entered = False
        self._stop_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        if self.running:
            return f'http://127.0.0.1:{self.port}'
        _enter_offline()
        self._entered = True
        try:
            import uvicorn
            from companion.app import create_app
            from fastapi import Request
            from fastapi.responses import HTMLResponse, JSONResponse
            kwargs = {'db_path': str(self.directory / 'companion.sqlite3'), 'start_scheduler': True}
            if 'web_dir' in inspect.signature(create_app).parameters:
                kwargs['web_dir'] = resource_root() / 'web'
            app = create_app(**kwargs)

            @app.middleware('http')
            async def desktop_boundary(request: Request, call_next):
                if request.url.path.startswith('/api/integrations/open-wearables/') or request.url.path == '/api/devices':
                    return JSONResponse({'detail': '本机离线模式不连接外部服务。真实穿戴同步和推送请使用在线账户及手机 App。'}, status_code=409)
                if request.url.path == '/desktop/settings':
                    return await self._settings_page(request, HTMLResponse)
                return await call_next(request)

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            settings = Settings(self.directory)
            try:
                listener.bind(('127.0.0.1', settings.local_port))
            except OSError:
                listener.bind(('127.0.0.1', 0))
            self._listener = listener
            self.port = listener.getsockname()[1]
            settings.set_local_port(self.port)
            config = uvicorn.Config(app, host='127.0.0.1', port=self.port, access_log=False, log_config=None,
                                    timeout_graceful_shutdown=3, lifespan='on', ws='none')
            self._server = uvicorn.Server(config)

            def serve():
                try:
                    self._server.run(sockets=[listener])
                except BaseException as error:
                    self._error = error
                    logging.error('Local service stopped unexpectedly: %s', type(error).__name__)

            self._thread = threading.Thread(target=serve, name='xiaoban-local-service', daemon=False)
            self._thread.start()
            deadline = time.monotonic() + 25
            while not self._server.started:
                if self._error or not self.running or time.monotonic() > deadline:
                    raise RuntimeError('本机服务未能启动，请查看本机 desktop.log') from self._error
                time.sleep(.03)
            return f'http://127.0.0.1:{self.port}'
        except BaseException:
            self.stop()
            raise

    async def _settings_page(self, request, response_type):
        if request.query_params.get('key') != self.settings_key:
            return response_type('禁止访问', status_code=403)
        settings = Settings(self.directory)
        notice = ''
        if request.method == 'POST':
            raw = await request.body()
            if len(raw) > 4096:
                return response_type('配置过长', status_code=413)
            origin = request.headers.get('origin')
            if origin and origin != f'http://127.0.0.1:{self.port}':
                return response_type('禁止跨来源修改', status_code=403)
            try:
                settings.set_public_url(parse_qs(raw.decode('utf-8')).get('url', [''])[0])
                notice = '地址已保存，请使用窗口菜单“在线账户（云端）”进入。'
            except (ValueError, UnicodeError) as error:
                notice = str(error)
        value = html.escape(settings.public_url, quote=True)
        nonce = secrets.token_urlsafe(20)
        return response_type(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>小伴 · 在线地址设置</title>
<style nonce="{nonce}">body{{background:#f6f4ec;color:#213f35;font:17px/1.8 "Microsoft YaHei",sans-serif;margin:60px auto;max-width:760px;padding:30px}}input{{width:95%;padding:14px;border:1px solid #cbd9cc;border-radius:12px}}button{{background:#196b5c;color:white;padding:13px 26px;border:0;border-radius:12px;margin-top:20px}}</style>
<h1>在线账户与本机数据</h1><p>在线账户使用云端服务，可以与手机登录同一账号；本机数据仅保存在这台电脑，不自动上传或合并。</p>
<p>手表 / 手环健康授权由手机 App 采集。Windows 可以查看已同步记录，也可以手动记录生活。</p>
<form method="post"><label>在线服务 HTTPS 地址<br><input name="url" value="{value}" required></label><br><button>保存在线地址</button></form>
<p>{html.escape(notice)}</p><p>切换数据域请使用窗口顶部菜单。本机模式仅使用本地规则与结构化记忆；可选大模型/语义记忆由在线服务配置。</p></html>''', headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': f"default-src 'none'; style-src 'nonce-{nonce}'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    def stop(self) -> None:
        with self._stop_lock:
            if self._server is not None:
                self._server.should_exit = True
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join(timeout=8)
                if self._thread.is_alive():
                    self._server.force_exit = True
                    self._thread.join(timeout=4)
                if self._thread.is_alive():
                    raise RuntimeError('本机服务仍在关闭，请稍后退出')
            if self._listener is not None:
                self._listener.close()
                self._listener = None
            if self._entered:
                _leave_offline()
                self._entered = False
