import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {workerSource} from './fixtures/worker-source.mjs';

const encoder=new TextEncoder();
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const delta=text=>'data: '+JSON.stringify({choices:[{delta:{content:text}}]})+'\n\n';
const end='data: '+JSON.stringify({choices:[{delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
async function fixture(outboundService){
  const mf=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{AUTH_PEPPER:'test-only-32-character-server-pepper',AUTH_RATE_LIMIT:'1000',COMPANION_MODEL_BASE_URL:'https://model.fixture/v1',COMPANION_MODEL_API_KEY:'test-only-key',COMPANION_MODEL:'fixture'},outboundService});
  const db=await mf.getD1Database('DB');
  for(const file of (await readdir('drizzle')).filter(x=>x.endsWith('.sql')).sort())for(const sql of (await readFile('drizzle/'+file,'utf8')).split('--> statement-breakpoint').map(x=>x.trim()).filter(Boolean))await db.prepare(sql).run();
  let token;
  const call=(route,method='GET',data,stream=false)=>mf.dispatchFetch('https://fixture/api'+route,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{}),...(stream?{Accept:'text/event-stream'}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
  const account=await (await call('/auth/register','POST',{name:'合成账号',email:crypto.randomUUID()+'@example.com',password:'test-long-password',consent_health_data:true})).json();token=account.token;assert.ok(token);
  return {mf,db,call,consent:async()=>assert.equal((await call('/preferences','PATCH',{model_consent:true,support_style:'listen'})).status,200)};
}
async function readUntil(reader,pattern){let text='';const decoder=new TextDecoder();while(!pattern.test(text)){const part=await reader.read();if(part.done)break;text+=decoder.decode(part.value,{stream:true});}assert.match(text,pattern);return text;}
async function readRest(reader){let text='';const decoder=new TextDecoder();while(true){const part=await reader.read();if(part.done)break;text+=decoder.decode(part.value,{stream:true});}return text+decoder.decode();}
const events=text=>text.split(/\r?\n\r?\n/).filter(x=>x.startsWith('event:')).map(block=>({event:block.split('\n')[0].slice(7),data:JSON.parse(block.split('\n').find(x=>x.startsWith('data: ')).slice(6))}));

test('SSE preserves consent gating and stores the exact completed rule reply',async()=>{
  let upstream=0;const f=await fixture(async()=>{upstream++;throw Error('must not call');});
  try{const response=await f.call('/chat','POST',{message:'想聊聊今天'},true);assert.match(response.headers.get('content-type'),/text\/event-stream/);
    const received=events(await response.text());assert.deepEqual(received.map(e=>e.event),['delta','done']);assert.equal(received[1].data.mode,'local');assert.equal(received[0].data.text,received[1].data.reply);assert.equal(upstream,0);
    const history=await (await f.call('/chat')).json();assert.equal(history.items.length,2);assert.equal(history.items[1].content,received[1].data.reply);
  }finally{await f.mf.dispose();}
});

test('first real delta arrives before upstream completion and user lease covers the stream',async()=>{
  const release=deferred();let payload;const f=await fixture(async request=>{payload=await request.json();return new Response(new ReadableStream({start(controller){controller.enqueue(encoder.encode(delta('听见你')));release.promise.then(()=>{controller.enqueue(encoder.encode(delta('今天很累。')+end));controller.close();});}}),{headers:{'Content-Type':'text/event-stream'}});});
  let reader;
  try{await f.consent();const response=await f.call('/chat','POST',{message:'今天想休息'},true);assert.match(response.headers.get('content-type'),/text\/event-stream/);reader=response.body.getReader();
    const first=await readUntil(reader,/event: delta/);assert.match(first,/听见你/);assert.doesNotMatch(first,/event: done/);assert.equal(payload.stream,true);
    assert.equal((await f.db.prepare('SELECT count(*) n FROM chat').first()).n,0);
    assert.equal((await f.call('/preferences','PATCH',{support_style:'plan'})).status,409);
    release.resolve();const received=events(first+await readRest(reader));assert.equal(received.filter(e=>e.event==='delta').map(e=>e.data.text).join(''),'听见你今天很累。');
    const done=received.at(-1);assert.equal(done.event,'done');assert.equal(done.data.reply,'听见你今天很累。');assert.equal(done.data.mode,'model');
    assert.equal((await f.call('/preferences','PATCH',{support_style:'plan'})).status,200);
    const history=await (await f.call('/chat')).json();assert.deepEqual(history.items.map(m=>m.content),['今天想休息','听见你今天很累。']);
  }finally{release.resolve();await reader?.cancel().catch(()=>{});await f.mf.dispose();}
});

test('truncated output after a visible delta is an error and is never stored as a completed turn',async()=>{
  const release=deferred();const f=await fixture(async()=>new Response(new ReadableStream({start(controller){controller.enqueue(encoder.encode(delta('先听你说')));release.promise.then(()=>controller.close());}}),{headers:{'Content-Type':'text/event-stream'}}));let reader;
  try{await f.consent();const response=await f.call('/chat','POST',{message:'今天想聊聊'},true);assert.match(response.headers.get('content-type'),/text\/event-stream/);reader=response.body.getReader();const first=await readUntil(reader,/event: delta/);release.resolve();
    const received=events(first+await readRest(reader));assert.deepEqual(received.map(e=>e.event),['delta','error']);assert.match(received[1].data.detail,/中断/);assert.equal((await f.db.prepare('SELECT count(*) n FROM chat').first()).n,0);
    assert.equal((await f.call('/preferences','PATCH',{support_style:'plan'})).status,200);
  }finally{release.resolve();await reader?.cancel().catch(()=>{});await f.mf.dispose();}
});

test('upstream failure before any content gives an explicit rule fallback with done metadata',async()=>{
  const f=await fixture(async()=>new Response('unavailable',{status:503}));
  try{await f.consent();const response=await f.call('/chat','POST',{message:'今天想聊聊'},true);assert.match(response.headers.get('content-type'),/text\/event-stream/);const received=events(await response.text());assert.deepEqual(received.map(e=>e.event),['delta','done']);assert.equal(received.at(-1).data.mode,'local_fallback');assert.match(received[0].data.text,/模型服务暂不可用/);
  }finally{await f.mf.dispose();}
});

test('ordinary JSON chat requests remain compatible and do not request an upstream stream',async()=>{
  let payload;const f=await fixture(async request=>{payload=await request.json();return Response.json({choices:[{message:{content:'完整回复'}}]});});
  try{await f.consent();const response=await f.call('/chat','POST',{message:'聊聊今天'});assert.match(response.headers.get('content-type'),/application\/json/);const result=await response.json();assert.equal(result.reply,'完整回复');assert.equal(result.mode,'model');assert.equal(payload.stream,undefined);
  }finally{await f.mf.dispose();}
});

test('cancelling the browser stream promptly releases its lease without storing partial chat',async()=>{
  const release=deferred();const f=await fixture(async()=>new Response(new ReadableStream({start(controller){controller.enqueue(encoder.encode(delta('还在生成')));release.promise.then(()=>{try{controller.enqueue(encoder.encode(delta('晚到的内容')+end));controller.close();}catch{}});}}),{headers:{'Content-Type':'text/event-stream'}}));let reader;
  try{await f.consent();const response=await f.call('/chat','POST',{message:'想聊聊今天'},true);reader=response.body.getReader();await readUntil(reader,/event: delta/);await reader.cancel();
    for(let n=0;n<100&&(await f.db.prepare('SELECT count(*) n FROM leases').first()).n;n++)await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal((await f.db.prepare('SELECT count(*) n FROM leases').first()).n,0);assert.equal((await f.db.prepare('SELECT count(*) n FROM chat').first()).n,0);
    // The Node outbound-service bridge only notices remote cancellation when
    // it next writes. Direct upstream AbortSignal teardown is covered by the
    // model-stream tests; here late provider output must never get persisted.
    release.resolve();await new Promise(resolve=>setTimeout(resolve,30));assert.equal((await f.db.prepare('SELECT count(*) n FROM chat').first()).n,0);
  }finally{release.resolve();await reader?.cancel().catch(()=>{});await f.mf.dispose();}
});
