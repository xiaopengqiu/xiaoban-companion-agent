"""Desktop lifecycle and native update-window boundaries, without public networking."""
import importlib
import sys
from types import SimpleNamespace

import pytest

from desktop import main


def test_apply_helper_dispatches_before_any_normal_app_initialization(monkeypatch, tmp_path):
    request = tmp_path / 'request.json'
    calls = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['xiaoban', '--apply-update', str(request)])
    monkeypatch.setitem(sys.modules, 'desktop.update_apply', SimpleNamespace(
        apply_update_request=lambda value: calls.append(value) or 7))
    monkeypatch.setattr(main, 'data_directory', lambda: pytest.fail('helper initialized app data'))
    monkeypatch.setattr(main, 'SingleInstance', lambda *_: pytest.fail('helper acquired app mutex'))
    monkeypatch.setattr(main, 'LocalServer', lambda *_: pytest.fail('helper initialized local server'))
    assert main.main() == 7
    assert calls == [request]


def test_source_process_cannot_be_used_as_an_executable_replacement_helper(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    monkeypatch.setattr(sys, 'argv', ['xiaoban', '--apply-update', str(tmp_path / 'request.json')])
    monkeypatch.setitem(sys.modules, 'desktop.update_apply', SimpleNamespace(
        apply_update_request=lambda *_: pytest.fail('source process invoked replacement')))
    monkeypatch.setattr(main, 'data_directory', lambda: pytest.fail('source helper initialized app'))
    assert main.main() == 1


class FakeManager:
    def __init__(self, *, pending=False, launch=True):
        self.calls = []
        self.pending, self.launch = pending, launch
        self.value = {'current_version': '0.4.0', 'latest_version': '0.4.1', 'phase': 'ready',
                      'progress': 100, 'auto_download': True, 'message': '已就绪', 'notes': '<img src=x onerror=alert(1)>'}

    def status(self):
        return self.value.copy()

    def start(self):
        self.calls.append('start')

    def stop(self):
        self.calls.append('stop')

    def has_pending(self):
        self.calls.append('pending')
        return self.pending

    def launch_pending(self):
        self.calls.append('launch')
        return self.launch

    def check_async(self):
        self.calls.append('check')

    def download_async(self):
        self.calls.append('download')

    def set_auto_download(self, value):
        self.value['auto_download'] = value
        self.calls.append(('auto', value))


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, callback):
        self.handlers.append(callback)
        return self

    def wait(self, timeout=None):
        return True


class FakeWindow:
    def __init__(self, title, url=None, **kwargs):
        self.title, self.url, self.options = title, url, kwargs
        self.destroyed = False
        self.events = SimpleNamespace(closed=FakeEvent(), loaded=FakeEvent())

    def destroy(self):
        self.destroyed = True
        for callback in self.events.closed.handlers:
            callback()

    def show(self):
        pass

    def restore(self):
        pass

    def set_title(self, title):
        self.title = title

    def load_url(self, url):
        self.url = url

    def evaluate_js(self, script):
        return True


class FakeWebview:
    def __init__(self):
        self.windows = []
        self.start_options = None

    def create_window(self, *args, **kwargs):
        window = FakeWindow(*args, **kwargs)
        self.windows.append(window)
        return window

    def start(self, func=None, **kwargs):
        self.start_options = kwargs
        if func:
            func()


def install_shell(monkeypatch, tmp_path, manager, *arguments):
    ui = importlib.import_module('desktop.update_ui')
    webview = FakeWebview()
    calls = []
    class Service:
        port, running, settings_key = 43111, False, 'test'
        def __init__(self, directory):
            calls.append('server-created')
        def start(self):
            calls.append('server-started')
            self.running = True
            return 'http://127.0.0.1:43111'
        def stop(self):
            self.running = False
            calls.append('server-stopped')
    class Instance:
        def __init__(self, directory):
            calls.append('mutex-created')
        def acquire(self):
            return getattr(manager, 'instance_available', True)
        def close(self):
            calls.append('mutex-closed')
    monkeypatch.setattr(ui, 'create_update_manager', lambda directory: manager)
    monkeypatch.setattr(ui, 'load_update_config', lambda: {'version': '0.4.0'})
    monkeypatch.setattr(main, 'LocalServer', Service)
    monkeypatch.setattr(main, 'SingleInstance', Instance)
    monkeypatch.setattr(sys, 'argv', ['xiaoban', '--data-dir', str(tmp_path), *arguments])
    monkeypatch.setitem(sys.modules, 'webview', webview)
    monkeypatch.setitem(sys.modules, 'webview.menu', SimpleNamespace(
        Menu=lambda title, items: (title, items), MenuAction=lambda title, action: (title, action)))
    return ui, webview, calls


def test_verified_pending_update_launches_after_mutex_before_server_or_window(monkeypatch, tmp_path):
    manager = FakeManager(pending=True)
    _, webview, calls = install_shell(monkeypatch, tmp_path, manager)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert main.main() == 0
    assert manager.calls == ['pending', 'launch', 'stop']
    assert calls == ['mutex-created', 'mutex-closed']
    assert webview.windows == []


def test_second_instance_does_not_launch_pending_while_another_chat_is_running(monkeypatch, tmp_path):
    manager = FakeManager(pending=True)
    manager.instance_available = False
    _, webview, calls = install_shell(monkeypatch, tmp_path, manager)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    # Avoid a real MessageBox in this headless lifecycle test.
    monkeypatch.setattr(sys, 'platform', 'test')
    assert main.main() == 0
    assert manager.calls == ['stop']
    assert calls == ['mutex-created', 'mutex-closed']
    assert webview.windows == []


def test_failed_pending_launch_opens_app_and_starts_background_after_gui(monkeypatch, tmp_path):
    manager = FakeManager(pending=True, launch=False)
    _, webview, calls = install_shell(monkeypatch, tmp_path, manager)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    start = manager.start
    def after_window():
        assert len(webview.windows) == 1
        assert webview.start_options is not None
        start()
    manager.start = after_window
    assert main.main() == 0
    assert manager.calls == ['pending', 'launch', 'start', 'stop']
    assert 'server-started' in calls and calls[-1] == 'mutex-closed'
    assert 'js_api' not in webview.windows[0].options


def test_skip_once_does_not_launch_or_delete_pending_update(monkeypatch, tmp_path):
    manager = FakeManager(pending=True)
    _, webview, calls = install_shell(monkeypatch, tmp_path, manager, '--skip-update-once')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert main.main() == 0
    assert manager.calls == ['start', 'stop']
    assert len(webview.windows) == 1


def test_source_startup_never_applies_pending_to_python(monkeypatch, tmp_path):
    manager = FakeManager(pending=True)
    install_shell(monkeypatch, tmp_path, manager)
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert main.main() == 0
    assert manager.calls == ['start', 'stop']


def test_self_test_does_not_initialize_or_start_public_updater(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['xiaoban', '--self-test', '--data-dir', str(tmp_path)])
    monkeypatch.setattr(main, 'self_test', lambda *_: None)
    monkeypatch.setattr(main, 'SingleInstance', lambda *_: pytest.fail('self-test acquired normal mutex'))
    assert main.main() == 0


def test_self_test_without_explicit_data_dir_never_opens_user_profile(monkeypatch):
    paths = []
    monkeypatch.setattr(sys, 'argv', ['xiaoban', '--self-test'])
    monkeypatch.setattr(main, 'self_test', lambda report: paths.append(report))
    monkeypatch.setattr(main, 'data_directory', lambda: pytest.fail('self-test opened normal user profile'))
    assert main.main() == 0
    assert paths and 'xiaoban-' in str(paths[0].parent)


def test_packaged_health_report_validates_bundled_public_config_without_network(monkeypatch, tmp_path):
    import json
    manager = FakeManager()
    ui = importlib.import_module('desktop.update_ui')
    monkeypatch.setattr(ui, 'load_update_config', lambda: {'version': '0.4.0'})
    monkeypatch.setitem(sys.modules, 'desktop.updater', SimpleNamespace(UpdateManager=lambda config, directory: manager))
    report = tmp_path / 'health.json'
    main.self_test(report)
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['status'] == 'passed'
    assert result['update_version'] == '0.4.0'
    assert result['public_updates_disabled'] is True
    assert 'bundled-signed-update-config' in result['checks']
    assert manager.calls == []


def test_ui_smoke_exercises_update_window_without_initializing_public_updater(monkeypatch, tmp_path):
    report = tmp_path / 'ui-report.json'
    manager = FakeManager(pending=True)
    ui, webview, calls = install_shell(monkeypatch, tmp_path, manager, '--ui-smoke', '--report', str(report))
    monkeypatch.setattr(ui, 'create_update_manager', lambda *_: pytest.fail('smoke initialized public updater'))
    monkeypatch.setattr(ui, 'load_update_config', lambda: {'version': '0.4.0'})
    monkeypatch.setattr(ui.UnavailableUpdates, 'start', lambda *_: pytest.fail('smoke started network scheduling'))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert main.main() == 0
    assert manager.calls == []
    assert len(webview.windows) == 2 and webview.windows[1].destroyed
    import json
    evidence = json.loads(report.read_text(encoding='utf-8'))
    assert evidence['server_stopped']
    assert 'smoke-public-updates-disabled' in evidence['checks']


def test_closed_window_cannot_start_update_scheduler_after_shutdown(monkeypatch, tmp_path):
    manager = FakeManager()
    ui, webview, _ = install_shell(monkeypatch, tmp_path, manager)
    def close_before_callback(func=None, **kwargs):
        webview.windows[0].destroy()
        if func:
            func()
    webview.start = close_before_callback
    assert main.main() == 0
    assert manager.calls == ['stop']


def test_update_restart_ack_is_written_only_after_service_and_gui_are_ready(monkeypatch, tmp_path):
    manager = FakeManager()
    token = 'a' * 64
    path = tmp_path / 'updates' / ('startup-' + token + '.json')
    ui, webview, calls = install_shell(monkeypatch, tmp_path, manager,
                                      '--update-ack', str(path), '--update-ack-token', token)
    acknowledgements = []
    def acknowledge(directory, actual_path, actual_token, version):
        assert calls == ['mutex-created', 'server-created', 'server-started']
        assert webview.windows and webview.start_options is not None
        acknowledgements.append((directory, actual_path, actual_token, version))
        return True
    monkeypatch.setattr(ui, 'acknowledge_startup', acknowledge, raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert main.main() == 0
    assert acknowledgements == [(tmp_path, path, token, '0.4.0')]


def test_ack_writer_rejects_source_wrong_path_and_invalid_token(monkeypatch, tmp_path):
    ui = importlib.import_module('desktop.update_ui')
    assert hasattr(ui, 'acknowledge_startup'), 'startup readiness acknowledgements are missing'
    token = 'a' * 64
    path = tmp_path / 'updates' / ('startup-' + token + '.json')
    path.parent.mkdir()
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert ui.acknowledge_startup(tmp_path, path, token, '0.4.0') is False
    assert not path.exists()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert ui.acknowledge_startup(tmp_path, tmp_path / path.name, token, '0.4.0') is False
    assert ui.acknowledge_startup(tmp_path, path, 'short', '0.4.0') is False
    assert not path.exists()


def test_ack_writer_reports_current_frozen_version_and_process(monkeypatch, tmp_path):
    import json
    import os
    ui = importlib.import_module('desktop.update_ui')
    assert hasattr(ui, 'acknowledge_startup'), 'startup readiness acknowledgements are missing'
    token = 'b' * 64
    path = tmp_path / 'updates' / ('startup-' + token + '.json')
    path.parent.mkdir()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert ui.acknowledge_startup(tmp_path, path, token, '0.4.0') is True
    assert json.loads(path.read_text(encoding='utf-8')) == {
        'status': 'ready', 'version': '0.4.0', 'pid': os.getpid(), 'token': token}
    assert ui.acknowledge_startup(tmp_path, path, token, '0.4.0') is False


def test_update_menu_uses_independent_window_and_narrow_native_api(monkeypatch, tmp_path):
    manager = FakeManager()
    ui, webview, _ = install_shell(monkeypatch, tmp_path, manager)
    assert main.main() == 0
    main_window = webview.windows[0]
    main_url = main_window.url
    menus = dict(webview.start_options['menu'])
    dict(menus['窗口'])['检查更新']()
    assert len(webview.windows) == 2
    child = webview.windows[1]
    assert main_window.url == main_url
    assert 'js_api' not in main_window.options
    assert child.url is None and 'html' in child.options
    bridge = child.options['js_api']
    assert {name for name in dir(bridge) if not name.startswith('_')} == {
        'status', 'check', 'download', 'set_auto_download', 'restart'}
    assert manager.value['notes'] not in child.options['html']
    assert 'textContent' in child.options['html']
    assert 'innerHTML' not in child.options['html']
    bridge.check()
    bridge.download()
    bridge.set_auto_download(False)
    assert manager.calls[-3:] == ['check', 'download', ('auto', False)]
    with pytest.raises(ValueError):
        bridge.set_auto_download('false')


def test_restart_only_closes_application_after_helper_success(monkeypatch):
    ui = importlib.import_module('desktop.update_ui')
    manager = FakeManager(launch=False)
    calls = []
    bridge = ui.UpdateBridge(manager, lambda: calls.append('closed'))
    assert bridge.restart() is False
    assert calls == []
    manager.launch = True
    assert bridge.restart() is True
    assert calls == ['closed']


def test_update_window_reuses_existing_child_then_reopens_after_close():
    ui = importlib.import_module('desktop.update_ui')
    webview, manager = FakeWebview(), FakeManager()
    controller = ui.UpdateWindowController(webview, manager, lambda: None)
    controller.show()
    controller.show()
    assert len(webview.windows) == 1
    webview.windows[0].destroy()
    controller.show()
    assert len(webview.windows) == 2
    controller.close()
    assert webview.windows[-1].destroyed


@pytest.mark.parametrize('phase,button', [('ready', 'restart'), ('available', 'download')])
def test_update_action_buttons_are_enabled_after_async_status_poll(tmp_path, phase, button):
    import json
    import re
    import subprocess
    from desktop.update_ui import UPDATE_HTML
    script = re.search(r'<script>(.*?)</script>', UPDATE_HTML, re.S).group(1)
    state = FakeManager().value | {'phase': phase, 'can_restart': phase == 'ready'}
    harness = '''const vm = require('node:vm');const assert = require('node:assert/strict');
const elements = new Map();const handlers = {};
const window = {addEventListener:(name,fn)=>handlers[name]=fn,
  pywebview:{api:{status:async()=>(STATE)}}};
const document = {getElementById:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);}};
vm.runInNewContext(SCRIPT,{window,document,setInterval:()=>0});
handlers.pywebviewready();
setImmediate(()=>assert.equal(elements.get(BUTTON).disabled,false));
'''.replace('STATE', json.dumps(state, ensure_ascii=False)).replace('SCRIPT', json.dumps(script)).replace('BUTTON', json.dumps(button))
    path = tmp_path / 'buttons.cjs'
    path.write_text(harness, encoding='utf-8')
    result = subprocess.run(['node', str(path)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
