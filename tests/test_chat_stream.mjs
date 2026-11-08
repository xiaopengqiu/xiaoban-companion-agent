import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {SessionGuard,ReminderTracker} from '../web/lifecycle.mjs';
import {DueCheckGate} from '../web/companion-ui.mjs';
import * as careUI from '../web/care-ui.mjs';

const module=await import('../web/chat-stream.mjs').catch(()=>({}));
const requestChat=(...args)=>{assert.equal(typeof module.requestChat,'function','stream request helper missing');return module.requestChat(...args);};
const encoder=new TextEncoder();
const frame=(event,data)=>`event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
function controlled(){let controller;let cancelled=false;const body=new ReadableStream({start(value){controller=value;},cancel(){cancelled=true;}});return {response:new Response(body,{headers:{'Content-Type':'text/event-stream'}}),push(text){controller.enqueue(encoder.encode(text));},close(){controller.close();},get cancelled(){return cancelled;}};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('chat renders streamed Chinese text before completion and requests the actual SSE transport',async()=>{
 const stream=controlled(),seen=[];let options;
 const pending=requestChat({message:'今天想聊聊',token:'test-token',onDelta:text=>seen.push(text),fetcher:async(path,value)=>{assert.equal(path,'/api/chat');options=value;return stream.response;}});
 stream.push(frame('delta',{text:'我在'}));await tick();assert.deepEqual(seen,['我在']);
 assert.equal(options.headers.Accept,'text/event-stream');assert.equal(options.headers.Authorization,'Bearer test-token');assert.deepEqual(JSON.parse(options.body),{message:'今天想聊聊'});
 stream.push(frame('delta',{text:'听。'}));stream.push(frame('done',{reply:'我在听。',mode:'model',evidence:[],suggestions:[]}));stream.close();
 assert.equal((await pending).reply,'我在听。');assert.deepEqual(seen,['我在','听。']);
});

test('SSE decoder handles UTF8 split inside a Chinese character and split CRLF records',async()=>{
 let controller;const body=new ReadableStream({start(c){controller=c;}});const seen=[];
 const pending=requestChat({message:'hello',onDelta:text=>seen.push(text),fetcher:async()=>new Response(body,{headers:{'Content-Type':'text/event-stream; charset=utf-8'}})});
 const bytes=encoder.encode(frame('delta',{text:'你好🌱'})+frame('done',{reply:'你好🌱',mode:'model'}));
 for(const byte of bytes)controller.enqueue(Uint8Array.of(byte));controller.close();
 assert.equal((await pending).reply,'你好🌱');assert.deepEqual(seen,['你好🌱']);
});

test('incomplete and error streams retain the delivered prefix without claiming success',async()=>{
 for(const ending of ['',frame('error',{detail:'模型回复中断，请重试。'})]){
  const seen=[];const pending=requestChat({message:'hello',onDelta:text=>seen.push(text),fetcher:async()=>new Response(frame('delta',{text:'已经看到'})+ending,{headers:{'Content-Type':'text/event-stream'}})});
  await assert.rejects(pending,/中断|未完成/);assert.deepEqual(seen,['已经看到']);
 }
});

test('stopping cancels a pending stream reader and does not wait for another chunk',async()=>{
 const stream=controlled(),controller=new AbortController(),seen=[];
 const pending=requestChat({message:'hello',signal:controller.signal,onDelta:text=>seen.push(text),fetcher:async()=>stream.response});
 stream.push(frame('delta',{text:'前半句'}));await tick();controller.abort();
 await assert.rejects(pending,error=>error.name==='AbortError');assert.equal(stream.cancelled,true);assert.deepEqual(seen,['前半句']);
});

test('older JSON backends remain usable and authorization failures keep their status',async()=>{
 const reply={reply:'旧服务完整回复',mode:'local'};const seen=[];
 const result=await requestChat({message:'hello',onDelta:text=>seen.push(text),fetcher:async()=>Response.json(reply)});
 assert.deepEqual(result,reply);assert.deepEqual(seen,[]);
 await assert.rejects(requestChat({message:'hello',fetcher:async()=>Response.json({detail:'需要登录'},{status:401})}),error=>error.status===401);
});

test('malformed event data and a done event without a reply are rejected',async()=>{
 for(const text of ['event: delta\ndata: {broken}\n\n',frame('delta',{text:42})+frame('done',{reply:'x'}),frame('done',{mode:'model'})]){
  await assert.rejects(requestChat({message:'hello',fetcher:async()=>new Response(text,{headers:{'Content-Type':'text/event-stream'}})}));
 }
});

test('a hung model response times out and cancels its reader',async()=>{
 const stream=controlled();await assert.rejects(requestChat({message:'hello',timeoutMs:10,fetcher:async()=>stream.response}),/超时/);assert.equal(stream.cancelled,true);
});

test('already cancelled requests never send a message and oversized streams are rejected',async()=>{
 const abort=new AbortController();abort.abort();let calls=0;
 await assert.rejects(requestChat({message:'hello',signal:abort.signal,fetcher:async()=>{calls++;return Response.json({reply:'never'});}}),error=>error.name==='AbortError');assert.equal(calls,0);
 await assert.rejects(requestChat({message:'hello',fetcher:async()=>new Response(frame('delta',{text:'x'.repeat(6001)})+frame('done',{reply:'done'}),{headers:{'Content-Type':'text/event-stream'}})}),/中断/);
});

function appHarness(fetcher){
 const nodes=new Map();const node=selector=>{if(!nodes.has(selector))nodes.set(selector,{textContent:'',disabled:false,scrollHeight:300,clientHeight:150,scrollTop:0,value:'',append(){},remove(){this.removed=true;},close(){},classList:{remove(){}}});return nodes.get(selector);};
 const document={querySelector(selector){if(selector.startsWith('meta'))return null;return node(selector);},querySelectorAll(){return [];},createElement(){return {className:'',textContent:''};}};
 const context={...careUI,document,localStorage:{getItem:()=> 'alice',removeItem(){}},SessionGuard,ReminderTracker,DueCheckGate,AbortController,setTimeout,clearTimeout,fetch:fetcher,requestChat:options=>module.requestChat({...options,fetcher}),console};vm.createContext(context);
 const source=readFileSync(new URL('../web/app.js',import.meta.url),'utf8').replace(/^import .*;$/gm,'').split('setInterval(checkNewMessages')[0];
 vm.runInContext(source+`;globalThis.testApp={state,sendChat,loadData,logout,renderChat,stopChat:()=>stopChat(),replaceAccount(token){state.token=token;sessionGuard.replace(token);}}; updateChatPanel=()=>{}; renderAuth=()=>{};`,context);
 context.testApp.state.dashboard={user:{is_admin:false}};return {app:context.testApp,node};
}

test('sendChat updates one assistant bubble before done and preserves draft input',async()=>{
 const stream=controlled();const {app,node}=appHarness(async(path,options)=>options?.method==='POST'?stream.response:Response.json(path==='/api/memories'?{items:[]}:{items:[{role:'user',content:'说说今天'},{role:'assistant',content:'我在听你说。'}]}));
 const pending=app.sendChat('说说今天');node('#chat-input').value='下一句草稿';stream.push(frame('delta',{text:'我在听'}));await tick();
 assert.equal(app.state.messages.length,2);assert.equal(app.state.messages[1].content,'我在听');assert.equal(node('.message.streaming').textContent,'我在听');assert.equal(node('#chat-input').value,'下一句草稿');
 stream.push(frame('delta',{text:'你说。'}));stream.push(frame('done',{reply:'我在听你说。',mode:'model'}));stream.close();await pending;
 assert.equal(app.state.sending,false);assert.equal(app.state.messages.filter(m=>m.role==='assistant').length,1);
});

test('stop retains partial text, and logout prevents a late delta from crossing accounts',async()=>{
 const stream=controlled();const {app}=appHarness(async()=>stream.response);const pending=app.sendChat('hello');stream.push(frame('delta',{text:'一部分'}));await tick();app.stopChat();await pending;
 assert.equal(app.state.messages.at(-1).content,'一部分');assert.equal(app.state.chatStatus,'已停止生成');assert.equal(app.state.sending,false);
 const later=controlled();const second=appHarness(async()=>later.response);const waiting=second.app.sendChat('another');later.push(frame('delta',{text:'旧账号内容'}));await tick();second.app.logout({revoke:false,cleanupPush:false});second.app.replaceAccount('bob');await waiting;
 assert.equal(second.app.state.messages.length,0);assert.equal(second.app.state.sending,false);
});

test('refresh during generation cannot replace the visible in-progress conversation',async()=>{
 const stream=controlled();const {app}=appHarness(async(path,options)=>options?.method==='POST'?stream.response:Response.json(path==='/api/dashboard'?{user:{is_admin:false}}:path==='/api/preferences'?{}:path==='/api/integrations/status'?{}:{items:[]}));
 const pending=app.sendChat('hello');stream.push(frame('delta',{text:'未完成的回复'}));await tick();await app.loadData();
 assert.equal(app.state.messages.length,2);assert.equal(app.state.messages.at(-1).content,'未完成的回复');app.stopChat();await pending;
});

test('expired chat session logs out instead of retaining another authenticated-looking conversation',async()=>{
 const {app}=appHarness(async()=>Response.json({detail:'登录已失效'},{status:401}));await app.sendChat('hello');assert.equal(app.state.token,null);assert.equal(app.state.messages.length,0);assert.equal(app.state.sending,false);
});

test('rendered streaming bubbles escape model HTML and expose a stop button',()=>{
 const {app}=appHarness(async()=>Response.json({}));app.state.messages=[{role:'assistant',content:'<img src=x onerror=alert(1)>',streaming:true}];app.state.sending=true;
 const html=app.renderChat();assert.match(html,/id="stop-chat"/);assert.match(html,/aria-busy="true"/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img src=x/);
});
