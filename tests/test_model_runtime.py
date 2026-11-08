"""Real process lifecycle and cancellable network boundary regressions."""
import asyncio
import json
import os
import subprocess
import sys
import time

import httpx
import pytest

from companion import model_runtime as runtime


def alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        api=ctypes.windll.kernel32
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        api.OpenProcess.restype=wintypes.HANDLE
        api.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=api.OpenProcess(0x1000,False,pid)
        if not handle:return False
        code=ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value==259
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False


def test_cli_parent_exit_cannot_leave_descendant_or_block_output(tmp_path,monkeypatch):
    pidfile=tmp_path/'descendant.pid'
    code=('import subprocess,sys,pathlib,json; '
          'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"]);'
          f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid));'
          'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"synthetic"}}),flush=True)')
    monkeypatch.setattr(runtime,'cli_binary',lambda provider:sys.executable)
    monkeypatch.setattr(runtime,'cli_command',lambda *args:[sys.executable,'-c',code])
    pid=None
    try:
        try:result=runtime.run_cli('codex',[{'role':'user','content':'synthetic'}])
        except runtime.ModelRuntimeError:result=None
        pid=int(pidfile.read_text())
        assert not alive(pid),'CLI descendant survived completed invocation'
        assert result=='synthetic'
    finally:
        if pid is None and pidfile.exists():pid=int(pidfile.read_text())
        if pid and alive(pid):
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            else:os.kill(pid,9)


def test_http_total_deadline_cancels_slow_response_and_closes_stream(monkeypatch):
    closed=[];cancelled=[]
    class SlowBody(httpx.SyncByteStream,httpx.AsyncByteStream):
        def __iter__(self):
            for _ in range(20):time.sleep(.02);yield b' '
        async def __aiter__(self):
            try:
                for _ in range(20):await asyncio.sleep(.02);yield b' '
            except asyncio.CancelledError:cancelled.append(True);raise
        def close(self):closed.append(True)
        async def aclose(self):closed.append(True)
    monkeypatch.setattr(runtime,'HTTP_TIMEOUT_SECONDS',.08,raising=False)
    original=runtime.http_client
    monkeypatch.setattr(runtime,'http_client',lambda **kw:original(transport=httpx.MockTransport(lambda req:httpx.Response(200,stream=SlowBody())),**kw))
    model=object.__new__(runtime.ModelRuntime)
    monkeypatch.setattr(model,'config',lambda:{'mode':'api','api_url':'https://synthetic.invalid/v1','api_key':'synthetic-secret','model':'test'})
    began=time.monotonic()
    with pytest.raises(runtime.ModelRuntimeError):model.complete([{'role':'user','content':'synthetic'}])
    assert time.monotonic()-began<.3
    assert cancelled and closed,'deadline must cancel and close actual response iteration'


@pytest.mark.parametrize('failure',['timeout','stdout','stderr'])
def test_cli_failure_kills_actual_process_tree(tmp_path,monkeypatch,failure):
    pidfile=tmp_path/'descendant.pid'
    suffix={'timeout':'time.sleep(20)',
            'stdout':'sys.stdout.write("x"*300000);sys.stdout.flush();time.sleep(20)',
            'stderr':'sys.stderr.write("x"*20000);sys.stderr.flush();time.sleep(20)'}[failure]
    code=('import subprocess,sys,pathlib,time;'
          'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"]);'
          f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid));'+suffix)
    monkeypatch.setattr(runtime,'cli_binary',lambda provider:sys.executable)
    monkeypatch.setattr(runtime,'cli_command',lambda *args:[sys.executable,'-c',code])
    monkeypatch.setattr(runtime,'CLI_TIMEOUT_SECONDS',.5)
    with pytest.raises(runtime.ModelRuntimeError):runtime.run_cli('codex',[{'role':'user','content':'synthetic'}])
    pid=int(pidfile.read_text())
    try:assert not alive(pid)
    finally:
        if alive(pid):
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            else:os.kill(pid,9)
    # Cleanup must also release the singleton slot after every failure.
    assert runtime._cli_slot.acquire(blocking=False)
    runtime._cli_slot.release()
