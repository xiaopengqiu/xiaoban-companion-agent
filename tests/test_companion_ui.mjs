import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {SessionGuard,ReminderTracker} from '../web/lifecycle.mjs';
import {memoryGroups, threadPayload, dueThreads, DueCheckGate, localInput, modelConfigPayload} from '../web/companion-ui.mjs';
import * as careUI from '../web/care-ui.mjs';

test('expired personal context stays editable but is separated from current understanding',()=>{
  const groups=memoryGroups([{id:'a',category:'preference'},{id:'b',category:'context',is_active:true},{id:'c',category:'context',is_active:false},{id:'d',category:'boundary'},{id:'e',category:'pattern'}]);
  assert.deepEqual(groups.map(g=>[g.id,g.items.map(m=>m.id)]),[['stable',['a']],['context',['b']],['boundary',['d']],['pattern',['e']],['expired',['c']]]);
});
test('unrelated edits preserve a past follow-up without rescheduling it',()=>{
  const original={title:'面试',details:'准备介绍',status:'active',outcome:'',event_at:null,follow_up_enabled:true,follow_up_at:'2026-01-01T00:00:00.000Z'};
  const values={...original,title:'面试准备',event_at:'',follow_up_at:localInput(original.follow_up_at)};
  const payload=threadPayload(values,original);
  assert.equal(payload.title,'面试准备');
  assert.equal(Object.hasOwn(payload,'follow_up_at'),false);
  assert.equal(payload.follow_up_enabled,true);
});
test('follow-up requires a valid explicit date and does not turn on from event date alone',()=>{
  const base={title:'面试',details:'',status:'active',outcome:'',event_at:'2026-11-10T10:00',follow_up_at:'',follow_up_enabled:false};
  assert.equal(threadPayload(base).follow_up_enabled,false);
  assert.equal(threadPayload(base).follow_up_at,null);
  assert.throws(()=>threadPayload({...base,follow_up_enabled:true}),/跟进时间/);
  assert.throws(()=>threadPayload({...base,event_at:'invalid'}),/时间/);
});
test('re-enabling follow-up explicitly submits its chosen date for server validation',()=>{
  const original={title:'面试',follow_up_enabled:false,follow_up_at:'2026-11-10T10:00:00.000Z'};
  assert.equal(threadPayload({...original,follow_up_enabled:true,follow_up_at:localInput(original.follow_up_at)},original).follow_up_at,original.follow_up_at);
});
test('only active opted-in recent due threads need browser-open evaluation',()=>{
  const now=Date.parse('2026-11-08T12:00:00Z');
  const t={id:'a',revision:2,status:'active',follow_up_enabled:true,follow_up_at:'2026-11-08T11:00:00Z'};
  const threads=[t,{...t,id:'b',follow_up_enabled:false},{...t,id:'c',status:'completed'},{...t,id:'d',follow_up_at:'2026-11-07T10:00:00Z'},{...t,id:'e',follow_up_at:'2026-11-09T10:00:00Z'}];
  assert.deepEqual(dueThreads(threads,{enabled:true},[],now).map(t=>t.id),['a']);
  assert.equal(dueThreads(threads,{enabled:false},[],now).length,0);
  assert.equal(dueThreads(threads,{enabled:true,life_followups_enabled:false},[],now).length,0);
  assert.equal(dueThreads(threads,{enabled:true},[{dedupe_key:'life:a:2',status:'done'}],now).length,0);
});
test('due-check gate prevents overlapping polls and stale account completions',()=>{
  const gate=new DueCheckGate();
  const first=gate.begin(1000);assert.ok(first);
  assert.equal(gate.begin(1001),null);
  gate.reset();const next=gate.begin(1002);assert.ok(next);
  gate.finish(first);assert.equal(gate.begin(1003),null);
  gate.finish(next);assert.equal(gate.begin(5000),null);
  assert.ok(gate.begin(302000));
});

test('admin blank secret fields preserve server secrets instead of sending empty replacements',()=>{
  const payload=modelConfigPayload({mode:'api',api_url:'https://api.example/v1',api_key:'  ',model:'test-model',local_provider:'codex',gateway_url:'',gateway_key:''});
  assert.equal(payload.api_url,'https://api.example/v1');
  assert.equal(Object.hasOwn(payload,'api_key'),false);assert.equal(Object.hasOwn(payload,'gateway_key'),false);
  assert.equal(modelConfigPayload({...payload,api_key:'test-only-new-secret'}).api_key,'test-only-new-secret');
});

test('local CLI model selection stays separate from the API model',()=>{
  const config=modelConfigPayload({mode:'local',model:'api-model',local_model:'cli-model',local_provider:'claude'});
  assert.equal(config.local_model,'cli-model');assert.equal(config.model,'api-model');
});

test('ordinary users never fetch admin resources; admin user lists clear when role changes',async()=>{
  const calls=[],elements=new Map();let isAdmin=false;
  const context={...careUI,document:{querySelector(selector){if(selector.startsWith('meta'))return null;if(!elements.has(selector))elements.set(selector,{});return elements.get(selector);}},localStorage:{getItem:()=> 'test-session'},SessionGuard,ReminderTracker,DueCheckGate,AbortController,setTimeout,clearTimeout,
    fetch:async path=>{calls.push(path);const data=path==='/api/dashboard'?{user:{is_admin:isAdmin}}:path==='/api/admin/users'?{items:[{id:'owner',name:'Owner',email:'owner@example.com',is_admin:true},{id:'demo',name:'Demo',email:'demo@example.com',is_demo:true,is_admin:false}]}:path==='/api/preferences'?{}:path==='/api/integrations/status'?{}:{items:[]};return {ok:true,status:200,json:async()=>data};}};
  vm.createContext(context);
  const source=readFileSync(new URL('../web/app.js',import.meta.url),'utf8').replace(/^import .*;$/gm,'').split('setInterval(checkNewMessages')[0];
  vm.runInContext(source+';globalThis.load=loadData;globalThis.list=()=>state.adminUsers;globalThis.roles=()=>renderAdminUsers();',context);
  await context.load();assert.equal(calls.some(p=>p.startsWith('/api/admin/')),false);
  isAdmin=true;await context.load();assert.ok(calls.includes('/api/admin/users'));assert.equal(context.list().length,2);
  assert.match(context.roles(),/data-admin-role="owner"/);assert.doesNotMatch(context.roles(),/data-admin-role="demo"/);
  isAdmin=false;await context.load();assert.equal(context.list().length,0);assert.equal(context.roles(),'');
});
