import {getModelConfig,ModelRuntimeError} from './model-admin.mjs';

const INPUT_LIMIT=128000,OUTPUT_LIMIT=6000;
const safeError=()=>new ModelRuntimeError('模型服务暂时不可用，请检查管理员配置');

// The iterator owns the provider connection. Returning early, a disconnected
// browser and the total deadline all tear down the actual upstream body.
export async function* streamModel(env,messages,{signal}={}){
 let reader,timer,finished=false,bytes=0,output='';
 const controller=new AbortController();
 const stop=()=>{controller.abort(signal?.reason);void reader?.cancel().catch(()=>{});};
 if(signal?.aborted)throw signal.reason;
 signal?.addEventListener('abort',stop,{once:true});
 try{
  const c=await getModelConfig(env);if(!c.configured)throw safeError();
  controller.signal.throwIfAborted();
  const local=c.mode==='local',url=local?c.gateway_url:c.api_url,key=local?c.gateway_key:c.api_key;
  const target=new URL(url);if(target.protocol!=='https:'||target.username||target.password||target.search||target.hash)throw safeError();
  timer=setTimeout(()=>{controller.abort(new DOMException('Model deadline exceeded','TimeoutError'));void reader?.cancel().catch(()=>{});},local?50000:20000);
  const payload={model:local?c.local_provider:c.model,messages,max_tokens:700,temperature:.4,stream:true};
  if(local&&c.local_model)payload.local_model=c.local_model;
  const response=await fetch(url.replace(/\/$/,'')+'/chat/completions',{method:'POST',redirect:'manual',headers:{'Content-Type':'application/json',Authorization:'Bearer '+key},signal:controller.signal,body:JSON.stringify(payload)});
  controller.signal.throwIfAborted();
  reader=response.body?.getReader();if(!response.ok||!reader)throw safeError();
  const type=(response.headers.get('Content-Type')||'').split(';')[0].trim().toLowerCase();
  const decoder=new TextDecoder('utf-8',{fatal:true});
  async function read(){
   controller.signal.throwIfAborted();const value=await reader.read();controller.signal.throwIfAborted();
   if(value.done)return {done:true,text:decoder.decode()};
   bytes+=value.value.byteLength;if(bytes>INPUT_LIMIT)throw safeError();
   return {done:false,text:decoder.decode(value.value,{stream:true})};
  }
  if(type==='application/json'){
   let raw='';while(true){const next=await read();raw+=next.text;if(next.done)break;}
   const text=JSON.parse(raw).choices?.[0]?.message?.content;
   if(typeof text!=='string'||!text.trim()||text.length>OUTPUT_LIMIT)throw safeError();
   finished=true;yield text.trim();controller.signal.throwIfAborted();return;
  }
  if(type!=='text/event-stream')throw safeError();
  let buffer='',complete=false;
  function event(frame){
   const lines=frame.split(/\r?\n/),data=lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,'')).join('\n');
   if(!data)return '';
   if(data.trim()==='[DONE]'){complete=true;return '';}
   const value=JSON.parse(data);if(value.error||lines.some(line=>/^event:\s*error\s*$/.test(line)))throw safeError();
   if(!Array.isArray(value.choices))throw safeError();
   const choice=value.choices.find(item=>item.index===0)||value.choices[0];
   if(!choice)return '';
   if(choice.finish_reason!==null&&choice.finish_reason!==undefined){if(typeof choice.finish_reason!=='string')throw safeError();complete=true;}
   const text=choice.delta?.content;if(text===null||text===undefined)return '';
   if(typeof text!=='string')throw safeError();
   output+=text;if(output.length>OUTPUT_LIMIT)throw safeError();return text;
  }
  while(true){
   const next=await read();buffer+=next.text;
   let match;while((match=/\r?\n\r?\n/.exec(buffer))){
    const frame=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);
    const text=event(frame);if(text)yield text;
    controller.signal.throwIfAborted();
    if(complete){if(!output.trim())throw safeError();finished=true;return;}
   }
   if(next.done){if(!complete||buffer.trim()||!output.trim())throw safeError();finished=true;return;}
  }
 }catch(error){if(signal?.aborted)throw signal.reason;throw safeError();}
 finally{
  clearTimeout(timer);signal?.removeEventListener('abort',stop);
  if(!finished&&!controller.signal.aborted)controller.abort();
  if(reader){try{await reader.cancel();}catch{}reader.releaseLock();}
 }
}
