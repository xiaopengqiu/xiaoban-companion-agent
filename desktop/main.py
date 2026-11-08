"""GUI entry point. No Python installation is required by the frozen EXE."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time

from desktop.runtime import LocalServer, Settings, SingleInstance, data_directory, validate_url


def self_test(report: Path) -> None:
    from datetime import datetime, timedelta, timezone
    import httpx
    result = {'status': 'failed', 'frozen': bool(getattr(sys, 'frozen', False)), 'checks': []}
    with tempfile.TemporaryDirectory(prefix='xiaoban-frozen-smoke-') as directory:
        service = LocalServer(Path(directory))
        try:
            url = service.start()
            result['port'] = service.port
            with httpx.Client(base_url=url, trust_env=False, timeout=20) as client:
                def request(method, path, **kwargs):
                    response = client.request(method, path, **kwargs)
                    response.raise_for_status()
                    return response.json()

                assert client.get('/api/health').json()['mode'] == 'local'
                assert '小伴' in client.get('/').text
                for path in ['/app.js', '/lifecycle.mjs', '/companion-ui.mjs']:
                    asset = client.get(path)
                    assert asset.status_code == 200
                    assert 'javascript' in asset.headers.get('content-type', '')
                    assert asset.text.strip()
                assert 'memoryGroups' in client.get('/companion-ui.mjs').text
                result['checks'].append('bundled-js-modules')
                auth = request('POST', '/api/demo', json={})
                headers = {'Authorization': 'Bearer ' + auth['token']}
                assert client.get('/api/dashboard', headers=headers).status_code == 200

                memory = request('POST', '/api/memories', headers=headers,
                                 json={'text': '冻结自检：这周在筹备紫色风筝展', 'category': 'context'})
                assert memory['is_active'] is True
                assert datetime.fromisoformat(memory['expires_at']) - datetime.fromisoformat(memory['created_at']) == timedelta(days=7)
                expired_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
                expired = request('PATCH', '/api/memories/' + memory['id'], headers=headers,
                                  json={'expires_at': expired_at})
                assert expired['is_active'] is False
                reply = request('POST', '/api/chat', headers=headers, json={'message': '我之前筹备的紫色风筝展是什么？'})
                assert reply['mode'] == 'local'
                assert '紫色风筝展' not in reply['reply']
                renewed = request('PATCH', '/api/memories/' + memory['id'], headers=headers,
                                  json={'expires_at': None})
                assert renewed['is_active'] is True and renewed['expires_at'] is None
                result['checks'].append('context-default-expiry-inactive-retrieval-renewal')

                thread = request('POST', '/api/threads', headers=headers,
                                 json={'title': '冻结自检：准备自我介绍', 'details': '仅有合成数据，不需要外部模型'})
                assert thread['status'] == 'active' and thread['revision'] == 1
                assert thread['follow_up_enabled'] is False
                paused = request('PATCH', '/api/threads/' + thread['id'], headers=headers, json={'status': 'paused'})
                assert paused['status'] == 'paused' and paused['revision'] == 2
                completed = request('PATCH', '/api/threads/' + thread['id'], headers=headers,
                                    json={'status': 'completed', 'outcome': '合成验收已完成'})
                assert completed['status'] == 'completed' and completed['revision'] == 3
                preferences = {'support_style': 'listen', 'life_followups_enabled': False, 'model_consent': False}
                saved = request('PATCH', '/api/preferences', headers=headers, json=preferences)
                assert all(saved[key] == value for key, value in preferences.items())
                result['checks'].append('thread-create-pause-complete-and-preferences')

            service.stop()
            assert not service.running
            service = LocalServer(Path(directory))
            url = service.start()
            result['restart_port'] = service.port
            assert result['restart_port'] == result['port']
            with httpx.Client(base_url=url, trust_env=False, timeout=20, headers=headers) as client:
                rows = client.get('/api/threads').json()['items']
                persisted = next(item for item in rows if item['id'] == thread['id'])
                assert persisted == completed
                saved = client.get('/api/preferences').json()
                assert all(saved[key] == value for key, value in preferences.items())
                rows = client.get('/api/memories').json()['items']
                persisted_memory = next(item for item in rows if item['id'] == memory['id'])
                assert persisted_memory['is_active'] is True and persisted_memory['expires_at'] is None
                assert len(client.get('/api/chat').json()['items']) >= 2
                result['checks'].append('restart-sqlite-thread-memory-preferences-chat-state')
                assert client.delete('/api/threads/' + thread['id']).status_code == 200
                assert all(item['id'] != thread['id'] for item in client.get('/api/threads').json()['items'])
                assert client.delete('/api/me').status_code == 200
                assert client.get('/api/me').status_code == 401
                result['checks'].append('thread-delete-account-cleanup')
            result.update(status='passed', resources='web/index.html + app.js + lifecycle.mjs + companion-ui.mjs',
                          local_http='health/demo/dashboard/context-expiry/threads/preferences/chat/restart/delete')
        finally:
            service.stop()
            result['server_stopped'] = not service.running
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--ui-smoke', action='store_true')
    parser.add_argument('--reuse-login', action='store_true')
    args = parser.parse_args()
    directory = args.data_dir or (Path(tempfile.mkdtemp(prefix='xiaoban-ui-smoke-')) if args.ui_smoke else data_directory())
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=directory / 'desktop.log', encoding='utf-8', level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(message)s')
    if args.self_test:
        self_test(args.report or directory / 'self-test.json')
        return 0
    instance = SingleInstance(directory)
    if not instance.acquire():
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '小伴已在运行，请切回现有窗口继续使用。', '小伴', 0x40)
        return 0
    # Some dependencies assume stdout/stderr exist, including a windowed EXE.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')
    import webview
    from webview.menu import Menu, MenuAction
    service = LocalServer(directory)
    window = None
    ui_result = {'status': 'failed', 'frozen': bool(getattr(sys, 'frozen', False))}
    try:
        local_url = service.start()
        ui_result['port'] = service.port
        window = webview.create_window('小伴 · 本机数据（离线）', local_url, width=1180, height=830,
                                       min_size=(880, 640), background_color='#F8F6EF')

        def local():
            window.set_title('小伴 · 本机数据（离线）')
            window.load_url(validate_url(local_url, allow_loopback=True))

        def online():
            url = Settings(directory).public_url
            if not url:
                configure()
                return
            window.set_title('小伴 · 在线账户（云端）')
            window.load_url(validate_url(url))

        def configure():
            window.set_title('小伴 · 数据域与在线地址设置')
            window.load_url(f'{local_url}/desktop/settings?key={service.settings_key}')

        menu = [Menu('数据与账户', [MenuAction('本机数据（离线）', local), MenuAction('在线账户（云端）', online),
                                MenuAction('在线地址 / 设备说明', configure)]), Menu('窗口', [MenuAction('退出小伴', window.destroy)])]
        window.events.closed += service.stop
        def ui_check():
            def wait_js(script, timeout=30):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        if window.evaluate_js(script):
                            return
                    except Exception:
                        pass
                    time.sleep(.2)
                raise RuntimeError('WebView2 UI check timed out')
            try:
                if args.reuse_login:
                    wait_js("document.body.innerText.includes('小林 · 合成数据')")
                    ui_result['restored_login'] = True
                else:
                    wait_js("!!document.querySelector('#demo-start')")
                    window.evaluate_js("document.querySelector('#demo-start').click()")
                wait_js("document.body.innerText.includes('小林 · 合成数据')")
                configure()
                wait_js("document.body.innerText.includes('在线账户与本机数据')")
                local()
                wait_js("document.body.innerText.includes('小林 · 合成数据')")
                ui_result.update(status='passed', gui='WebView2', checks=['login', 'demo-dashboard', 'settings-menu', 'return-local-login-preserved'])
            except Exception as error:
                ui_result['error_type'] = type(error).__name__
            finally:
                window.destroy()

        webview.start(func=ui_check if args.ui_smoke else None, gui='edgechromium', menu=menu, debug=False, private_mode=False,
                      storage_path=str(directory / 'webview'))
        if args.ui_smoke:
            return 0 if ui_result['status'] == 'passed' else 1
        return 0
    except Exception as error:
        logging.error('Desktop startup failed: %s', type(error).__name__)
        if sys.platform == 'win32' and not args.ui_smoke:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '小伴未能启动。请确认 Microsoft Edge WebView2 Runtime 已安装。\n诊断日志：' + str(directory / 'desktop.log'), '小伴', 0x10)
        return 1
    finally:
        service.stop()
        instance.close()
        if args.ui_smoke:
            ui_result['server_stopped'] = not service.running
            report = args.report or directory / 'ui-smoke.json'
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(ui_result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
