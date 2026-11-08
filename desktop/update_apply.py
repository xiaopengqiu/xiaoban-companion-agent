"""A trusted copy of the CURRENT executable applies an already signed release."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

from desktop.updater import (atomic_json, file_sha256, plain_path, read_json,
                             verify_file, verify_manifest, version_tuple, within)

WAIT_DEADLINE = 120
PROBE_DEADLINE = 120
STARTUP_DEADLINE = 90
_test_locks = {}
_test_locks_guard = threading.Lock()


class _TargetLock:
    """Installation-path mutex, independent of a user's selected data profile."""
    def __init__(self, target: Path):
        self.handle = None
        self.lock = None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes as w
            self.api = _windows_api()
            self.api.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
            self.api.CreateMutexW.restype = w.HANDLE
            self.api.ReleaseMutex.argtypes = [w.HANDLE]
            self.api.ReleaseMutex.restype = w.BOOL
            name = 'Local\\XiaobanPortableUpdate-' + hashlib.sha256(str(target).casefold().encode()).hexdigest()
            self.handle = self.api.CreateMutexW(None, False, name)
            if not self.handle:
                raise OSError('无法创建程序更新锁')
            result = self.api.WaitForSingleObject(self.handle, WAIT_DEADLINE * 1000)
            if result not in {0, 0x80}:
                self.api.CloseHandle(self.handle)
                self.handle = None
                raise TimeoutError('另一个更新正在安装，请稍后重试')
        else:
            # Isolated source tests only; production entry requires Windows.
            with _test_locks_guard:
                self.lock = _test_locks.setdefault(str(target), threading.Lock())
            if not self.lock.acquire(timeout=WAIT_DEADLINE):
                raise TimeoutError('另一个更新正在安装，请稍后重试')

    def close(self):
        if self.handle:
            self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle)
            self.handle = None
        if self.lock:
            self.lock.release()
            self.lock = None


def _windows_api():
    import ctypes
    from ctypes import wintypes as w
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    api.OpenProcess.restype = w.HANDLE
    api.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    api.QueryFullProcessImageNameW.restype = w.BOOL
    api.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    api.GetProcessTimes.restype = w.BOOL
    api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    api.WaitForSingleObject.restype = w.DWORD
    api.CloseHandle.argtypes = [w.HANDLE]
    api.CloseHandle.restype = w.BOOL
    return api


def _process_handle(pid: int):
    import ctypes
    api = _windows_api()
    handle = api.OpenProcess(0x1000 | 0x100000, False, pid)  # query limited + synchronize only
    if not handle:
        if ctypes.get_last_error() in {87, 1168}:  # already exited
            return api, None
        raise OSError(0, '无法验证待退出的小伴进程', None, ctypes.get_last_error())
    return api, handle


def _identity(api, handle, pid):
    import ctypes
    from ctypes import wintypes as w
    buffer, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
    created, exited, kernel, user = w.FILETIME(), w.FILETIME(), w.FILETIME(), w.FILETIME()
    if (not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            or not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user))):
        raise OSError(0, '无法验证待退出的小伴进程', None, ctypes.get_last_error())
    return {'pid': pid, 'created': (created.dwHighDateTime << 32) | created.dwLowDateTime,
            'exe': str(Path(buffer.value).resolve())}


def owner_processes(target: Path) -> list[dict]:
    """Capture this PyInstaller child and only its same-image bootloader parent."""
    if os.name != 'nt':
        raise ValueError('自动替换仅适用于 Windows 便携版')
    owners = []
    for pid in {os.getpid(), os.getppid()}:
        api, handle = _process_handle(pid)
        if handle:
            try:
                identity = _identity(api, handle, pid)
                if identity['exe'].casefold() == str(target).casefold():
                    owners.append(identity)
            finally:
                api.CloseHandle(handle)
    if not any(identity['pid'] == os.getpid() for identity in owners):
        raise ValueError('只能更新当前运行的小伴程序')
    return owners


def wait_for_owners(owners: list, target: Path, deadline=WAIT_DEADLINE) -> None:
    """Hold verified process handles: PID reuse can never target another app."""
    if os.name != 'nt' or not owners or len(owners) > 2:
        raise ValueError('更新进程身份无效')
    handles = []
    try:
        for expected in owners:
            if (not isinstance(expected, dict) or set(expected) != {'pid', 'created', 'exe'}
                    or type(expected['pid']) is not int or expected['pid'] <= 0
                    or type(expected['created']) is not int or expected['created'] <= 0
                    or expected['exe'].casefold() != str(target).casefold()):
                raise ValueError('更新进程身份无效')
            api, handle = _process_handle(expected['pid'])
            if handle:
                handles.append((api, handle))
                # A retained Popen/OpenProcess handle keeps a terminated PID
                # openable, but Windows no longer provides its image name.
                # An exited handle cannot lock the EXE or be reused for another
                # process, so only living owners require the identity query.
                if not _running(api, handle):
                    continue
                try:
                    actual = _identity(api, handle, expected['pid'])
                except OSError:
                    # It may have exited between the status and image queries.
                    if not _running(api, handle):
                        continue
                    raise
                if actual != expected:
                    raise ValueError('原程序进程身份已改变，已取消更新')
        end = time.monotonic() + deadline
        while any(_running(api, handle) for api, handle in handles):
            if time.monotonic() >= end:
                raise TimeoutError('等待小伴退出超时，原程序未被更改')
            time.sleep(.1)
    finally:
        for api, handle in handles:
            api.CloseHandle(handle)


def _running(api, handle) -> bool:
    result = api.WaitForSingleObject(handle, 0)
    if result == 0:  # WAIT_OBJECT_0: terminated, even if another handle survives
        return False
    if result == 0x102:  # WAIT_TIMEOUT: alive
        return True
    import ctypes
    raise OSError(0, '无法查询待退出的小伴进程状态', None, ctypes.get_last_error())


def _probe(candidate: Path, updates: Path, expected_version: str) -> None:
    """The existing full backend self-test runs with disposable data and a bound."""
    directory = within(updates / ('probe-' + uuid.uuid4().hex), updates)
    directory.mkdir()
    report = directory / 'report.json'
    kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
              'shell': False, 'env': dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'), 'cwd': str(directory)}
    job = None
    if os.name == 'nt':
        # Reuse the application's suspended-root Job Object guardian. It owns only
        # this self-test tree and closes/kills it on completion or timeout.
        from companion.model_runtime import _WindowsJob
        job = _WindowsJob()
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | 0x4
    else:
        kwargs['start_new_session'] = True
    process = None
    try:
        process = subprocess.Popen([str(candidate), '--self-test', '--report', str(report),
                                    '--data-dir', str(directory / 'data')], **kwargs)
        if job:
            job.attach_and_resume(process)
        if process.wait(timeout=PROBE_DEADLINE) != 0:
            raise ValueError('新版本自检未通过，已保留原程序')
        result = read_json(report)
        if result.get('status') != 'passed' or result.get('server_stopped') is not True or result.get('frozen') is not True:
            raise ValueError('新版本自检未通过，已保留原程序')
        if result.get('update_version') != expected_version:
            raise ValueError('新程序内置版本与签名版本不一致，已保留原程序')
    except subprocess.TimeoutExpired as error:
        raise TimeoutError('新版本自检超时，已保留原程序') from error
    finally:
        if job:
            _terminate_owned_job(job)
        elif process and process.poll() is None:
            if os.name != 'nt':
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        if process:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def _terminate_owned_job(job) -> None:
    """Wait for the entire owned tree to release file locks before rollback."""
    if not hasattr(job, 'api'):  # isolated injected jobs
        job.close()
        return
    import ctypes
    from ctypes import wintypes as w
    class Accounting(ctypes.Structure):
        _fields_ = [('total_user', ctypes.c_int64), ('total_kernel', ctypes.c_int64),
                    ('period_user', ctypes.c_int64), ('period_kernel', ctypes.c_int64),
                    ('faults', w.DWORD), ('processes', w.DWORD), ('active', w.DWORD), ('terminated', w.DWORD)]
    job.api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    job.api.TerminateJobObject.restype = w.BOOL
    job.api.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
    job.api.QueryInformationJobObject.restype = w.BOOL
    try:
        job.api.TerminateJobObject(job.handle, 1)
        end = time.monotonic() + 5
        while time.monotonic() < end:
            info = Accounting()
            if not job.api.QueryInformationJobObject(job.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None) or info.active == 0:
                break
            time.sleep(.05)
    finally:
        job.close()


def _disown_job(job) -> None:
    """Remove kill-on-close only after the owned application has proved ready."""
    import ctypes
    from ctypes import wintypes as w
    class Limits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', w.DWORD),
                    ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t), ('active', w.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('limits', Limits), ('io', Counters), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    info = Extended()  # flags=0: keep owned processes alive after closing handle.
    if not job.api.SetInformationJobObject(job.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError('无法释放新版本启动保护')
    job.close()


def _ack_owned(job, pid: int, target: Path) -> None:
    import ctypes
    from ctypes import wintypes as w
    if type(pid) is not int or pid <= 0:
        raise ValueError('启动确认的进程无效')
    api, handle = _process_handle(pid)
    if not handle:
        raise ValueError('新版本在确认前已退出')
    try:
        api.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
        api.IsProcessInJob.restype = w.BOOL
        member = w.BOOL()
        if (not api.IsProcessInJob(handle, job.handle, ctypes.byref(member)) or not member.value
                or _identity(api, handle, pid)['exe'].casefold() != str(target).casefold()
                or api.WaitForSingleObject(handle, 0) == 0):
            raise ValueError('启动确认不属于新版本进程')
    finally:
        api.CloseHandle(handle)


def _restart(target: Path, data_dir: Path, skip: bool, expected_version: str | None = None) -> dict:
    """Success means the new GUI+backend acknowledged, not merely Popen succeeded."""
    updates = plain_path(data_dir / 'updates')
    token = uuid.uuid4().hex + uuid.uuid4().hex
    ack = within(updates / ('startup-' + token + '.json'), updates)
    command = [str(target), '--data-dir', str(data_dir)]
    if skip:
        command.append('--skip-update-once')
    command += ['--update-ack', str(ack), '--update-ack-token', token]
    if os.name != 'nt':
        raise ValueError('自动重启仅适用于 Windows 便携版')
    from companion.model_runtime import _WindowsJob
    job, process, ready = _WindowsJob(), None, False
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, shell=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 0x4,
                                   env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'), close_fds=True)
        job.attach_and_resume(process)
        end = time.monotonic() + STARTUP_DEADLINE
        while time.monotonic() < end:
            if process.poll() is not None:
                raise OSError('小伴在启动完成前退出')
            if ack.exists():
                result = read_json(ack)
                if (set(result) != {'token', 'version', 'pid', 'status'} or result['token'] != token
                        or result['status'] != 'ready' or (expected_version is not None and result['version'] != expected_version)):
                    raise ValueError('新版本启动确认无效')
                _ack_owned(job, result['pid'], target)
                _disown_job(job)
                ready = True
                return result
            time.sleep(.1)
        raise TimeoutError('新版本启动确认超时，正在恢复原程序')
    finally:
        if not ready:
            _terminate_owned_job(job)  # never other apps
            if process:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        try:
            ack.unlink(missing_ok=True)
        except OSError:
            pass


def _configuration() -> dict:
    from desktop.runtime import resource_root
    return read_json(resource_root() / 'desktop/update-config.json')


def apply_update_request(request_path: Path, *, _config=None, _wait=None, _probe=None,
                         _restart=None, _test_mode=False) -> int:
    """Only main's --apply-update entry invokes this; production trust is pinned.

    Underscored injections are for isolated failure tests, never CLI/remote input.
    All changes, apart from replacing the EXE and its rollback, stay in updates/.
    """
    updates = data_dir = target = None
    backup = replacement = None
    old_sha = version = None
    replaced = False
    target_lock = None
    stage = 'validation'
    restart = _restart or globals()['_restart']
    try:
        if not _test_mode and (os.name != 'nt' or not getattr(sys, 'frozen', False)):
            raise ValueError('更新助手只能从 Windows 便携版运行')
        request_path = plain_path(request_path)
        request = read_json(request_path)
        fields = {'schema', 'data_dir', 'target', 'staged', 'helper', 'manifest', 'old_sha256', 'processes'}
        if set(request) != fields or type(request['schema']) is not int or request['schema'] != 1:
            raise ValueError('更新请求格式无效')
        config = _config if _test_mode else _configuration()
        payload = verify_manifest(config, request['manifest'])
        version = payload['version']
        if version_tuple(version) <= version_tuple(config['version']):
            raise ValueError('待安装版本不是更新版本')
        data_dir = plain_path(Path(request['data_dir']))
        updates = plain_path(data_dir / 'updates')
        if request_path.parent != updates:
            raise ValueError('更新请求不在专用更新目录')
        staged = within(Path(request['staged']), updates)
        helper = within(Path(request['helper']), updates)
        target = plain_path(Path(request['target']))
        if target.suffix.lower() != '.exe' or target.name.lower().startswith(('python', 'pypy')):
            raise ValueError('更新目标无效')
        if target == helper or target == staged or updates == target.parent or updates in target.parents:
            raise ValueError('不能替换更新目录中的程序')
        target_lock = _TargetLock(target)
        if not _test_mode and helper != plain_path(Path(sys.executable)):
            raise ValueError('更新助手身份不符')
        old_sha = request['old_sha256']
        if not isinstance(old_sha, str) or file_sha256(helper) != old_sha or file_sha256(target) != old_sha:
            raise ValueError('原程序或更新助手已改变，已取消更新')
        verify_file(staged, payload)
        stage = 'preflight'
        # Write and flush a disposable file in the install directory before any
        # process exit or replacement; a read-only USB/install path fails safely.
        replacement = plain_path(target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.update'))
        with replacement.open('xb') as stream:
            stream.write(b'')
            stream.flush()
            os.fsync(stream.fileno())
        stage = 'waiting'
        (_wait or wait_for_owners)(request['processes'], target)
        stage = 'probe'
        (_probe or globals()['_probe'])(staged, updates, version)
        stage = 'replacing'
        # Both files may have changed during the bounded self-test.
        verify_file(staged, payload)
        if file_sha256(target) != old_sha:
            raise ValueError('原程序已改变，已取消更新')
        backup = plain_path(target.with_name(target.name + '.rollback'))
        temporary_backup = plain_path(backup.with_name(backup.name + '.' + uuid.uuid4().hex + '.tmp'))
        try:
            shutil.copyfile(target, temporary_backup)
            if file_sha256(temporary_backup) != old_sha:
                raise ValueError('原程序备份校验失败')
            os.replace(temporary_backup, backup)
        finally:
            temporary_backup.unlink(missing_ok=True)
        shutil.copyfile(staged, replacement)
        verify_file(replacement, payload)
        # os.replace is atomic and same-directory. The old EXE always has a
        # verified rollback copy before this point.
        os.replace(replacement, target)
        replaced = True
        stage = 'starting'
        startup = restart(target, data_dir, False, version)
        # Once the real GUI+backend have acknowledged and the job is disowned,
        # bookkeeping failure must never roll back a live successful program.
        try:
            atomic_json(updates / 'result.json', {'status': 'applied', 'version': version, 'message': '更新安装成功',
                                                 'ready_version': startup.get('version', '') if isinstance(startup, dict) else '',
                                                 'ready_pid': startup.get('pid', 0) if isinstance(startup, dict) else 0})
            pending = updates / 'pending.json'
            if pending.exists():
                plain_path(pending).unlink()
        except (OSError, ValueError):
            pass
        return 0
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, TimeoutError)) else '安装未完成，请检查程序目录权限或重新下载更新'
        if replaced and target and backup:
            try:
                if file_sha256(backup) != old_sha:
                    raise ValueError('回滚备份校验失败')
                restore = plain_path(target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.restore'))
                shutil.copyfile(backup, restore)
                if file_sha256(restore) != old_sha:
                    raise ValueError('回滚备份校验失败')
                os.replace(restore, target)
                replaced = False
            except Exception:
                message = '自动恢复未完成；原程序备份保留在同目录的 .rollback 文件中'
        if updates:
            try:
                state = read_json(updates / 'state.json') if (updates / 'state.json').exists() else {'auto_download': True}
                if version:
                    state['failed_version'] = version
                atomic_json(updates / 'state.json', state)
                code = getattr(error, 'winerror', None)
                atomic_json(updates / 'result.json', {'status': 'failed', 'version': version or '', 'message': message,
                                                     'stage': stage, 'error_type': type(error).__name__,
                                                     'winerror': code if type(code) is int else None})
            except Exception:
                pass
        # Restart only a target which is still the original verified binary.
        if target and data_dir and old_sha and not replaced:
            try:
                if file_sha256(target) == old_sha:
                    restart(target, data_dir, True, config['version'])
            except Exception:
                pass
        return 1
    finally:
        if target_lock:
            target_lock.close()
        if replacement:
            try:
                plain_path(replacement).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
