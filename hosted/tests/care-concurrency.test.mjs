import {test,beforeEach,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {workerSource} from './fixtures/worker-source.mjs';

let mf,db,admin,other,mode,calls,pushes,hold,entered,pushHold,pushEntered;
const NOW='2026-11-08T06:00:00.000Z';
async function call(path,method='GET',data,token=admin?.token){const r=await mf.dispatchFetch('https://fixture/api'+path,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});return {status:r.status,data:await r.json()};}
async function ok(...args){const r=await call(...args);assert.equal(r.status,200,JSON.stringify(r));return r.data;}
async function account(){return ok('/auth/register','POST',{name:'合成用户',email:crypto.randomUUID()+'@example.com',password:'a-long-test-password',consent_health_data:true},null);}
beforeEach(async()=>{
 mode='false';calls=[];pushes=[];hold=null;entered=null;pushHold=null;pushEntered=null;
 mf=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{AUTH_PEPPER:'only-test-pepper-with-at-least-32-characters',AUTH_RATE_LIMIT:'1000',ENVIRONMENT:'test',TEST_NOW:NOW,CRON_TOKEN:'only-test-cron-token-at-least-32-characters',SCHEDULER_BATCH_SIZE:'30'},outboundService:async r=>{
  const b=await r.json();if(new URL(r.url).hostname==='api.jpush.cn'){pushes.push({body:b,authorization:r.headers.get('Authorization')});if(pushEntered)pushEntered();if(pushHold)await pushHold;return Response.json({msg_id:'queued-fixture-id',sendno:0});}
  calls.push(b);if(entered)entered();if(hold)await hold;
  if(mode==='error')return new Response('temporary failure',{status:503});
  const context=JSON.parse(b.messages.find(m=>m.role==='user').content);
  let answer;if(b.messages[0].content.includes('CARE_REPLY'))answer='我记得你在准备面试。现在最想先聊哪一部分？';
  else if(mode==='false')answer=JSON.stringify({should_contact:false,reason:'目前不需要额外打扰'});
  else if(mode==='malformed')answer='not json';
  else if(mode==='foreign')answer=JSON.stringify({should_contact:true,title:'关心一下',message:'你愿意聊聊吗？',topic_key:'interview',reason:'有依据',evidence_ids:['foreign-id']});
  else answer=JSON.stringify({should_contact:true,title:'面试准备还顺利吗',message:'你提到这周在准备面试，愿意说说现在的感受吗？',topic_key:'interview',reason:'用户明确确认的近期处境',evidence_ids:[context.memories[0].id]});
  return Response.json({choices:[{message:{content:answer}}]});
 }});
 db=await mf.getD1Database('DB');for(const f of (await readdir('drizzle')).filter(f=>f.endsWith('.sql')).sort())for(const s of (await readFile('drizzle/'+f,'utf8')).split('--> statement-breakpoint').map(s=>s.trim()).filter(Boolean))await db.prepare(s).run();
 admin=await account();other=await account();await db.prepare("UPDATE users SET data=json_set(data,'$.is_admin',json('true')) WHERE id=?").bind(admin.user.id).run();
 await ok('/admin/model-config','PUT',{mode:'api',api_url:'https://model.fixture/v1',api_key:'fixture-model-private-key',model:'fixture-model'});
 await ok('/admin/care-config','PATCH',{enabled:true,analysis_interval_minutes:120,cooldown_minutes:240,daily_limit:2});
 await ok('/preferences','PATCH',{model_consent:true});await ok('/care/preferences','PATCH',{enabled:true});
 await ok('/memories','POST',{text:'这周正在准备面试，担心自我介绍',category:'context'});
});
afterEach(async()=>{await mf?.dispose();});


import {handleCare,analyzeCareUser,deliverCareThread,runCareScheduled} from '../worker/care.mjs';
import {handleNativePush,sendNativePush} from '../worker/native-push.mjs';
const directEnv=()=>({DB:db,AUTH_PEPPER:'only-test-pepper-with-at-least-32-characters',ENVIRONMENT:'test',TEST_NOW:NOW});
function slowBodyRequest(path,payload){let entered,release;const started=new Promise(r=>entered=r),gate=new Promise(r=>release=r);let sent=false;return {started,release,request:{url:'https://fixture/api'+path,method:'PATCH',body:{getReader:()=>({async read(){if(sent)return {done:true};sent=true;entered();await gate;return {done:false,value:new TextEncoder().encode(JSON.stringify(payload))};},releaseLock(){}})}}};}
for(const [path,payload,handler]of [
 ['/admin/care-config',{analysis_interval_minutes:15},handleCare],
 ['/admin/push-config',{enabled:true,provider:'jpush',app_key:'f'.repeat(24),master_secret:'fixture-attacker-new-secret'},handleNativePush],
])test('role revoked while body pending forbids '+path,async()=>{
 const request=slowBodyRequest(path,payload);const pending=handler(request.request,directEnv(),admin.user,new Date(NOW));await request.started;
 await db.prepare("UPDATE users SET data=json_set(data,'$.is_admin',json('false')) WHERE id=?").bind(admin.user.id).run();request.release();const response=await pending;
 assert.equal(response.status,403,JSON.stringify(await response.json()));
});

test('care config CAS never silently loses an intervening administrator save',async()=>{
 const env=directEnv();let reads=0;const original=db.prepare.bind(db);env.DB={batch:db.batch.bind(db),prepare(sql){const stmt=original(sql);return {first:()=>stmt.first(),all:()=>stmt.all(),run:()=>stmt.run(),bind(...args){const bound=stmt.bind(...args);if(sql==='SELECT value FROM service_state WHERE state_key=?'&&args[0]==='care_settings')return {...bound,async first(){const row=await bound.first();if(++reads===1){const current=JSON.parse(row.value);await original('UPDATE service_state SET value=? WHERE state_key=?').bind(JSON.stringify({...current,daily_limit:5,revision:current.revision+1}),'care_settings').run();}return row;}};return bound;}};}};
 const response=await handleCare(new Request('https://fixture/api/admin/care-config',{method:'PATCH',body:JSON.stringify({analysis_interval_minutes:15})}),env,admin.user,new Date(NOW));
 const saved=JSON.parse((await original('SELECT value FROM service_state WHERE state_key=?').bind('care_settings').first()).value);
 assert.ok(response.status===409||saved.daily_limit===5,JSON.stringify({status:response.status,saved}));
});


test('disabling care between snapshot reads prevents a new care invitation',async()=>{
 const env=directEnv();let reads=0;const original=db.prepare.bind(db);env.DB={batch:db.batch.bind(db),prepare(sql){const stmt=original(sql);return {first:()=>stmt.first(),all:()=>stmt.all(),run:()=>stmt.run(),bind(...args){const bound=stmt.bind(...args);if(sql==='SELECT value FROM service_state WHERE state_key=?'&&args[0]==='care_settings')return {...bound,async first(){const row=await bound.first();if(++reads===1){const current=JSON.parse(row.value);await original('UPDATE service_state SET value=? WHERE state_key=?').bind(JSON.stringify({...current,enabled:false,revision:current.revision+1}),'care_settings').run();}return row;}};return bound;}};}};
 const previous=globalThis.fetch;globalThis.fetch=async(url,options)=>{const body=JSON.parse(options.body),context=JSON.parse(body.messages[1].content);return Response.json({choices:[{message:{content:JSON.stringify({should_contact:true,title:'only synthetic',message:'only synthetic invitation',topic_key:'fixture',evidence_ids:[context.memories[0].id],reason:'fixture'})}}]});};
 try{const result=await analyzeCareUser(env,admin.user.id,new Date(NOW),{force:true});assert.equal(result.created,0,JSON.stringify(result));}finally{globalThis.fetch=previous;}
});

test('read care messages are not subsequently pushed when native transport becomes configured',async()=>{
 mode='true';const id=(await ok('/admin/care-check','POST',{})).care_id;await ok('/care/'+id+'/read','POST',{});
 const c=await ok('/admin/push-config','PATCH',{enabled:true,provider:'jpush',app_key:'e'.repeat(24),master_secret:'fixture-native-private-secret'});await ok('/push/devices','POST',{provider:'jpush',registration_id:'fixture-owned-read-device',config_revision:c.revision});
 const previous=globalThis.fetch;let count=0;globalThis.fetch=async()=>{count++;return Response.json({msg_id:'fixture-msg'});};
 try{await deliverCareThread(directEnv(),admin.user.id,id,new Date(NOW));assert.equal(count,0);}finally{globalThis.fetch=previous;}
});

test('deleted evidence does not trigger a delayed native care notification',async()=>{
 mode='true';const id=(await ok('/admin/care-check','POST',{})).care_id;const m=(await ok('/memories')).items[0];await ok('/memories/'+m.id,'DELETE');
 const c=await ok('/admin/push-config','PATCH',{enabled:true,provider:'jpush',app_key:'e'.repeat(24),master_secret:'fixture-native-private-secret'});await ok('/push/devices','POST',{provider:'jpush',registration_id:'fixture-owned-delete-device',config_revision:c.revision});
 const previous=globalThis.fetch;let count=0;globalThis.fetch=async()=>{count++;return Response.json({msg_id:'fixture-msg'});};
 try{await deliverCareThread(directEnv(),admin.user.id,id,new Date(NOW));assert.equal(count,0);}finally{globalThis.fetch=previous;}
});


test('production scheduling interval enforces the actual hourly clock',async()=>{
 const env={...directEnv(),BACKGROUND_CHECK_INTERVAL_MINUTES:'60',CARE_SCHEDULER_CONFIGURED:'true'};
 const patch=values=>handleCare(new Request('https://fixture/api/admin/care-config',{method:'PATCH',body:JSON.stringify(values)}),env,admin.user,new Date(NOW));
 for(const interval of [15,30,90])assert.equal((await patch({analysis_interval_minutes:interval})).status,422);
 const response=await patch({analysis_interval_minutes:180});assert.equal(response.status,200);const value=await response.json();
 assert.equal(value.scheduler_interval_minutes,60);assert.equal(value.min_analysis_interval_minutes,60);assert.equal(value.analysis_interval_minutes,180);
});

for(const operation of ['analysis','reply'])test('consent withdrawal and restoration invalidates in-flight '+operation,async()=>{
 mode='true';let id;if(operation==='reply')id=(await ok('/admin/care-check','POST',{})).care_id;
 let start,release;const started=new Promise(r=>start=r);hold=new Promise(r=>release=r);entered=start;
 const pending=operation==='analysis'?call('/admin/care-check','POST',{}):call('/care/'+id+'/reply','POST',{message:'合成用户回复',request_id:crypto.randomUUID()});await started;
 await ok('/preferences','PATCH',{model_consent:false});await ok('/preferences','PATCH',{model_consent:true});release();const result=await pending;
 assert.equal(result.status,200);if(operation==='analysis')assert.equal(result.data.created,0);else{assert.equal(result.data.reply_status,'pending');assert.equal(result.data.messages.filter(m=>m.role==='assistant').length,1);}
});


test('a disabled scheduler still records a truthful heartbeat',async()=>{
 await ok('/admin/care-config','PATCH',{enabled:false});const value=await runCareScheduled(directEnv(),new Date(NOW));assert.equal(value.reason_code,'disabled');
 const health=JSON.parse((await db.prepare("SELECT value FROM service_state WHERE state_key='care_scheduler_status'").first()).value);assert.equal(health.last_run_at,NOW);assert.equal(health.evaluated,0);
});

test('an exact scheduler batch has no more pages after its final user',async()=>{
 const prefs=(await db.prepare('SELECT data FROM preferences WHERE user_id=?').bind(admin.user.id).first()).data;
 for(let i=0;i<5;i++){const id='synthetic-extra-'+i;await db.prepare('INSERT INTO users(id,email,password_hash,salt,data,created_at) VALUES(?,?,?,?,?,?)').bind(id,id+'@example.invalid','not-a-real-password-hash','only-fixture-salt',JSON.stringify({id,name:'synthetic',is_admin:false}),NOW).run();await db.prepare('INSERT INTO preferences(user_id,data,revision) VALUES(?,?,0)').bind(id,prefs).run();await db.prepare('INSERT INTO care_preferences(user_id,enabled,revision) VALUES(?,1,1)').bind(id).run();}
 const previous=globalThis.fetch;globalThis.fetch=async()=>Response.json({choices:[{message:{content:JSON.stringify({should_contact:false,reason:'fixture'})}}]});
 try{const result=await runCareScheduled(directEnv(),new Date(NOW));assert.equal(result.evaluated,6);assert.equal(result.has_more,false);}finally{globalThis.fetch=previous;}
});


test('a device rebound to a different account before HTTP is not sent the previous account notification',async()=>{
 const c=await ok('/admin/push-config','PATCH',{enabled:true,provider:'jpush',app_key:'e'.repeat(24),master_secret:'fixture-native-private-secret'}),registration='fixture-rebound-device-rid';
 await ok('/push/devices','POST',{provider:'jpush',registration_id:registration,config_revision:c.revision});
 const previous=globalThis.fetch;let count=0;globalThis.fetch=async()=>{count++;return Response.json({msg_id:'fixture-msg'});};
 try{await sendNativePush(directEnv(),admin.user.id,{type:'care_test'},{beforeSend:async()=>{await ok('/push/devices','POST',{provider:'jpush',registration_id:registration,config_revision:c.revision},other.token);return true;}});assert.equal(count,0);}finally{globalThis.fetch=previous;}
});


for(const change of ['expired','edited'])test('a source '+change+' after creation suppresses delayed native care',async()=>{
 mode='true';const id=(await ok('/admin/care-check','POST',{})).care_id,memory=(await ok('/memories')).items[0];
 if(change==='expired')await db.prepare("UPDATE memories SET data=json_set(data,'$.expires_at',?) WHERE id=?").bind(NOW,memory.id).run();
 else await db.prepare("UPDATE memories SET data=json_set(data,'$.text',?) WHERE id=?").bind('The original synthetic circumstance was corrected',memory.id).run();
 const c=await ok('/admin/push-config','PATCH',{enabled:true,provider:'jpush',app_key:'e'.repeat(24),master_secret:'fixture-native-private-secret'});await ok('/push/devices','POST',{provider:'jpush',registration_id:'fixture-changed-source-device',config_revision:c.revision});
 const previous=globalThis.fetch;let count=0;globalThis.fetch=async()=>{count++;return Response.json({msg_id:'fixture-msg'});};
 try{await deliverCareThread(directEnv(),admin.user.id,id,new Date(NOW));assert.equal(count,0);}finally{globalThis.fetch=previous;}
});
