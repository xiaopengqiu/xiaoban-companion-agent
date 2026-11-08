// The final `done` event confirms persistence; deltas are visible before it arrives.
const MAX_BYTES=256000;
const interrupted=()=>new Error('回复中断，尚未生成完整内容，请重试。');
function validReply(data){if(!data||typeof data.reply!=='string'||!data.reply.trim()||data.reply.length>6000)throw interrupted();return data;}

export async function requestChat({message,token,signal,onDelta=()=>{},fetcher=fetch,timeoutMs=90000}){
 const controller=new AbortController();let reader,timedOut=false;
 const cancel=()=>{controller.abort(signal?.reason);reader?.cancel().catch(()=>{});};
 if(signal?.aborted)cancel();else signal?.addEventListener('abort',cancel,{once:true});
 const timer=setTimeout(()=>{timedOut=true;controller.abort();reader?.cancel().catch(()=>{});},timeoutMs);
 const check=()=>controller.signal.throwIfAborted();
 try{
  check();const response=await fetcher('/api/chat',{method:'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json',Accept:'text/event-stream',...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify({message})});check();
  reader=response.body?.getReader();if(!reader)throw interrupted();
  const decoder=new TextDecoder('utf-8',{fatal:true});let bytes=0;
  async function read(){check();const part=await reader.read();check();if(part.done)return null;bytes+=part.value.byteLength;if(bytes>MAX_BYTES)throw interrupted();return decoder.decode(part.value,{stream:true});}
  const streaming=response.ok&&response.headers.get('Content-Type')?.toLowerCase().includes('text/event-stream');
  if(!streaming){let text='',chunk;while((chunk=await read())!==null)text+=chunk;text+=decoder.decode();let data;try{data=JSON.parse(text);}catch{throw interrupted();}
   if(!response.ok){const error=new Error(response.status===429?'尝试次数较多，请稍后再试。':typeof data.detail==='string'?data.detail.slice(0,250):`请求未完成（${response.status}）`);error.status=response.status;throw error;}
   return validReply(data);
  }
  let pending='',event='',data=[],length=0;
  function dispatch(){if(!data.length){event='';return null;}let value;try{value=JSON.parse(data.join('\n'));}catch{throw interrupted();}const kind=event;event='';data=[];
   if(kind==='delta'){if(typeof value.text!=='string'||(length+=value.text.length)>6000)throw interrupted();if(value.text)onDelta(value.text);}
   if(kind==='error')throw new Error(typeof value.detail==='string'?value.detail.slice(0,250):'回复中断，请重试。');
   if(kind==='done')return validReply(value);return null;
  }
  let chunk;
  while((chunk=await read())!==null){pending+=chunk;let end;
   while((end=pending.indexOf('\n'))!==-1){const line=pending.slice(0,end).replace(/\r$/,'');pending=pending.slice(end+1);
    if(!line){const result=dispatch();if(result)return result;}
    else if(line.startsWith('event:'))event=line.slice(6).trim();
    else if(line.startsWith('data:'))data.push(line.slice(5).replace(/^ /,''));
   }
  }
  decoder.decode();throw interrupted();
 }catch(error){
  if(controller.signal.aborted){if(timedOut)throw new Error('回复等待超时，请重试。');throw controller.signal.reason;}
  if(error instanceof TypeError)throw new Error('连接不到小伴服务，请检查网络后重试。');throw error;
 }finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);if(reader){await reader.cancel().catch(()=>{});reader.releaseLock();}}
}
