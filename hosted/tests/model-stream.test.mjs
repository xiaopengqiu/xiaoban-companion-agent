import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {ModelRuntimeError} from '../worker/model-admin.mjs';

const encoder=new TextEncoder();
const originalFetch=globalThis.fetch;
const originalSetTimeout=globalThis.setTimeout;
afterEach(()=>{globalThis.fetch=originalFetch;globalThis.setTimeout=originalSetTimeout;});
const env={DB:{prepare:()=>({first:async()=>null})},COMPANION_MODEL_BASE_URL:'https://model.fixture/v1',COMPANION_MODEL_API_KEY:'test-private-key',COMPANION_MODEL:'fixture-model'};
const messages=[{role:'user',content:'这是合成连接测试'}];
const delta=(text,finish=null)=>'data: '+JSON.stringify({choices:[{index:0,delta:{content:text},finish_reason:finish}]})+'\n\n';
const done='data: [DONE]\n\n';
async function runtime(){const module=await import('../worker/model-stream.mjs').catch(()=>({}));assert.equal(typeof module.streamModel,'function','streamModel must support incremental model output');return module.streamModel;}
function response(body,headers={'Content-Type':'text/event-stream'}){return new Response(body,{headers});}
function chunks(values){return new ReadableStream({start(c){for(const value of values)c.enqueue(typeof value==='string'?encoder.encode(value):value);c.close();}});}
async function collect(iterator){let text='';for await(const part of iterator)text+=part;return text;}

test('first model chunk is yielded before the provider finishes and requests actual streaming',async()=>{
 const streamModel=await runtime();let upstream,finished=false;
 globalThis.fetch=async(url,options)=>{assert.equal(url,'https://model.fixture/v1/chat/completions');assert.equal(options.redirect,'manual');assert.equal(options.headers.Authorization,'Bearer test-private-key');assert.deepEqual(JSON.parse(options.body),{model:'fixture-model',messages,max_tokens:700,temperature:.4,stream:true});return response(new ReadableStream({start(c){upstream=c;c.enqueue(encoder.encode(delta('我在听')));}}));};
 const iterator=streamModel(env,messages);
 const first=await iterator.next();assert.equal(first.value,'我在听');assert.equal(first.done,false);assert.equal(finished,false);
 upstream.enqueue(encoder.encode(delta('，慢慢说。','stop')+done));finished=true;upstream.close();
 assert.equal(await collect(iterator),'，慢慢说。');
});

test('SSE parser handles split UTF-8, CRLF, multiline data and ignores reasoning/tool output',async()=>{
 const streamModel=await runtime();
 const data=': heartbeat\r\n\r\ndata: {"choices":[\r\ndata: {"index":0,"delta":{"reasoning_content":"private reasoning","tool_calls":[{}]}}]}\r\n\r\n'+delta('中文🙂','stop').replaceAll('\n','\r\n')+done.replaceAll('\n','\r\n');
 const bytes=encoder.encode(data);globalThis.fetch=async()=>response(chunks(Array.from(bytes,byte=>new Uint8Array([byte]))));
 assert.equal(await collect(streamModel(env,messages)),'中文🙂');
});

test('JSON-only gateways remain compatible without pretending to stream characters',async()=>{
 const streamModel=await runtime();globalThis.fetch=async()=>Response.json({choices:[{message:{content:' 完整网关回复 '}}]});
 const iterator=streamModel(env,messages);assert.deepEqual(await iterator.next(),{value:'完整网关回复',done:false});assert.equal((await iterator.next()).done,true);
});

test('caller cancellation aborts the upstream request and cancels the unread response',async()=>{
 const streamModel=await runtime();let upstreamSignal,cancelled=false;
 globalThis.fetch=async(url,options)=>{upstreamSignal=options.signal;return response(new ReadableStream({start(c){c.enqueue(encoder.encode(delta('第一段')));},cancel(){cancelled=true;}}));};
 const controller=new AbortController(),iterator=streamModel(env,messages,{signal:controller.signal});assert.equal((await iterator.next()).value,'第一段');
 const reason=new DOMException('Stopped by user','AbortError');controller.abort(reason);
 await assert.rejects(iterator.next(),error=>error===reason);assert.equal(upstreamSignal.aborted,true);assert.equal(cancelled,true);
});

test('cancellation promptly rejects a pending read without waiting for provider cleanup',async()=>{
 const streamModel=await runtime();let cancelled=false,releaseCleanup;
 const cleanup=new Promise(resolve=>{releaseCleanup=resolve;});
 globalThis.fetch=async()=>response(new ReadableStream({start(c){c.enqueue(encoder.encode(delta('首段')));},cancel(){cancelled=true;return cleanup;}}));
 const controller=new AbortController(),iterator=streamModel(env,messages,{signal:controller.signal});assert.equal((await iterator.next()).value,'首段');
 const pending=iterator.next();controller.abort();
 try{
  const result=await Promise.race([pending.then(()=> 'resolved',error=>error.name),new Promise(resolve=>setTimeout(()=>resolve('still waiting'),100))]);
  assert.equal(result,'AbortError');assert.equal(cancelled,true);
 }finally{releaseCleanup();await pending.catch(()=>{});}
});

test('API total deadline aborts a waiting body instead of resetting for every chunk',async()=>{
 const streamModel=await runtime();let signal,expire,timeout;
 globalThis.fetch=async(url,options)=>{signal=options.signal;return response(new ReadableStream({start(c){c.enqueue(encoder.encode(delta('首段')));}}));};
 globalThis.setTimeout=(callback,milliseconds)=>{expire=callback;timeout=milliseconds;return undefined;};
 const iterator=streamModel(env,messages);assert.equal((await iterator.next()).value,'首段');const pending=iterator.next();assert.equal(timeout,20000);expire();
 await assert.rejects(pending,ModelRuntimeError);assert.equal(signal.aborted,true);
});

test('local gateways retain their transport identity, specific model and 50-second deadline',async()=>{
 const streamModel=await runtime(),pepper='test-only-local-config-encryption-secret';
 const config={mode:'local',gateway_url:'https://gateway.fixture/v1',gateway_key:'test-gateway-key',local_provider:'codex',local_model:'fixture-cli-model'};
 const hash=await crypto.subtle.digest('SHA-256',encoder.encode('xiaoban-model-settings-v1\0'+pepper)),key=await crypto.subtle.importKey('raw',hash,'AES-GCM',false,['encrypt']);
 const iv=crypto.getRandomValues(new Uint8Array(12)),raw=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode('xiaoban-model-settings-v1')},key,encoder.encode(JSON.stringify(config)));
 const encoded=value=>btoa(String.fromCharCode(...new Uint8Array(value)));
 const localEnv={AUTH_PEPPER:pepper,DB:{prepare:()=>({first:async()=>({value:JSON.stringify({version:1,iv:encoded(iv),data:encoded(raw)})})})}};
 let timeout;globalThis.setTimeout=(callback,milliseconds)=>{timeout=milliseconds;return undefined;};
 globalThis.fetch=async(url,options)=>{assert.equal(url,'https://gateway.fixture/v1/chat/completions');const payload=JSON.parse(options.body);assert.equal(payload.stream,true);assert.equal(payload.model,'codex');assert.equal(payload.local_model,'fixture-cli-model');assert.equal(options.headers.Authorization,'Bearer test-gateway-key');return Response.json({choices:[{message:{content:'网关测试成功'}}]});};
 assert.equal(await collect(streamModel(localEnv,messages)),'网关测试成功');assert.equal(timeout,50000);
});

test('leaving the generator cancels an unfinished provider stream',async()=>{
 const streamModel=await runtime();let cancelled=false,upstreamSignal;
 globalThis.fetch=async(url,options)=>{upstreamSignal=options.signal;return response(new ReadableStream({start(c){c.enqueue(encoder.encode(delta('第一段')));},cancel(){cancelled=true;}}));};
 const iterator=streamModel(env,messages);assert.equal((await iterator.next()).value,'第一段');await iterator.return();assert.equal(cancelled,true);assert.equal(upstreamSignal.aborted,true);
});

test('a broken stream after visible output fails instead of treating partial text as complete',async()=>{
 const streamModel=await runtime();globalThis.fetch=async()=>response(chunks([delta('已经显示的部分')]));
 const iterator=streamModel(env,messages);assert.equal((await iterator.next()).value,'已经显示的部分');await assert.rejects(iterator.next(),ModelRuntimeError);
});

test('a provider finish_reason is sufficient when the final DONE event is absent',async()=>{
 const streamModel=await runtime();globalThis.fetch=async()=>response(chunks([delta('完成。','stop')]));assert.equal(await collect(streamModel(env,messages)),'完成。');
});

test('empty, malformed, oversized and incomplete replies return only a safe runtime error',async()=>{
 const streamModel=await runtime();
 for(const body of [done,'data: not-json\n\n',delta('x'.repeat(6001),'stop')+done,'data: '+ ' '.repeat(128001),delta('unfinished')+'data: {"choices":[]}',delta('')+done,'data: {"error":{"message":"SECRET diagnostics"}}\n\n']){
  globalThis.fetch=async()=>response(chunks([body]));
  await assert.rejects(collect(streamModel(env,messages)),error=>error instanceof ModelRuntimeError&&error.message==='模型服务暂时不可用，请检查管理员配置');
 }
});

test('redirect and HTTP errors are never followed or exposed',async()=>{
 const streamModel=await runtime();for(const status of [302,307,401,500]){globalThis.fetch=async()=>new Response('SECRET provider detail',{status,headers:{Location:'https://untrusted.fixture'}});await assert.rejects(collect(streamModel(env,messages)),error=>error instanceof ModelRuntimeError&&!error.message.includes('SECRET'));}
});

test('pre-aborted requests never contact the provider',async()=>{
 const streamModel=await runtime();let calls=0;globalThis.fetch=async()=>{calls++;throw Error();};const controller=new AbortController();controller.abort();await assert.rejects(collect(streamModel(env,messages,{signal:controller.signal})),error=>error.name==='AbortError');assert.equal(calls,0);
});

test('cancellation after the final chunk cannot be mistaken for successful completion',async()=>{
 const streamModel=await runtime();for(const reply of [()=>response(chunks([delta('最后一段','stop')])),()=>Response.json({choices:[{message:{content:'最后一段'}}]})]){
  globalThis.fetch=async()=>reply();const controller=new AbortController(),iterator=streamModel(env,messages,{signal:controller.signal});assert.equal((await iterator.next()).value,'最后一段');controller.abort();await assert.rejects(iterator.next(),error=>error.name==='AbortError');
 }
});
