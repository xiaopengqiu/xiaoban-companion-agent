"""Trust-chain and failure-path tests; no real account, network or installation."""
import base64
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import threading
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def module(name='desktop.updater'):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        pytest.fail('Signed portable update engine is not implemented: ' + name)


@pytest.fixture
def release():
    key = Ed25519PrivateKey.generate()
    config = {'schema': 1, 'version': '0.3.99',
              'feed_url': 'https://updates.example/api/desktop-updates/latest',
              'public_key': base64.b64encode(key.public_key().public_bytes(
                  serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()}
    body = b'MZ' + b'signed new executable' * 100
    payload = {'schema': 1, 'version': '0.4.0', 'platform': 'windows-x64',
               'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
               'published_at': '2026-11-08T08:00:00.000Z', 'notes': '更新：陪伴体验'}
    payload['url'] = 'https://updates.example/api/desktop-updates/assets/0.4.0/' + payload['sha256'] + '.exe'

    def signed(value=None):
        value = copy.deepcopy(payload if value is None else value)
        canonical = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        return {'payload': value, 'signature': base64.b64encode(key.sign(canonical)).decode()}

    return config, body, payload, signed


def finish(manager, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not manager.busy:
            return manager.status()
        time.sleep(.01)
    pytest.fail('update operation did not finish within test deadline')


def test_signed_unicode_manifest_and_semantic_versions(release):
    core = module()
    config, _, payload, signed = release
    assert core.verify_manifest(config, signed()) == payload
    assert core.version_tuple('0.10.0') > core.version_tuple('0.9.99')
    tampered = signed()
    tampered['payload']['notes'] = 'altered'
    with pytest.raises(ValueError, match='签名'):
        core.verify_manifest(config, tampered)


@pytest.mark.parametrize('change', [
    {'schema': 2}, {'schema': True}, {'platform': 'linux-x64'}, {'version': '0.3.98'},
    {'version': '00.4.0'}, {'version': '0.4.0-beta'}, {'size': 0}, {'size': True},
    {'size': 64 * 1024 * 1024 + 1}, {'sha256': 'A' * 64}, {'extra': 'untrusted'},
    {'notes': 'x' * 2001}, {'published_at': 'yesterday'},
    {'url': 'https://evil.example/new.exe'},
    {'url': 'http://updates.example/api/desktop-updates/assets/0.4.0/new.exe'},
    {'url': 'https://user:pass@updates.example/new.exe'},
    {'url': 'https://updates.example/api/desktop-updates/assets/0.4.0/../new.exe'},
    {'url': 'https://updates.example/api/desktop-updates/assets/0.4.0/new.exe?token=x'},
])
def test_even_signed_invalid_release_is_rejected(release, change):
    config, _, payload, signed = release
    payload.update(change)
    with pytest.raises(ValueError):
        module().verify_manifest(config, signed(payload))


def test_download_stages_verified_bytes_and_persists_only_update_state(release, tmp_path):
    config, body, _, signed = release
    calls = []

    def serve(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=signed()) if request.url.path.endswith('latest') else httpx.Response(200, content=body)

    (tmp_path / 'companion.sqlite3').write_bytes(b'private database sentinel')
    manager = module().UpdateManager(config, tmp_path, _transport=httpx.MockTransport(serve))
    assert manager.check_async() is True
    assert finish(manager)['phase'] == 'ready'
    assert manager.status()['progress'] == 100
    assert manager.has_pending()
    assert len(calls) == 2
    assert (tmp_path / 'companion.sqlite3').read_bytes() == b'private database sentinel'
    assert set(p.name for p in tmp_path.iterdir()) == {'companion.sqlite3', 'updates'}
    assert list((tmp_path / 'updates').rglob('*.exe'))[0].read_bytes() == body
    assert not list((tmp_path / 'updates').rglob('*.part'))
    manager.set_auto_download(False)
    restored = module().UpdateManager(config, tmp_path)
    assert restored.status()['auto_download'] is False
    assert restored.has_pending()


@pytest.mark.parametrize('broken', ['truncated', 'hash', 'oversized', 'redirect'])
def test_bad_download_never_becomes_pending_and_part_is_removed(release, tmp_path, broken):
    config, body, _, signed = release

    def serve(request):
        if request.url.path.endswith('latest'):
            return httpx.Response(200, json=signed())
        if broken == 'redirect':
            return httpx.Response(302, headers={'Location': 'https://evil.example/new.exe'})
        invalid = body[:-1] if broken == 'truncated' else (b'x' + body[1:] if broken == 'hash' else body + b'extra')
        return httpx.Response(200, content=invalid)

    manager = module().UpdateManager(config, tmp_path, _transport=httpx.MockTransport(serve))
    manager.check_async()
    assert finish(manager)['phase'] == 'error'
    assert not manager.has_pending()
    assert not list((tmp_path / 'updates').rglob('*.part'))
    assert not list((tmp_path / 'updates').rglob('*.exe'))


def test_manual_download_and_offline_check_do_not_modify_user_files(release, tmp_path):
    config, body, _, signed = release

    def serve(request):
        return httpx.Response(200, json=signed()) if request.url.path.endswith('latest') else httpx.Response(200, content=body)

    manager = module().UpdateManager(config, tmp_path, _transport=httpx.MockTransport(serve))
    manager.set_auto_download(False)
    manager.check_async()
    assert finish(manager)['phase'] == 'available'
    assert not manager.has_pending()
    assert manager.download_async()
    assert finish(manager)['phase'] == 'ready'
    staged = next((tmp_path / 'updates').rglob('*.exe'))
    staged.write_bytes(b'tampered after download')
    assert not manager.has_pending()

    def offline(request):
        raise httpx.ConnectError('offline', request=request)

    offline_manager = module().UpdateManager(config, tmp_path / 'offline', _transport=httpx.MockTransport(offline))
    offline_manager.check_async()
    assert finish(offline_manager)['phase'] == 'error'


def test_background_operations_never_overlap_and_shutdown_cancels(release, tmp_path):
    import asyncio
    config, _, _, _ = release
    entered = threading.Event()

    async def serve(request):
        entered.set()
        await asyncio.sleep(30)
        return httpx.Response(404)

    manager = module().UpdateManager(config, tmp_path, _transport=httpx.MockTransport(serve))
    assert manager.check_async()
    assert entered.wait(1)
    assert manager.check_async() is False
    assert manager.download_async() is False
    began = time.monotonic()
    manager.stop()
    assert time.monotonic() - began < 2
    assert not manager.busy


def test_network_has_total_deadline_even_if_peer_keeps_responding(release, tmp_path, monkeypatch):
    import asyncio
    core = module()
    config, _, _, _ = release
    monkeypatch.setattr(core, 'CHECK_DEADLINE', .05)

    async def serve(request):
        await asyncio.sleep(.2)
        return httpx.Response(404)

    manager = core.UpdateManager(config, tmp_path, _transport=httpx.MockTransport(serve))
    manager.check_async()
    assert finish(manager)['phase'] == 'error'


def test_source_python_can_never_launch_replacement(release, tmp_path, monkeypatch):
    config, _, _, _ = release
    monkeypatch.delattr(sys, 'frozen', raising=False)
    manager = module().UpdateManager(config, tmp_path, executable=Path(sys.executable))
    assert manager.launch_pending() is False
    assert manager.status()['phase'] == 'error'


@pytest.fixture
def apply_case(release, tmp_path):
    config, body, _, signed = release
    data = tmp_path / 'profile'
    updates = data / 'updates'
    updates.mkdir(parents=True)
    target = tmp_path / 'portable' / '小伴.exe'
    target.parent.mkdir()
    old = b'MZtrusted previous program'
    target.write_bytes(old)
    staged = updates / 'candidate.exe'
    staged.write_bytes(body)
    helper = updates / 'helper.exe'
    helper.write_bytes(old)
    (data / 'database.sqlite3').write_bytes(b'keep private data')
    request = {'schema': 1, 'data_dir': str(data), 'target': str(target), 'staged': str(staged),
               'helper': str(helper), 'manifest': signed(), 'old_sha256': hashlib.sha256(old).hexdigest(),
               'processes': []}
    path = updates / 'apply-request.json'
    path.write_text(json.dumps(request), encoding='utf-8')
    return config, request, path, target, old


def run_apply(case, **kwargs):
    config, _, path, _, _ = case
    return module('desktop.update_apply').apply_update_request(
        path, _config=config, _wait=lambda *args: None, _probe=lambda *args: None,
        _restart=kwargs.pop('_restart', lambda *args: None), _test_mode=True, **kwargs)


def test_helper_replaces_after_probe_and_preserves_backup_and_profile(apply_case, release):
    _, request, path, target, old = apply_case
    restarts = []
    assert run_apply(apply_case, _restart=lambda *args: restarts.append(args)) == 0
    assert target.read_bytes() == release[1]
    assert target.with_name(target.name + '.rollback').read_bytes() == old
    assert (Path(request['data_dir']) / 'database.sqlite3').read_bytes() == b'keep private data'
    assert restarts and restarts[0][0] == target
    result = json.loads((path.parent / 'result.json').read_text(encoding='utf-8'))
    assert result['status'] == 'applied'


@pytest.mark.parametrize('change', ['target', 'staged', 'signature', 'outside', 'helper'])
def test_helper_rejects_changed_or_untrusted_inputs_without_touching_target(apply_case, change):
    _, request, path, target, old = apply_case
    if change == 'target':
        target.write_bytes(b'other application')
    elif change == 'staged':
        Path(request['staged']).write_bytes(b'wrong download')
    elif change == 'signature':
        request['manifest']['payload']['notes'] = 'tampered'
    elif change == 'outside':
        request['staged'] = str(target)
    elif change == 'helper':
        Path(request['helper']).write_bytes(b'untrusted helper')
    path.write_text(json.dumps(request), encoding='utf-8')
    expected = target.read_bytes()
    assert run_apply(apply_case) != 0
    assert target.read_bytes() == expected


def test_failed_probe_keeps_old_version_and_suppresses_retry(apply_case):
    config, _, path, target, old = apply_case

    def fail(*args):
        raise RuntimeError('self test failed')

    result = module('desktop.update_apply').apply_update_request(
        path, _config=config, _wait=lambda *args: None, _probe=fail,
        _restart=lambda *args: None, _test_mode=True)
    assert result != 0
    assert target.read_bytes() == old
    assert json.loads((path.parent / 'state.json').read_text(encoding='utf-8'))['failed_version'] == '0.4.0'
    failure = json.loads((path.parent / 'result.json').read_text(encoding='utf-8'))
    assert failure['stage'] == 'probe'
    assert failure['error_type'] == 'RuntimeError'
    assert failure['winerror'] is None


def test_restart_failure_rolls_back_and_restarts_old_with_skip_flag(apply_case):
    _, _, _, target, old = apply_case
    calls = []

    def restart(*args):
        calls.append(args)
        if len(calls) == 1:
            raise OSError('failed to launch new program')

    assert run_apply(apply_case, _restart=restart) != 0
    assert target.read_bytes() == old
    assert len(calls) == 2
    assert calls[-1][2] is True


def test_same_portable_executable_serializes_updates_from_two_profiles(apply_case, release, monkeypatch, tmp_path):
    apply = module('desktop.update_apply')
    config, request_a, path_a, target, _ = apply_case
    _, _, payload, signed = release
    second = tmp_path / 'second-profile' / 'updates'
    second.mkdir(parents=True)
    request_b = copy.deepcopy(request_a)
    request_b['data_dir'] = str(second.parent)
    request_b['helper'] = str(second / 'helper.exe')
    request_b['staged'] = str(second / 'candidate.exe')
    Path(request_b['helper']).write_bytes(Path(request_a['helper']).read_bytes())
    newer = b'MZsigned even newer program'
    Path(request_b['staged']).write_bytes(newer)
    payload.update(version='0.5.0', sha256=hashlib.sha256(newer).hexdigest(), size=len(newer))
    payload['url'] = 'https://updates.example/api/desktop-updates/assets/0.5.0/' + payload['sha256'] + '.exe'
    request_b['manifest'] = signed(payload)
    path_b = second / 'apply.json'
    path_b.write_text(json.dumps(request_b), encoding='utf-8')
    paused, resume = threading.Event(), threading.Event()
    original = apply.shutil.copyfile

    def pause(source, destination):
        if Path(source) == Path(request_a['staged']):
            paused.set()
            assert resume.wait(3)
        return original(source, destination)

    monkeypatch.setattr(apply.shutil, 'copyfile', pause)
    results = []
    first = threading.Thread(target=lambda: results.append(run_apply(apply_case)))
    first.start()
    assert paused.wait(2)
    other = threading.Thread(target=lambda: results.append(apply.apply_update_request(
        path_b, _config=config, _wait=lambda *args: None, _probe=lambda *args: None,
        _restart=lambda *args: None, _test_mode=True)))
    other.start()
    time.sleep(.1)
    assert other.is_alive(), 'second helper must wait for the install-path lock'
    resume.set()
    first.join(3)
    other.join(3)
    assert sorted(results) == [0, 1]
    assert target.read_bytes() == release[1]


def test_probe_rejects_executable_with_wrong_embedded_version(tmp_path, monkeypatch):
    apply = module('desktop.update_apply')
    import companion.model_runtime as runtime

    class Job:
        def attach_and_resume(self, process):
            pass

        def close(self):
            pass

    class Process:
        def __init__(self, command, **kwargs):
            report = Path(command[command.index('--report') + 1])
            report.write_text(json.dumps({'status': 'passed', 'frozen': True,
                                         'server_stopped': True, 'update_version': '0.3.99'}), encoding='utf-8')

        def wait(self, **kwargs):
            return 0

    monkeypatch.setattr(runtime, '_WindowsJob', Job)
    monkeypatch.setattr(apply.subprocess, 'Popen', Process)
    with pytest.raises(ValueError, match='版本'):
        apply._probe(tmp_path / 'candidate.exe', tmp_path, '0.4.0')


def test_restart_waits_for_real_ready_ack_and_kills_only_owned_tree_on_failure(tmp_path, monkeypatch):
    apply = module('desktop.update_apply')
    import companion.model_runtime as runtime
    closed = []

    class Job:
        def attach_and_resume(self, process):
            pass

        def close(self):
            closed.append(True)

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return None

        def wait(self, **kwargs):
            return 0

    monkeypatch.setattr(runtime, '_WindowsJob', Job)
    monkeypatch.setattr(apply.subprocess, 'Popen', Process)
    monkeypatch.setattr(apply, 'STARTUP_DEADLINE', .05, raising=False)
    (tmp_path / 'updates').mkdir()
    with pytest.raises(TimeoutError):
        apply._restart(tmp_path / 'portable.exe', tmp_path, False)
    assert closed == [True]


def test_deeply_nested_feed_becomes_actionable_error_not_stuck_checking(release, tmp_path):
    config, _, _, _ = release
    nested = '{"payload":' + '[' * 10000 + '0' + ']' * 10000 + ',"signature":"invalid"}'
    manager = module().UpdateManager(config, tmp_path, _transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=nested.encode())))
    manager.check_async()
    assert finish(manager)['phase'] == 'error'


def test_result_write_failure_after_ready_does_not_roll_back_live_new_app(apply_case, release, monkeypatch):
    apply = module('desktop.update_apply')
    original = apply.atomic_json
    calls = []

    def fail_result(path, value):
        if path.name == 'result.json':
            raise OSError('disk became read-only after GUI readiness')
        return original(path, value)

    monkeypatch.setattr(apply, 'atomic_json', fail_result)
    assert run_apply(apply_case, _restart=lambda *args: calls.append(args)) == 0
    assert apply_case[3].read_bytes() == release[1]
    assert len(calls) == 1


def test_stream_download_total_deadline_cleans_partial_file(release, tmp_path, monkeypatch):
    import asyncio
    core = module()
    config, body, _, signed = release
    monkeypatch.setattr(core, 'DOWNLOAD_DEADLINE', .05)

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body[:10]
            await asyncio.sleep(.3)
            yield body[10:]

    def serve(request):
        if request.url.path.endswith('latest'):
            return httpx.Response(200, json=signed())
        return httpx.Response(200, headers={'Content-Length': str(len(body))}, stream=SlowBody())

    manager = core.UpdateManager(config, tmp_path, _transport=httpx.MockTransport(serve))
    manager.check_async()
    assert finish(manager)['phase'] == 'error'
    assert not manager.has_pending()
    assert not list((tmp_path / 'updates').rglob('*.part'))


def test_scheduler_starts_once_uses_six_hour_interval_and_stops(release, tmp_path, monkeypatch):
    core = module()
    assert core.CHECK_INTERVAL == 6 * 60 * 60
    config, _, _, _ = release
    monkeypatch.setattr(core, 'INITIAL_DELAY', .01)
    monkeypatch.setattr(core, 'CHECK_INTERVAL', .1)
    calls, third = [], threading.Event()

    def serve(request):
        calls.append(str(request.url))
        if len(calls) == 3:
            third.set()
        return httpx.Response(404)

    manager = core.UpdateManager(config, tmp_path, _transport=httpx.MockTransport(serve))
    manager.start()
    manager.start()
    assert third.wait(2)
    manager.stop()
    assert not manager.busy
    count = len(calls)
    time.sleep(.15)
    assert len(calls) == count == 3


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows retained process-handle regression')
def test_wait_accepts_exited_owner_even_when_another_handle_keeps_pid_alive():
    import subprocess
    apply = module('desktop.update_apply')
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.3)'],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    api, handle = apply._process_handle(process.pid)
    try:
        expected = apply._identity(api, handle, process.pid)
        process.wait(timeout=5)
        assert api.WaitForSingleObject(handle, 0) == 0
        # Popen and this handle deliberately still own the terminated process.
        # It may be openable by PID although image-name queries no longer work.
        apply.wait_for_owners([expected], Path(expected['exe']), deadline=.1)
    finally:
        api.CloseHandle(handle)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows live process identity regression')
def test_wait_still_rejects_live_process_with_different_creation_time():
    import subprocess
    apply = module('desktop.update_apply')
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    api, handle = apply._process_handle(process.pid)
    try:
        expected = apply._identity(api, handle, process.pid)
        expected['created'] += 1
        with pytest.raises(ValueError, match='身份'):
            apply.wait_for_owners([expected], Path(expected['exe']), deadline=.1)
        assert process.poll() is None
    finally:
        api.CloseHandle(handle)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows exit-during-query regression')
def test_wait_accepts_owner_that_exits_between_open_and_identity(monkeypatch):
    import subprocess
    apply = module('desktop.update_apply')
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    api, handle = apply._process_handle(process.pid)
    try:
        expected = apply._identity(api, handle, process.pid)
        original = apply._identity

        def exit_before_query(*args):
            process.kill()
            process.wait(timeout=5)
            return original(*args)

        monkeypatch.setattr(apply, '_identity', exit_before_query)
        apply.wait_for_owners([expected], Path(expected['exe']), deadline=.1)
    finally:
        api.CloseHandle(handle)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
