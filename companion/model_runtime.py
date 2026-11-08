"""Administrator-controlled model transports. Secrets never enter public status."""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import httpx

http_client=httpx.AsyncClient
HTTP_TIMEOUT_SECONDS=25
GATEWAY_TIMEOUT_SECONDS=50
CLI_TIMEOUT_SECONDS=45
_cli_slot=threading.BoundedSemaphore(1)
FIELDS={'mode','api_url','api_key','model','local_provider','local_model','gateway_url','gateway_key'}

class ModelRuntimeError(ValueError):
    pass

def _error(message='模型服务暂时不可用，请检查管理员配置'):
    raise ModelRuntimeError(message)

def validate_config(data,previous=None,cloud=False):
    if not isinstance(data,dict) or set(data)-FIELDS:_error('模型配置字段无效')
    config={'mode':'disabled','api_url':'','api_key':'','model':'','local_provider':'codex','local_model':'','gateway_url':'','gateway_key':'',**(previous or {}),**data}
    if not isinstance(config['mode'],str) or config['mode'] not in {'disabled','api','local'}:_error('调用模式无效')
    if not isinstance(config['local_provider'],str) or config['local_provider'] not in {'codex','claude'}:_error('本地服务只支持 Claude 或 Codex')
    for key in FIELDS-{'mode','local_provider'}:
        if not isinstance(config[key],str) or len(config[key])>(8192 if key.endswith('key') else 1000):_error('模型配置字段无效')
        config[key]=config[key].strip()
    for key in ['api_url','gateway_url']:
        value=config[key]
        if value:
            parsed=urlsplit(value)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:_error('模型与网关地址必须是无凭证的 HTTPS 地址')
            config[key]=value.rstrip('/')
    if config['local_model'] and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}',config['local_model']):_error('本地模型名称无效')
    if config['mode']=='api' and not all(config[k] for k in ['api_url','api_key','model']):_error('请填写 API 地址、密钥和模型名称')
    if config['mode']=='local' and (cloud or config['gateway_url']) and not all(config[k] for k in ['gateway_url','gateway_key']):_error('云端本地模式需要 HTTPS 网关地址和访问令牌')
    return {k:config[k] for k in FIELDS}

def legacy_config():
    values={'mode':'api','api_url':os.getenv('COMPANION_MODEL_BASE_URL',''),'api_key':os.getenv('COMPANION_MODEL_API_KEY',''),'model':os.getenv('COMPANION_MODEL','')}
    if not all(values[k] for k in ['api_url','api_key','model']):values['mode']='disabled'
    return {'local_provider':'codex','local_model':'','gateway_url':'','gateway_key':'',**values}

def _key():
    secret=os.getenv('COMPANION_CONFIG_ENCRYPTION_KEY') or os.getenv('AUTH_PEPPER','')
    if len(secret)<32:_error('服务端尚未配置模型设置加密密钥')
    return hashlib.sha256(('xiaoban-model-settings-v1\0'+secret).encode()).digest()

def encrypt(config):
    iv=os.urandom(12)
    value=AESGCM(_key()).encrypt(iv,json.dumps(config,ensure_ascii=False).encode(),b'xiaoban-model-settings-v1')
    return json.dumps({'version':1,'iv':base64.b64encode(iv).decode(),'data':base64.b64encode(value).decode()})

def decrypt(value):
    try:
        doc=json.loads(value)
        if doc['version']!=1:_error()
        raw=AESGCM(_key()).decrypt(base64.b64decode(doc['iv']),base64.b64decode(doc['data']),b'xiaoban-model-settings-v1')
        return json.loads(raw)
    except ModelRuntimeError:raise
    except Exception:_error('模型设置无法解密，请检查服务端加密密钥')

def cli_binary(provider):
    name=os.getenv('COMPANION_'+provider.upper()+'_BIN') or shutil.which(provider)
    return name if name and Path(name).name.lower() in {provider,provider+'.exe'} else None

def cli_command(provider,binary,model=''):
    if provider=='codex':
        cmd=[binary,'exec','--ignore-user-config','--ignore-rules','--sandbox','read-only','--ephemeral','--skip-git-repo-check','--json','--color','never']
        for flag in ['shell_tool','apps','browser_use','computer_use','in_app_browser','multi_agent','multi_agent_v2','view_image','code_mode','code_mode_host','skill_search','shell_snapshot']:
            cmd+=['--config','features.'+flag+'=false']
        cmd+=['--config','web_search="disabled"','--config','project_doc_max_bytes=0','--config','features.skip_host_skill_discovery=true']
        if model:cmd+=['--model',model]
        return cmd+['-']
    if provider=='claude':
        cmd=[binary,'--safe-mode','--restricted','-p','--output-format','json','--tools','','--disallowedTools','*','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--no-session-persistence','--disable-slash-commands','--no-chrome','--permission-mode','dontAsk']
        if model:cmd+=['--model',model]
        return cmd
    _error('本地服务只支持 Claude 或 Codex')

def parse_cli_output(provider,output):
    try:
        if provider=='claude':
            data=json.loads(output)
            if data.get('is_error'):_error()
            text=data['result']
        else:
            text=None
            for line in output.splitlines():
                event=json.loads(line)
                if event.get('type') in {'turn.failed','error'}:_error()
                item=event.get('item',{})
                if event.get('type')=='item.completed' and item.get('type')=='agent_message':text=item['text']
        if not isinstance(text,str) or not text.strip():_error()
        return text.strip()[:6000]
    except ModelRuntimeError:raise
    except Exception:_error('本地模型未返回可用文本')

class _WindowsJob:
    """Attach a suspended root before it can create children; closing kills all."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        class Limits(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class Counters(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ['read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes']]
        class Extended(ctypes.Structure):
            _fields_=[('limits',Limits),('io',Counters),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];self.api.CreateJobObjectW.restype=w.HANDLE
        self.api.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];self.api.SetInformationJobObject.restype=w.BOOL
        self.api.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];self.api.AssignProcessToJobObject.restype=w.BOOL
        self.api.CloseHandle.argtypes=[w.HANDLE];self.api.CloseHandle.restype=w.BOOL
        self.lock=threading.Lock();self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        info=Extended();info.limits.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)):
            error=ctypes.WinError(ctypes.get_last_error());self.close();raise error

    def attach_and_resume(self,process):
        import ctypes
        from ctypes import wintypes as w
        if not self.api.AssignProcessToJobObject(self.handle,int(process._handle)):
            process.kill();raise ctypes.WinError(ctypes.get_last_error())
        resume=ctypes.WinDLL('ntdll').NtResumeProcess
        resume.argtypes=[w.HANDLE];resume.restype=w.LONG
        if resume(int(process._handle))!=0:
            self.close();raise OSError('Cannot resume isolated CLI process')

    def close(self):
        with self.lock:
            if self.handle:self.api.CloseHandle(self.handle);self.handle=None


def _kill(process,job=None):
    if job is not None:
        job.close()
        if process.poll() is None:process.kill()
        return
    if os.name=='nt':
        if process.poll() is not None:return
        try:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
        finally:
            if process.poll() is None:process.kill()
    else:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass

def run_cli(provider,messages,model=''):
    binary=cli_binary(provider)
    if not binary:_error('服务器尚未安装对应的本地 CLI')
    if not _cli_slot.acquire(blocking=False):_error('本地模型正在处理其他请求，请稍后再试')
    try:
        payload=('你是小伴的纯文本陪伴回复服务。仅根据下面的消息回答，不调用工具、不读取文件、不执行命令，也不要声称执行了操作。\n'+json.dumps(messages,ensure_ascii=False)).encode()
        if len(payload)>80000:_error('模型上下文过长')
        env={k:v for k,v in os.environ.items() if not k.startswith(('COMPANION_','OPEN_WEARABLES_','FCM_','MEM0_','LANGSMITH_','LANGCHAIN_')) and k not in {'AUTH_PEPPER','CRON_TOKEN','ADMIN_SETUP_TOKEN','CODEX_THREAD_ID'}}
        with tempfile.TemporaryDirectory(prefix='xiaoban-model-') as directory:
            kwargs={'cwd':directory,'env':env,'stdin':subprocess.PIPE,'stdout':subprocess.PIPE,'stderr':subprocess.PIPE,'shell':False}
            if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP
            else:kwargs['start_new_session']=True
            job=_WindowsJob() if os.name=='nt' else None
            process=None
            if job:kwargs['creationflags']|=0x4  # CREATE_SUSPENDED
            try:
                process=subprocess.Popen(cli_command(provider,binary,model),**kwargs)
                if job:job.attach_and_resume(process)
            except BaseException:
                if process is not None:_kill(process,job)
                elif job:job.close()
                raise
            buffers=[bytearray(),bytearray()];overflow=threading.Event()
            def read(stream,index,limit):
                while True:
                    chunk=stream.read(4096)
                    if not chunk:break
                    if len(buffers[index])+len(chunk)>limit:
                        overflow.set();_kill(process,job);break
                    buffers[index].extend(chunk)
            def write():
                try:process.stdin.write(payload);process.stdin.close()
                except (BrokenPipeError,OSError):pass
            threads=[threading.Thread(target=read,args=(process.stdout,0,262144),daemon=True),threading.Thread(target=read,args=(process.stderr,1,16384),daemon=True),threading.Thread(target=write,daemon=True)]
            for thread in threads:thread.start()
            timed_out=False
            try:process.wait(timeout=CLI_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:timed_out=True
            finally:
                # The root may exit before descendants. Always close its whole
                # process group/job before waiting for inherited pipes to drain.
                _kill(process,job)
                process.wait(timeout=5)
                for thread in threads:thread.join(timeout=1)
                for stream in [process.stdin,process.stdout,process.stderr]:
                    stream.close()
            if timed_out:_error('本地模型响应超时，请稍后再试')
            if process.returncode or overflow.is_set():_error('本地模型调用失败，请检查 CLI 登录与服务状态')
            return parse_cli_output(provider,buffers[0].decode('utf-8'))
    except ModelRuntimeError:raise
    except Exception:_error('本地模型调用失败，请检查 CLI 登录与服务状态')
    finally:_cli_slot.release()

class ModelRuntime:
    def __init__(self,store):
        self.store=store
        with store.transaction():store.connection.execute('CREATE TABLE IF NOT EXISTS service_state (state_key TEXT PRIMARY KEY,value TEXT NOT NULL)')

    def config(self):
        with self.store.lock:row=self.store.connection.execute("SELECT value FROM service_state WHERE state_key='model_settings'").fetchone()
        return decrypt(row[0]) if row else legacy_config()

    def save(self,data):
        config=validate_config(data,self.config())
        with self.store.transaction():self.store.connection.execute("INSERT INTO service_state VALUES('model_settings',?) ON CONFLICT(state_key) DO UPDATE SET value=excluded.value",(encrypt(config),))
        return self.public_config()

    @property
    def configured(self):
        try:
            c=self.config()
            return c['mode']=='api' and all(c.get(k) for k in ['api_url','api_key','model']) or c['mode']=='local' and bool((c.get('gateway_url') and c.get('gateway_key')) or cli_binary(c['local_provider']))
        except ModelRuntimeError:return False

    def status(self):
        try:c=self.config()
        except ModelRuntimeError:c={'mode':'disabled'}
        return {'configured':bool(self.configured),'mode':c['mode'],'provider':c.get('local_provider') if c['mode']=='local' else 'api' if c['mode']=='api' else None}

    def public_config(self):
        c=self.config()
        return {**{k:v for k,v in c.items() if k not in {'api_key','gateway_key'}},'api_key_configured':bool(c.get('api_key')),'gateway_key_configured':bool(c.get('gateway_key')),'configured':bool(self.configured),'runtime':'python'}

    def complete(self,messages):
        config=self.config()
        if not self.configured:_error('模型服务尚未配置')
        if config['mode']=='local' and not config.get('gateway_url'):return run_cli(config['local_provider'],messages,config.get('local_model',''))
        local=config['mode']=='local';url=config['gateway_url'] if local else config['api_url'];key=config['gateway_key'] if local else config['api_key']
        target=urlsplit(url)
        if target.scheme!='https' or not target.hostname or target.username or target.password or target.query or target.fragment:_error('模型与网关地址必须是无凭证的 HTTPS 地址')
        payload={'model':config['local_provider'] if local else config['model'],'messages':messages,'max_tokens':700,'temperature':.4}
        if local and config.get('local_model'):payload['local_model']=config['local_model']
        async def request_text():
            async with http_client(timeout=GATEWAY_TIMEOUT_SECONDS if local else HTTP_TIMEOUT_SECONDS,follow_redirects=False) as client:
                async with client.stream('POST',url.rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json=payload) as response:
                    response.raise_for_status();raw=bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>128000:_error('模型响应内容过大')
            text=json.loads(raw)['choices'][0]['message']['content']
            if not isinstance(text,str) or not text.strip():_error('模型未返回可用文本')
            return text[:6000]
        async def bounded_request():
            return await asyncio.wait_for(request_text(),timeout=GATEWAY_TIMEOUT_SECONDS if local else HTTP_TIMEOUT_SECONDS)
        try:
            return asyncio.run(bounded_request())
        except Exception:_error()
