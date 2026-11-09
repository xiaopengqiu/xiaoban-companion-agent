import {test,beforeEach,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {workerSource} from './fixtures/worker-source.mjs';

let mf,db,admin,other,mode,calls,pushes,hold,entered,pushHold,pushEntered,sourceFixture;
const NOW='2026-11-08T06:00:00.000Z';
async function call(path,method='GET',data,token=admin?.token){const r=await mf.dispatchFetch('https://fixture/api'+path,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});return {status:r.status,data:await r.json()};}
async function ok(...args){const r=await call(...args);assert.equal(r.status,200,JSON.stringify(r));return r.data;}
async function account(){return ok('/auth/register','POST',{name:'合成用户',email:crypto.randomUUID()+'@example.com',password:'a-long-test-password',consent_health_data:true},null);}
beforeEach(async()=>{
 mode='false';calls=[];pushes=[];hold=null;entered=null;pushHold=null;pushEntered=null;sourceFixture=null;
 mf=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{AUTH_PEPPER:'only-test-pepper-with-at-least-32-characters',AUTH_RATE_LIMIT:'1000',ENVIRONMENT:'test',TEST_NOW:NOW,CRON_TOKEN:'only-test-cron-token-at-least-32-characters',SCHEDULER_BATCH_SIZE:'30'},outboundService:async r=>{
  const b=await r.json();if(new URL(r.url).hostname==='api.jpush.cn'){pushes.push({body:b,authorization:r.headers.get('Authorization')});if(pushEntered)pushEntered();if(pushHold)await pushHold;return Response.json({msg_id:'queued-fixture-id',sendno:0});}
  calls.push(b);if(entered)entered();if(hold)await hold;
  if(mode==='error')return new Response('temporary failure',{status:503});
  const context=JSON.parse(b.messages.find(m=>m.role==='user').content);
  let answer;if(b.messages[0].content.includes('CARE_REPLY'))answer='我记得你在准备面试。现在最想先聊哪一部分？';
  else if(mode==='false')answer=JSON.stringify({should_contact:false,reason:'目前不需要额外打扰'});
  else if(mode==='false-extra')answer=JSON.stringify({should_contact:false,reason:'目前不需要额外打扰',title:'多余字段'});
  else if(mode==='malformed')answer='not json';
  else if(mode==='foreign')answer=JSON.stringify({should_contact:true,title:'关心一下',message:'你愿意聊聊吗？',topic_key:'interview',reason:'有依据',evidence_ids:['foreign-id']});
  else if(mode==='duplicate')answer=JSON.stringify({should_contact:true,title:'关心一下',message:'你愿意聊聊吗？',topic_key:'interview',thread_id:null,reason:'有依据',evidence_ids:[context.memories[0].id,context.memories[0].id]});
  else if(mode==='thread-mismatch')answer=JSON.stringify({should_contact:true,title:'关心一下',message:'你愿意聊聊吗？',topic_key:'interview',thread_id:'not-selected',reason:'有依据',evidence_ids:[context.memories[0].id]});
  else if(mode==='thread')answer=JSON.stringify({should_contact:true,title:'接着聊聊',message:'愿意说说进展吗？',topic_key:'thread',thread_id:sourceFixture.id,evidence_ids:[sourceFixture.id,context.memories[0].id],reason:'持续关注'});
  else answer=JSON.stringify({should_contact:true,title:'面试准备还顺利吗',message:'你提到这周在准备面试，愿意说说现在的感受吗？',topic_key:'interview',thread_id:null,reason:'用户明确确认的近期处境',evidence_ids:[context.memories[0].id]});
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

test('admin frequency configuration, user consent and native settings are access controlled',async()=>{
 for(const [path,method,data]of [['/admin/care-config','GET'],['/admin/care-config','PATCH',{enabled:true}],['/admin/care-check','POST',{}],['/admin/push-config','GET']])assert.equal((await call(path,method,data,other.token)).status,403);
 assert.equal((await call('/care/preferences','PATCH',{enabled:true},other.token)).status,409);
 for(const data of [{analysis_interval_minutes:1},{daily_limit:0},{cooldown_minutes:true},{enabled:'yes'},{unexpected:true}])assert.equal((await call('/admin/care-config','PATCH',data)).status,422);
 const c=await ok('/admin/care-config');assert.equal(c.analysis_interval_minutes,120);assert.equal(c.model_ready,true);assert.equal(c.scheduler_configured,false);
});
test('no-contact decision remains silent and advances the durable due time',async()=>{
 const r=await ok('/admin/care-check','POST',{});assert.equal(r.created,0);assert.equal(r.reason_code,'no_attention_needed');
 const list=await ok('/care');assert.equal(list.items.length,0);assert.equal(list.unread_count,0);assert.equal(list.status.next_check_at,'2026-11-08T08:00:00.000Z');
 await ok('/internal/evaluate','POST',{},'only-test-cron-token-at-least-32-characters');assert.equal(calls.length,1);assert.equal(pushes.length,0);
 assert.equal((await db.prepare('SELECT count(*) n FROM care_messages').first()).n,0);
});
test('decision contract links exactly one selected active thread and snapshots provenance',async()=>{
 const source=await ok('/threads','POST',{title:'<面试 & 准备>',details:'周五'});
 sourceFixture=source;mode='thread';const id=(await ok('/admin/care-check','POST',{})).care_id,detail=await ok('/care/'+id);assert.deepEqual(detail.thread.source_thread,{id:source.id,title:source.title,revision:1});
 await db.prepare("UPDATE care_threads SET data=json_set(data,'$.source_thread.title',json('5')) WHERE id=?").bind(id).run();assert.equal((await ok('/care/'+id)).thread.source_thread,null);assert.equal((await ok('/care')).items[0].source_thread,null);
});
test('true decision creates one tenant-scoped inbox thread, supports read and contextual idempotent replies',async()=>{
 mode='true';const created=await ok('/admin/care-check','POST',{});assert.equal(created.created,1);
 const list=await ok('/care');assert.equal(list.items.length,1);assert.equal(list.unread_count,1);assert.equal(list.items[0].source,'ai');
 const id=created.care_id;assert.equal((await call('/care/'+id,'GET',undefined,other.token)).status,404);assert.equal((await call('/care/'+id+'/reply','POST',{message:'foreign',request_id:crypto.randomUUID()},other.token)).status,404);
 assert.equal((await ok('/care/'+id)).messages[0].role,'assistant');await ok('/care/'+id+'/read','POST',{});assert.equal((await ok('/care')).unread_count,0);
 const data={message:'我最担心自我介绍',request_id:crypto.randomUUID()};const reply=await ok('/care/'+id+'/reply','POST',data);assert.equal(reply.reply_status,'complete');assert.equal(reply.messages.length,3);assert.match(reply.messages[2].content,/面试/);
 assert.equal((await ok('/care/'+id+'/reply','POST',data)).messages.length,3);assert.equal((await call('/care/'+id+'/reply','POST',{...data,message:'different'})).status,409);
 const second=await ok('/admin/care-check','POST',{});assert.equal(second.created,0);assert.equal(second.reason_code,'cooldown');
});
test('model errors and every inexact decision shape remain silent',async()=>{
 for(const value of ['error','malformed','foreign','false-extra','duplicate','thread-mismatch']){mode=value;const r=await ok('/admin/care-check','POST',{});assert.equal(r.created,0);assert.ok(['model_error','invalid_decision'].includes(r.reason_code));}
 assert.equal((await ok('/care')).items.length,0);assert.equal(pushes.length,0);
});
test('pending reply survives unavailable model and same request safely retries without duplicating user input',async()=>{
 mode='true';const id=(await ok('/admin/care-check','POST',{})).care_id;mode='error';
 const data={message:'我有点紧张',request_id:crypto.randomUUID()};const saved=await ok('/care/'+id+'/reply','POST',data);assert.equal(saved.reply_status,'pending');assert.equal(saved.messages.length,2);assert.equal(saved.messages[1].request_id,data.request_id);
 assert.equal((await call('/care/'+id+'/reply','POST',{message:'new turn',request_id:crypto.randomUUID()})).status,409);
 assert.equal((await ok('/care/'+id)).messages[1].reply_status,'pending');mode='true';const retried=await ok('/care/'+id+'/reply','POST',data);assert.equal(retried.reply_status,'complete');assert.equal(retried.messages.filter(m=>m.role==='user').length,1);
});
test('normal writes remain responsive during inference and changed context or consent prevents delivery',async()=>{
 mode='true';let release,start;const started=new Promise(r=>start=r);hold=new Promise(r=>release=r);entered=start;
 const analysis=call('/admin/care-check','POST',{});await started;
 await ok('/care/preferences','PATCH',{enabled:false});release();const result=await analysis;assert.equal(result.status,200);assert.equal(result.data.created,0);assert.equal(result.data.reason_code,'context_changed');assert.equal((await ok('/care')).items.length,0);
});
test('push config is encrypted/redacted, public bootstrapping is bounded, and test targets only the current account',async()=>{
 const config=await ok('/admin/push-config','PATCH',{enabled:true,provider:'jpush',app_key:'a'.repeat(24),master_secret:'fixture-private-master-secret',client_options:{firebase:{api_key:'public-client-api-key',app_id:'public-app-id',messaging_sender_id:'public-sender',project_id:'public-project'},manufacturers:{huawei_app_id:'public-huawei-app'}}});
 assert.equal(config.master_secret,undefined);assert.equal(config.master_secret_configured,true);assert.equal(config.plan,'free');
 const row=await db.prepare("SELECT value FROM service_state WHERE state_key='native_push_settings'").first();assert.equal(row.value.includes('fixture-private-master-secret'),false);
 const publicConfig=await ok('/push/native-config');assert.equal(publicConfig.app_key,'a'.repeat(24));assert.equal(JSON.stringify(publicConfig).includes('fixture-private-master-secret'),false);
 const token='fixture-admin-registration-id';await ok('/push/devices','POST',{provider:'jpush',registration_id:token,config_revision:config.revision});
 await ok('/push/devices','POST',{provider:'jpush',registration_id:'fixture-other-registration-id',config_revision:config.revision},other.token);
 const tested=await ok('/admin/push-test','POST',{});assert.equal(tested.status,'queued');assert.deepEqual(pushes.at(-1).body.audience.registration_id,[token]);assert.equal(pushes.at(-1).body.notification.android.alert.includes('面试'),false);
 assert.equal((await ok('/admin/push-config','PATCH',{master_secret:''})).master_secret_configured,true);
 const cleared=await ok('/admin/push-config','PATCH',{clear_master_secret:true});assert.equal(cleared.master_secret_configured,false);assert.equal((await ok('/push/native-config')).configured,false);
});
test('server-authenticated scheduling creates care without an open client, and account deletion cascades',async()=>{
 mode='true';assert.equal((await call('/internal/evaluate','POST',{},other.token)).status,401);
 await ok('/internal/evaluate','POST',{},'only-test-cron-token-at-least-32-characters');const list=await ok('/care');assert.equal(list.items.length,1);
 await db.prepare("UPDATE users SET data=json_set(data,'$.is_admin',json('true')) WHERE id=?").bind(other.user.id).run();await ok('/me','DELETE');
 assert.equal((await db.prepare('SELECT count(*) n FROM care_threads').first()).n,0);assert.equal((await db.prepare('SELECT count(*) n FROM care_messages').first()).n,0);
});
test('deleted or expired personal facts are not revived from old chat or care text for background analysis',async()=>{
 const memory=(await ok('/memories')).items[0];
 await db.prepare('INSERT INTO chat(id,user_id,data) VALUES(?,?,?)').bind('old-chat',admin.user.id,JSON.stringify({id:'old-chat',role:'assistant',content:memory.text,created_at:NOW})).run();
 await ok('/memories/'+memory.id,'DELETE');const r=await ok('/admin/care-check','POST',{});assert.equal(r.reason_code,'no_context');assert.equal(calls.length,0);
});
test('quiet hours and unconfigured models never call inference or create inbox entries',async()=>{
 mode='true';await ok('/preferences','PATCH',{quiet_start:'13:00',quiet_end:'15:00'});assert.equal((await ok('/admin/care-check','POST',{})).reason_code,'quiet_hours');
 await ok('/preferences','PATCH',{quiet_start:'00:00',quiet_end:'00:00'});await ok('/admin/model-config','PUT',{mode:'disabled'});assert.equal((await ok('/admin/care-check','POST',{})).reason_code,'model_unconfigured');assert.equal(calls.length,0);assert.equal((await ok('/care')).items.length,0);
});
test('duplicate topics and daily budgets remain independent from the configured analysis interval',async()=>{
 mode='true';const first=await ok('/admin/care-check','POST',{}),id=first.care_id;
 const old='2026-11-06T06:00:00.000Z';await db.prepare("UPDATE care_threads SET created_at=?,data=json_set(data,'$.created_at',?) WHERE id=?").bind(old,old,id).run();
 assert.equal((await ok('/admin/care-check','POST',{})).reason_code,'duplicate');assert.equal(calls.length,2,'A due analysis may choose not to send a duplicate');
 const recent='2026-11-08T05:40:00.000Z';await db.prepare("UPDATE care_threads SET created_at=?,data=json_set(data,'$.created_at',?) WHERE id=?").bind(recent,recent,id).run();await ok('/admin/care-config','PATCH',{daily_limit:1,cooldown_minutes:15});
 assert.equal((await ok('/admin/care-check','POST',{})).reason_code,'daily_limit');assert.equal((await ok('/care')).items.length,1);
});
test('overlapping analysis claims do not create duplicate conversations',async()=>{
 mode='true';let start,release;const started=new Promise(r=>start=r);hold=new Promise(r=>release=r);entered=start;
 const first=call('/admin/care-check','POST',{});await started;const second=await ok('/admin/care-check','POST',{});assert.equal(second.reason_code,'busy');release();assert.equal((await first).data.created,1);assert.equal((await ok('/care')).items.length,1);assert.equal(calls.length,1);
});
test('actual context changes while inference runs prevent stale invitations without blocking ordinary writes',async()=>{
 mode='true';let start,release;const started=new Promise(r=>start=r);hold=new Promise(r=>release=r);entered=start;const pending=call('/admin/care-check','POST',{});await started;
 await ok('/events','POST',{kind:'water',value:250,unit:'ml',start_at:NOW,source:'manual',metadata:{note:'only synthetic'}});release();const r=await pending;assert.equal(r.data.reason_code,'context_changed');assert.equal((await ok('/care')).items.length,0);
});
test('created care queues only a generic native notification once and stale device configuration is rejected',async()=>{
 const config=await ok('/admin/push-config','PATCH',{provider:'jpush',enabled:true,app_key:'b'.repeat(24),master_secret:'another-private-fixture-secret'});await ok('/push/devices','POST',{provider:'jpush',registration_id:'fixture-owned-device-rid',config_revision:config.revision});
 mode='true';const r=await ok('/admin/care-check','POST',{});assert.equal(pushes.length,1);assert.equal(pushes[0].body.notification.android.extras.care_id,r.care_id);assert.equal(pushes[0].body.notification.android.alert.includes('面试'),false);
 await ok('/internal/evaluate','POST',{},'only-test-cron-token-at-least-32-characters');assert.equal(pushes.length,1);
 await ok('/admin/push-config','PATCH',{app_key:'c'.repeat(24)});assert.equal((await call('/push/devices','POST',{provider:'jpush',registration_id:'fixture-owned-device-rid',config_revision:config.revision})).status,409);assert.equal((await ok('/admin/push-test','POST',{})).status,'no_devices');
});
test('a saved pending turn can finish and retry idempotently even at the conversation capacity boundary',async()=>{
 mode='true';const id=(await ok('/admin/care-check','POST',{})).care_id;
 const filler=Array.from({length:198},(_,i)=>({id:'fixture-'+i,role:'assistant',content:'合成历史',created_at:NOW}));
 await db.batch(filler.map(m=>db.prepare('INSERT INTO care_messages(id,thread_id,user_id,role,created_at,data) VALUES(?,?,?,?,?,?)').bind(m.id,id,admin.user.id,m.role,NOW,JSON.stringify(m))));
 mode='error';const data={message:'最后这一条已保存的回复',request_id:crypto.randomUUID()};assert.equal((await ok('/care/'+id+'/reply','POST',data)).reply_status,'pending');
 mode='true';const complete=await ok('/care/'+id+'/reply','POST',data);assert.equal(complete.reply_status,'complete');assert.equal((await ok('/care/'+id+'/reply','POST',data)).messages.length,201);
 assert.equal((await call('/care/'+id+'/reply','POST',{message:'new over-limit reply',request_id:crypto.randomUUID()})).status,409);
});
test('a delayed push result never overwrites read markers or replies made while the notification request was in flight',async()=>{
 const config=await ok('/admin/push-config','PATCH',{provider:'jpush',enabled:true,app_key:'d'.repeat(24),master_secret:'only-fixture-push-private-secret'});await ok('/push/devices','POST',{provider:'jpush',registration_id:'fixture-concurrent-owned-rid',config_revision:config.revision});
 let start,release;const started=new Promise(r=>start=r);pushEntered=start;pushHold=new Promise(r=>release=r);mode='true';const checking=call('/admin/care-check','POST',{});await started;
 const id=(await ok('/care')).items[0].id;await ok('/care/'+id+'/reply','POST',{message:'我现在最担心表达清楚',request_id:crypto.randomUUID()});await ok('/care/'+id+'/read','POST',{});release();assert.equal((await checking).data.created,1);
 const after=await ok('/care/'+id);assert.equal(after.thread.unread,false);assert.equal(after.thread.message_count,3);assert.equal(after.thread.preview,after.messages[2].content);
});
