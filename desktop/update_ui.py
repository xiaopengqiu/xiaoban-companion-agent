"""Local-only update window. The chat WebView never receives this native API."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import sys
import threading

from desktop.runtime import resource_root


def load_update_config() -> dict:
    config = json.loads((resource_root() / 'desktop/update-config.json').read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('Invalid bundled update configuration')
    return config


def acknowledge_startup(directory: Path, path: Path, token: str, version: str) -> bool:
    """A one-shot local readiness receipt, confined to this profile's updates directory."""
    if not getattr(sys, 'frozen', False) or not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64}', token):
        return False
    from desktop.updater import atomic_json, plain_path, version_tuple
    try:
        updates, path = plain_path(Path(directory) / 'updates'), plain_path(path)
        if path.parent != updates or path.name != f'startup-{token}.json' or path.exists():
            return False
        version_tuple(version)
        atomic_json(path, {'status': 'ready', 'version': version, 'pid': os.getpid(), 'token': token})
        return True
    except (OSError, ValueError, TypeError):
        logging.warning('Portable startup acknowledgement could not be saved')
        return False


class UnavailableUpdates:
    """Keeps offline/chat usable when update configuration is unavailable, or during smoke tests."""
    def __init__(self, message: str, version: str = '', *, disabled: bool = True):
        self._message, self._version, self._disabled = message, version, disabled

    def status(self):
        return {'current_version': self._version, 'latest_version': '', 'phase': 'idle', 'progress': 0,
                'auto_download': True, 'message': self._message, 'notes': '', 'disabled': self._disabled}

    def start(self):
        pass

    def stop(self):
        pass

    def check_async(self):
        pass

    def download_async(self):
        pass

    def set_auto_download(self, value):
        pass

    def has_pending(self):
        return False

    def launch_pending(self):
        return False


def create_update_manager(directory: Path):
    from desktop.updater import UpdateManager
    try:
        return UpdateManager(load_update_config(), directory)
    except (OSError, ValueError, TypeError, KeyError):
        logging.warning('Bundled update configuration could not be loaded')
        return UnavailableUpdates('更新配置不可用，请重新下载官方便携版。聊天和本机数据仍可正常使用。')


class UpdateBridge:
    """Five narrow methods; all privileged collaborators stay private to pywebview reflection."""
    def __init__(self, manager, restart_callback):
        self._manager = manager
        self._restart_callback = restart_callback
        self._restart_lock = threading.Lock()

    def status(self):
        value = self._manager.status()
        result = {key: value.get(key, '') for key in
                  ('current_version', 'latest_version', 'phase', 'progress', 'auto_download', 'message', 'notes')}
        result['disabled'] = bool(value.get('disabled', False))
        result['can_restart'] = bool(getattr(sys, 'frozen', False)) and result['phase'] == 'ready'
        return result

    def check(self):
        self._manager.check_async()
        return self.status()

    def download(self):
        self._manager.download_async()
        return self.status()

    def set_auto_download(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('自动下载开关须为布尔值')
        self._manager.set_auto_download(enabled)
        return self.status()

    def restart(self):
        if not self._restart_lock.acquire(blocking=False):
            return False
        try:
            if not self._manager.launch_pending():
                return False
            self._restart_callback()
            return True
        finally:
            self._restart_lock.release()


UPDATE_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'">
<title>小伴 · 检查更新</title><style>
:root{font-family:"Microsoft YaHei UI",system-ui,sans-serif;color:#253c35;background:#f8f6ef;color-scheme:light}
*{box-sizing:border-box}body{max-width:650px;margin:0 auto;padding:30px}header{display:flex;gap:14px;align-items:center}.mark{background:#deeae0;padding:13px;border-radius:18px;font-size:25px}h1{font-size:23px;margin:0 0 7px}p{line-height:1.7;margin:7px 0;color:#5f6c64}.eyebrow{font-size:12px;letter-spacing:2px;color:#697d70}.card{padding:20px 22px;background:#fffdf8;border:1px solid #e3e6dc;border-radius:20px;margin:22px 0 16px}.versions{display:flex;gap:32px;margin-bottom:20px}.versions span{font-size:12px;color:#6a776e}.versions strong{display:block;font-size:21px;font-weight:600;margin-top:4px;min-height:28px}#message{min-height:25px;color:#314e40;font-size:14px}progress{width:100%;height:9px;accent-color:#46765a;margin:14px 0 3px}#progress-label{font-size:12px;text-align:right;color:#7b857c}#notes{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;color:#59695f;max-height:130px;overflow:auto;border-top:1px solid #eeeee5;padding-top:12px;margin-top:16px}label{display:flex;align-items:center;gap:9px;font-size:14px}input{accent-color:#46765a;width:17px;height:17px}.hint{font-size:12px;margin-top:10px}.buttons{display:flex;gap:10px;flex-wrap:wrap;margin-top:22px}button{border:1px solid #d6dfd2;border-radius:12px;padding:11px 17px;background:#fffdf8;color:#365b43;font:inherit;font-size:14px;cursor:pointer}button.primary{background:#3d6b50;color:white;border-color:#3d6b50}button:disabled{opacity:.45;cursor:default}button:focus-visible,input:focus-visible{outline:3px solid #a9c1aa;outline-offset:3px}.footer{font-size:12px;margin-top:18px;color:#7b857c}#error{color:#a33e35;font-size:13px;min-height:20px;margin-top:10px}
</style></head><body>
<header><div class="mark" aria-hidden="true">伴</div><div><div class="eyebrow">让陪伴持续变好</div><h1>小伴 · 检查更新</h1><p style="font-size:13px;margin:0">更新会在后台准备好，聊天继续。</p></div></header>
<section class="card"><div class="versions"><div><span>当前版本</span><strong id="current">—</strong></div><div><span>最新版本</span><strong id="latest">—</strong></div></div>
<div id="message" role="status" aria-live="polite">正在读取更新状态…</div>
<progress id="progress" value="0" max="100" aria-label="更新下载进度"></progress><div id="progress-label"></div>
<div id="notes" hidden></div></section>
<label><input type="checkbox" id="auto" checked>自动下载新版本</label>
<p class="hint">启动时自动检查，之后每 6 小时检查一次。下载完成后，下次启动自动更新；也可以点击“重启更新”。更新期间保留账户和本机记录。</p>
<div class="buttons"><button id="check">立即检查</button><button id="download" hidden>下载更新</button><button id="restart" class="primary" hidden>重启更新</button></div>
<div id="error" role="alert"></div><p class="footer">更新连接不可用时，可以继续使用当前版本。</p>
<script>
(()=>{'use strict';let api=null,busy=false;
const el=id=>document.getElementById(id);
function render(s){
 el('current').textContent=s.current_version?'v'+s.current_version:'未配置';
 el('latest').textContent=s.latest_version?'v'+s.latest_version:'—';
 el('message').textContent=String(s.message||'等待检查更新');
 el('notes').textContent=String(s.notes||'');el('notes').hidden=!s.notes;
 const progress=Math.min(100,Math.max(0,Number(s.progress)||0));el('progress').value=progress;
 el('progress-label').textContent=s.phase==='downloading'?Math.round(progress)+'%':'';
 el('auto').checked=s.auto_download===true;el('auto').disabled=!!s.disabled;
 el('download').hidden=s.phase!=='available';el('restart').hidden=!s.can_restart;
 el('check').disabled=!!s.disabled||['checking','downloading','applying'].includes(s.phase);
 el('download').disabled=busy;el('restart').disabled=busy;
}
async function call(name,...args){if(!api||busy)return;busy=true;el('error').textContent='';
 try{const result=await api[name](...args);busy=false;if(name==='restart'&&!result)el('error').textContent='暂时未能启动更新。请检查更新状态后重试。';
 if(result&&typeof result==='object')render(result);}catch(e){el('error').textContent='更新操作未完成，可以稍后重试。';}finally{busy=false;}}
el('check').onclick=()=>call('check');el('download').onclick=()=>call('download');
el('auto').onchange=()=>call('set_auto_download',el('auto').checked);
el('restart').onclick=()=>call('restart');
window.addEventListener('pywebviewready',()=>{api=window.pywebview.api;call('status');setInterval(()=>{if(!busy)call('status');},700);});
})();</script></body></html>'''


class UpdateWindowController:
    def __init__(self, webview, manager, restart_callback):
        self._webview, self._manager = webview, manager
        self._restart_callback = restart_callback
        self._window = None
        self._lock = threading.RLock()

    @property
    def window(self):
        return self._window

    def show(self):
        with self._lock:
            if self._window is not None:
                self._window.show()
                self._window.restore()
            else:
                self._window = self._webview.create_window(
                    '小伴 · 检查更新', html=UPDATE_HTML,
                    js_api=UpdateBridge(self._manager, self._restart_callback), width=660, height=640,
                    min_size=(540, 560), background_color='#F8F6EF', text_select=True)
                self._window.events.closed += self._forget
        self._manager.check_async()
        return self._window

    def _forget(self):
        with self._lock:
            self._window = None

    def close(self):
        with self._lock:
            window, self._window = self._window, None
        if window is not None:
            window.destroy()
