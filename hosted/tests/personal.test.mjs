import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
const NOW='2026-11-08T06:00:00.000Z';
const bindings={ENVIRONMENT:'test',TEST_NOW:NOW,AUTH_PEPPER:'test-only-32-character-server-pepper',AUTH_RATE_LIMIT:'1000'};
let mf,db;
async function migrate(database){for(const file of (await readdir('drizzle')).filter(x=>x.endsWith('.sql')).sort())for(const sql of (await readFile('drizzle/'+file,'utf8')).split('--> statement-breakpoint').map(x=>x.trim()).filter(Boolean))await database.prepare(sql).run();}
async function request(route,method='GET',data,token,instance=mf){const response=await instance.dispatchFetch('https://fixture/api'+route,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});return {status:response.status,data:await response.json()};}
async function ok(route,method='GET',data,token,instance=mf){const result=await request(route,method,data,token,instance);assert.equal(result.status,200,route+': '+JSON.stringify(result.data));return result.data;}
async function account(instance=mf){return ok('/auth/register','POST',{name:'小林',email:crypto.randomUUID()+'@example.com',password:'a-very-long-password',consent_health_data:true},undefined,instance);}
async function due(token,extra={}){const item=await ok('/threads','POST',{title:'周五的面试',details:'想聊聊准备情况',follow_up_enabled:true,follow_up_at:'2026-11-08T07:00:00Z',...extra},token);item.follow_up_at='2026-11-08T05:59:00.000Z';await db.prepare('UPDATE threads SET data=? WHERE id=?').bind(JSON.stringify(item),item.id).run();return item;}
before(async()=>{mf=new Miniflare({modules:true,scriptPath:'worker/index.js',compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings});db=await mf.getD1Database('DB');await migrate(db);});
after(async()=>{await mf?.dispose();});

test('personal context is confirmed, expires, can be renewed and stays tenant scoped',async()=>{
 const a=await account(),b=await account();
 const memory=await ok('/memories','POST',{text:'这周正在赶项目',category:'context'},a.token);
 assert.equal(memory.expires_at,'2026-11-15T06:00:00.000Z');assert.equal(memory.is_active,true);
 const expired=await ok('/memories','POST',{text:'上周的临时压力',category:'context',expires_at:'2026-11-01T00:00:00Z'},a.token);assert.equal(expired.is_active,false);
 assert.equal((await request('/memories/'+memory.id,'PATCH',{text:'foreign'},b.token)).status,404);
 const renewed=await ok('/memories/'+expired.id,'PATCH',{expires_at:null,category:'boundary'},a.token);assert.equal(renewed.is_active,true);assert.equal(renewed.text,expired.text);
 assert.deepEqual((await ok('/memories','GET',undefined,b.token)).items,[]);
 assert.equal((await request('/memories','POST',{text:'bad date',category:'context',expires_at:'tomorrow'},a.token)).status,422);
 assert.equal((await request('/memories','POST',{text:'bad category',category:null},a.token)).status,422);
});

test('support styles are persisted, validated and used by free-form local replies',async()=>{
 const a=await account();assert.equal((await ok('/preferences','GET',undefined,a.token)).support_style,'reflect');
 await ok('/preferences','PATCH',{support_style:'listen'},a.token);
 const reply=await ok('/chat','POST',{message:'我最近在准备面试，有点担心表现'},a.token);assert.match(reply.reply,/听|慢慢说/);assert.equal(reply.reply.includes('步'),false);
 assert.equal(reply.suggestions.length,2);assert.deepEqual((await ok('/threads','GET',undefined,a.token)).items,[]);assert.deepEqual((await ok('/memories','GET',undefined,a.token)).items,[]);
 assert.equal((await ok('/chat','POST',{message:'今天步数多少？'},a.token)).suggestions.length,0);
 assert.equal((await ok('/chat','POST',{message:'比如我下周面试怎么办？'},a.token)).suggestions.length,0);
 assert.equal((await request('/preferences','PATCH',{support_style:'diagnose'},a.token)).status,422);
 await ok('/threads','POST',{title:'准备面试'},a.token);
 const avoided=await ok('/chat','POST',{message:'今天先不提面试，我只想聊聊电影'},a.token);assert.equal(avoided.reply.includes('面试'),false);assert.equal(JSON.stringify(avoided.evidence).includes('面试'),false);assert.equal(avoided.suggestions.length,0);
 await ok('/memories','POST',{text:'面试有压力',category:'context'},a.token);
 const explicitAvoid=await ok('/chat','POST',{message:'不要提我之前的面试，先不聊这个'},a.token);assert.equal(JSON.stringify(explicitAvoid.evidence).includes('面试'),false);assert.equal(explicitAvoid.reply.includes('面试'),false);
 assert.equal((await ok('/chat','POST',{message:'今天想随便聊聊电影'},a.token)).reply.includes('面试'),false);
 assert.equal((await ok('/chat','POST',{message:'想继续聊聊准备面试的事'},a.token)).reply.includes('准备面试'),true);
});

test('important things have explicit opt-in, owner scope, revision and account cascade',async()=>{
 const a=await account(),b=await account();
 const item=await ok('/threads','POST',{title:'准备面试',details:'周五下午'},a.token);assert.equal(item.follow_up_enabled,false);assert.equal(item.revision,1);
 assert.deepEqual((await ok('/threads','GET',undefined,b.token)).items,[]);
 assert.equal((await request('/threads/'+item.id,'PATCH',{status:'completed'},b.token)).status,404);
 assert.equal((await request('/threads/'+item.id,'DELETE',undefined,b.token)).status,404);
 for(const patch of [{follow_up_enabled:true},{follow_up_enabled:'yes'},{status:'unknown'},{user_id:b.user.id},{follow_up_enabled:true,follow_up_at:'2027-11-01T00:00:00Z'},{follow_up_enabled:true,follow_up_at:'2026-11-31T06:00:00Z'}])assert.equal((await request('/threads/'+item.id,'PATCH',patch,a.token)).status,422);
 const changed=await ok('/threads/'+item.id,'PATCH',{status:'completed',outcome:'已经聊完，先休息'},a.token);assert.equal(changed.revision,2);
 await ok('/me','DELETE',undefined,a.token);assert.equal((await db.prepare('SELECT count(*) n FROM threads WHERE user_id=?').bind(a.user.id).first()).n,0);
});

test('due follow-up works without health data, deduplicates and is cancelled on completion',async()=>{
 const a=await account(),item=await due(a.token);
 assert.equal((await ok('/evaluate','POST',{},a.token)).created,1);assert.equal((await ok('/evaluate','POST',{},a.token)).created,0);
 const reminder=(await ok('/reminders','GET',undefined,a.token)).items[0];assert.equal(reminder.category,'life');assert.equal(reminder.dedupe_key,`life:${item.id}:1`);assert.equal(reminder.expires_at,'2026-11-09T05:59:00.000Z');assert.match(reminder.body,/想|愿意/);
 await ok('/threads/'+item.id,'PATCH',{status:'completed',outcome:'准备完了'},a.token);
 assert.equal((await ok('/reminders','GET',undefined,a.token)).items[0].status,'cancelled');assert.equal((await ok('/evaluate','POST',{},a.token)).created,0);
});

test('life follow-ups share quiet hours, daily budget and global opt-out',async()=>{
 const a=await account();await ok('/preferences','PATCH',{daily_limit:1,quiet_start:'13:00',quiet_end:'15:00'},a.token);await due(a.token);await due(a.token,{title:'另一个重要事情'});
 assert.equal((await ok('/evaluate','POST',{},a.token)).created,0);
 await ok('/preferences','PATCH',{quiet_start:'22:00',quiet_end:'08:00'},a.token);
 assert.equal((await ok('/reminders','GET',undefined,a.token)).items.filter(r=>r.status==='pending').length,1);
 await ok('/preferences','PATCH',{life_followups_enabled:false},a.token);
 assert.ok((await ok('/reminders','GET',undefined,a.token)).items.every(r=>r.status==='cancelled'));
 assert.equal((await ok('/evaluate','POST',{},a.token)).created,0);
});

test('an unchanged overdue follow-up permits edits and stale follow-ups are not generated',async()=>{
 const a=await account(),item=await due(a.token);
 assert.equal((await ok('/threads/'+item.id,'PATCH',{details:'更新准备情况'},a.token)).details,'更新准备情况');
 const stale=await due(a.token,{title:'太旧的事情'});stale.follow_up_at='2026-11-05T06:00:00Z';await db.prepare('UPDATE threads SET data=? WHERE id=?').bind(JSON.stringify(stale),stale.id).run();
 await ok('/evaluate','POST',{},a.token);assert.ok((await ok('/reminders','GET',undefined,a.token)).items.every(r=>!r.evidence_ids.includes(stale.id)));
});

test('shared sharing examples only offer confirmation for actual first-person context',async()=>{
 const a=await account(),fixtures=JSON.parse(await readFile('tests/fixtures/personal-sharing.json','utf8'));
 fixtures.push({message:'同事最近在准备面试，今天来问我该怎么做。',suggest:false});
 for(const fixture of fixtures){const response=await ok('/chat','POST',{message:fixture.message},a.token);assert.equal(response.suggestions.length>0,fixture.suggest,fixture.message);}
 assert.deepEqual((await ok('/threads','GET',undefined,a.token)).items,[]);
});

test('thread quota rejects new records and pauses, revocations and deletion cancel follow-ups',async()=>{
 const a=await account();
 for(const action of ['paused','revoke','delete']){
  const item=await due(a.token,{title:action});await ok('/evaluate','POST',{},a.token);
  if(action==='delete')await ok('/threads/'+item.id,'DELETE',undefined,a.token);
  else await ok('/threads/'+item.id,'PATCH',action==='revoke'?{follow_up_enabled:false}:{status:action},a.token);
  const reminder=(await ok('/reminders','GET',undefined,a.token)).items.find(r=>r.evidence_ids.includes(item.id));assert.equal(reminder.status,'cancelled');
 }
 const b=await account();await db.prepare(`WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<100) INSERT INTO threads(id,user_id,data) SELECT ?||n,?,json_object('id',?||n,'title','fixture','status','completed','revision',1) FROM seq`).bind('limit-'+b.user.id,b.user.id,'limit-'+b.user.id).run();
 assert.equal((await request('/threads','POST',{title:'over quota'},b.token)).status,409);
});

test('expired personal goals cannot influence health reminder wording or evidence',async()=>{
 const a=await account();const memory=await ok('/memories','POST',{text:'我想早点睡觉，expired-goal-secret',category:'goal',expires_at:'2026-11-07T00:00:00Z'},a.token);
 for(let day=0;day<3;day++)await ok('/events','POST',{kind:'sleep',value:5,unit:'h',source:'manual',start_at:new Date(Date.parse(NOW)-day*86400000-3600000).toISOString()},a.token);
 await ok('/evaluate','POST',{},a.token);const reminders=(await ok('/reminders','GET',undefined,a.token)).items;assert.equal(reminders.length,1);assert.equal(JSON.stringify(reminders).includes(memory.id),false);assert.equal(JSON.stringify(reminders).includes('expired-goal-secret'),false);
});

test('life reminder transport sends only the scoped device a generic lockscreen invitation',async()=>{
 const keys=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
 const der=new Uint8Array(await crypto.subtle.exportKey('pkcs8',keys.privateKey));
 const service={client_email:'fixture@example.iam.gserviceaccount.com',project_id:'fixture',private_key:'-----BEGIN PRIVATE KEY-----\n'+btoa(String.fromCharCode(...der))+'\n-----END PRIVATE KEY-----'};
 const sent=[];const instance=new Miniflare({modules:true,scriptPath:'worker/index.js',compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{...bindings,FCM_SERVICE_ACCOUNT_JSON:JSON.stringify(service)},outboundService:async req=>{
  if(new URL(req.url).hostname==='oauth2.googleapis.com')return new Response(JSON.stringify({access_token:'test-oauth-token'}));
  sent.push(await req.json());return new Response(JSON.stringify({name:'projects/fixture/messages/accepted'}));
 }});
 try{const database=await instance.getD1Database('DB');await migrate(database);const a=await account(instance),b=await account(instance);
  await ok('/devices','POST',{platform:'android',token:'a-device-token-with-enough-length'},a.token,instance);await ok('/devices','POST',{platform:'android',token:'b-device-token-with-enough-length'},b.token,instance);
  const item=await ok('/threads','POST',{title:'private-health-interview',follow_up_enabled:true,follow_up_at:'2026-11-08T07:00:00Z'},a.token,instance);item.follow_up_at='2026-11-08T05:59:00Z';await database.prepare('UPDATE threads SET data=? WHERE id=?').bind(JSON.stringify(item),item.id).run();
  assert.equal((await ok('/evaluate','POST',{},a.token,instance)).created,1);assert.equal(sent.length,1);assert.equal(sent[0].message.token,'a-device-token-with-enough-length');assert.equal(JSON.stringify(sent[0]).includes('private-health-interview'),false);
  assert.equal((await ok('/reminders','GET',undefined,a.token,instance)).items[0].delivery_status,'queued');await ok('/evaluate','POST',{},a.token,instance);assert.equal(sent.length,1);
 }finally{await instance.dispose();}
});

test('model sees active threads and style, but cannot recover expired context through old chat',async()=>{
 const captured=[];const instance=new Miniflare({modules:true,scriptPath:'worker/index.js',compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{...bindings,COMPANION_MODEL_BASE_URL:'https://model.fixture/v1',COMPANION_MODEL_API_KEY:'test-only-key',COMPANION_MODEL:'fixture'},outboundService:async req=>{captured.push(await req.json());return new Response(JSON.stringify({choices:[{message:{content:'想从哪部分开始聊？'}}]}));}});
 try{const database=await instance.getD1Database('DB');await migrate(database);const a=await account(instance);
 await ok('/memories','POST',{text:'expired-sensitive-context',category:'context',expires_at:'2026-11-07T06:00:00Z'},a.token,instance);
 await ok('/memories','POST',{text:'喜欢先倾听',category:'boundary'},a.token,instance);
 await ok('/threads','POST',{title:'下周的面试',details:'想准备自我介绍'},a.token,instance);
 const old={id:'old',role:'assistant',content:'expired-sensitive-context in old conversation',created_at:'2026-11-06T06:00:00Z',context_revision:2};
 // Set to the current revision so the expiry cutoff, rather than edit invalidation, is exercised.
 old.context_revision=(await database.prepare('SELECT revision FROM preferences WHERE user_id=?').bind(a.user.id).first()).revision;
 await database.prepare('INSERT INTO chat(id,user_id,data) VALUES(?,?,?)').bind(old.id,a.user.id,JSON.stringify(old)).run();
 await ok('/preferences','PATCH',{model_consent:true,support_style:'plan'},a.token,instance);
 const result=await ok('/chat','POST',{message:'我想继续聊准备的事'},a.token,instance);assert.equal(result.mode,'model');
 const payload=captured[0];assert.equal(JSON.stringify(payload).includes('expired-sensitive-context'),false);
 const context=JSON.parse(payload.messages[0].content.split('\n',2)[1]);assert.equal(context.support_style,'plan');assert.equal(context.active_threads[0].title,'下周的面试');assert.ok(context.memories.some(m=>m.text==='喜欢先倾听'));
 }finally{await instance.dispose();}
});
