"""Stateless, authenticated Claude/Codex text gateway for the hosted Worker."""
from __future__ import annotations

import hmac
import json
import os
import re
import time

from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .model_runtime import ModelRuntimeError,run_cli

def create_gateway():
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

    @app.middleware('http')
    async def private_response(request,call_next):
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'})
        return response

    @app.get('/health')
    def health():return {'status':'ok'}

    @app.post('/v1/chat/completions')
    async def complete(request:Request):
        secret=os.getenv('COMPANION_CLI_GATEWAY_TOKEN','');supplied=request.headers.get('authorization','').removeprefix('Bearer ')
        if len(secret)<32:raise HTTPException(503,'CLI 网关尚未配置访问令牌')
        if not hmac.compare_digest(secret.encode(),supplied.encode()):raise HTTPException(401,'需要服务端网关授权')
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>80000:raise HTTPException(413,'模型上下文过长')
        try:
            data=json.loads(raw)
            if not isinstance(data,dict) or set(data)-{'model','messages','max_tokens','temperature','local_model'}:raise ValueError()
            provider=data['model'];messages=data['messages'];model=data.get('local_model','')
            if provider not in {'codex','claude'} or not isinstance(messages,list) or not 1<=len(messages)<=16:raise ValueError()
            if not isinstance(model,str) or model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}',model):raise ValueError()
            for message in messages:
                if not isinstance(message,dict) or set(message)!={'role','content'} or message['role'] not in {'system','user','assistant'} or not isinstance(message['content'],str):raise ValueError()
        except (ValueError,TypeError,KeyError):raise HTTPException(422,'仅支持 Claude/Codex 的有效文本消息')
        try:text=await run_in_threadpool(run_cli,provider,messages,model)
        except ModelRuntimeError as error:raise HTTPException(503,str(error))
        except Exception:raise HTTPException(503,'本地模型服务暂不可用')
        return {'id':'xiaoban-cli','object':'chat.completion','created':int(time.time()),'model':provider,'choices':[{'index':0,'message':{'role':'assistant','content':text[:6000]},'finish_reason':'stop'}]}

    return app

if __name__=='__main__':
    from dotenv import load_dotenv
    load_dotenv()
    import uvicorn
    uvicorn.run(create_gateway(),host=os.getenv('COMPANION_CLI_GATEWAY_HOST','127.0.0.1'),port=int(os.getenv('COMPANION_CLI_GATEWAY_PORT','8777')),access_log=False)
