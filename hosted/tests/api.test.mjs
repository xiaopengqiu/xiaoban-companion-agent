import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp,readdir} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {Miniflare} from 'miniflare';
import {workerSource} from './fixtures/worker-source.mjs';

let mf,db;
const NOW='2026-11-08T06:00:00.000Z';
async function migrate(database){for(const file of (await readdir('drizzle')).filter(x=>x.endsWith('.sql')).sort()){
  const sql=await readFile('drizzle/'+file,'utf8');for(const statement of sql.split('--> statement-breakpoint').map(x=>x.trim()).filter(Boolean))await database.prepare(statement).run();}}
async function call(route,method='GET',data,token){
  const response=await mf.dispatchFetch('http://localhost/api'+route,{method,
    headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{}),'CF-Connecting-IP':'127.0.0.1'},
    ...(data!==undefined?{body:JSON.stringify(data)}:{})});
  return {status:response.status,data:await response.json()};
}
async function account(email=crypto.randomUUID()+'@example.com'){
  const r=await call('/auth/register','POST',{name:'小林',email,password:'a-very-long-password',timezone:'Asia/Shanghai',consent_health_data:true});
  assert.equal(r.status,200,JSON.stringify(r.data));return r.data;
}
async function event(token,overrides={}){
  const r=await call('/events','POST',{kind:'steps',value:1200,unit:'count',start_at:NOW,source:'manual',external_id:crypto.randomUUID(),metadata:{},...overrides},token);
  assert.equal(r.status,200,JSON.stringify(r.data));return r.data.event;
}
before(async()=>{
  assert.ok(existsSync('worker/index.js'),'Hosted Worker API must be implemented');
  mf=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',
    d1Databases:['DB'],bindings:{TEST_NOW:NOW,ENVIRONMENT:'test',AUTH_PEPPER:'test-only-32-character-server-pepper',CRON_SECRET:'test-only-protected-cron-secret-32chars',AUTH_RATE_LIMIT:'1000',SCHEDULER_BATCH_SIZE:'30'}});
  db=await mf.getD1Database('DB');
  await migrate(db);
});
after(async()=>{await mf?.dispose();});

test('consent/authentication/hash storage/logout and tenant isolation',async()=>{
  assert.equal((await call('/auth/register','POST',{name:'x',email:'no@example.com',password:'a-long-password',consent_health_data:false})).status,422);
  const a=await account(),b=await account();
  assert.equal((await call('/me')).status,401);
  const record=await event(a.token);
  assert.deepEqual((await call('/events','GET',undefined,b.token)).data.items,[]);
  assert.equal((await call('/events/'+record.id,'DELETE',undefined,b.token)).status,404);
  const stored=await db.prepare('SELECT password_hash FROM users WHERE id=?').bind(a.user.id).first();
  assert.notEqual(stored.password_hash,'a-very-long-password');
  const sessions=await db.prepare('SELECT token_hash FROM sessions WHERE user_id=?').bind(a.user.id).all();
  assert.notEqual(sessions.results[0].token_hash,a.token);
  assert.equal((await call('/auth/login','POST',{email:a.user.email,password:'bad-password'})).status,401);
  const login=await call('/auth/login','POST',{email:a.user.email,password:'a-very-long-password'});
  assert.equal(login.status,200);
  assert.equal((await call('/auth/logout','POST',{},a.token)).status,200);
  assert.equal((await call('/me','GET',undefined,a.token)).status,401);
  assert.equal((await call('/me','GET',undefined,login.data.token)).status,200);
});

test('event idempotency, timezone validation, source suppression and manual records',async()=>{
  const a=await account();
  assert.equal((await call('/events','POST',{kind:'steps',value:1,unit:'steps',start_at:'2026-11-08T12:00:00',source:'manual'},a.token)).status,422);
  const first=await event(a.token,{external_id:'same'});
  const corrected=await event(a.token,{external_id:'same',value:2300});
  assert.equal(first.id,corrected.id);
  assert.equal((await call('/dashboard','GET',undefined,a.token)).data.metrics.steps,2300);
  assert.equal((await call('/events/'+first.id,'DELETE',undefined,a.token)).status,200);
  assert.equal((await call('/events','POST',{kind:'steps',value:5,unit:'count',start_at:NOW,source:'manual',external_id:'same',metadata:{}},a.token)).status,409);
  await event(a.token,{external_id:null});await event(a.token,{external_id:null});
  assert.equal((await call('/events','GET',undefined,a.token)).data.items.length,2);
});

test('explicit memories, correction and scoped chat persist in D1',async()=>{
  const a=await account(),b=await account();
  assert.equal((await call('/chat','POST',{message:'请记住：我不吃海鲜'},a.token)).status,200);
  const memory=(await call('/memories','GET',undefined,a.token)).data.items[0];
  assert.equal(memory.text,'我不吃海鲜');
  assert.equal((await call('/memories/'+memory.id,'PATCH',{text:'wrong'},b.token)).status,404);
  await call('/memories/'+memory.id,'PATCH',{text:'我喜欢早上散步'},a.token);
  const reply=(await call('/chat','POST',{message:'你记得我的偏好吗？'},a.token)).data;
  assert.match(reply.reply,/早上散步/);assert.equal(reply.mode,'local');
  assert.equal((await call('/chat','GET',undefined,a.token)).data.items.length,4);
  assert.deepEqual((await call('/chat','GET',undefined,b.token)).data.items,[]);
  await call('/memories/'+memory.id,'DELETE',undefined,a.token);
  assert.deepEqual((await call('/memories','GET',undefined,a.token)).data.items,[]);
});

test('dated meals, water conversion and mood are attributable',async()=>{
  const a=await account();
  const meal=await event(a.token,{kind:'meal',value:null,unit:'entry',metadata:{note:'午餐青菜豆腐'}});
  await event(a.token,{kind:'water',value:350,unit:'ml'});
  await event(a.token,{kind:'water',value:.5,unit:'l'});
  await event(a.token,{kind:'mood',value:3,unit:'score',metadata:{note:'今天有点累'}});
  const food=(await call('/chat','POST',{message:'今天午餐吃了什么？'},a.token)).data;
  assert.match(food.reply,/午餐青菜豆腐/);assert.match(food.reply,/2026-11-08/);
  assert.ok(food.evidence.some(x=>x.id===meal.id));
  assert.match((await call('/chat','POST',{message:'今天喝了多少水？'},a.token)).data.reply,/850/);
  assert.match((await call('/chat','POST',{message:'今天心情记录是什么？'},a.token)).data.reply,/3\/5/);
});

test('isolated demo, deterministic 21 days, gated reminders, feedback and correction',async()=>{
  const a=(await call('/demo','POST',{})).data,b=(await call('/demo','POST',{})).data;
  assert.notEqual(a.user.id,b.user.id);assert.equal(a.user.is_demo,true);
  const dash=(await call('/dashboard','GET',undefined,a.token)).data;
  assert.equal(dash.series.length,21);assert.ok(dash.insights.length);
  assert.equal(dash.last_sync_at,NOW);
  assert.equal((await call('/evaluate','POST',{},a.token)).status,200);
  assert.equal((await call('/evaluate','POST',{},a.token)).data.created,0);
  const reminders=(await call('/reminders','GET',undefined,a.token)).data.items;
  assert.ok(reminders.length>0&&reminders.length<=2);
  const reminder=reminders[0];
  assert.equal((await call('/reminders/'+reminder.id+'/feedback','POST',{action:'snooze',minutes:30},a.token)).data.status,'snoozed');
  await call('/events/'+reminder.evidence_ids[0],'DELETE',undefined,a.token);
  assert.equal((await call('/reminders','GET',undefined,a.token)).data.items.find(x=>x.id===reminder.id).status,'cancelled');
  await call('/preferences','PATCH',{enabled:false},a.token);
  assert.equal((await call('/evaluate','POST',{},a.token)).data.created,0);
});

test('account deletion cascades, health is minimal, cron is server authenticated',async()=>{
  const a=await account();await event(a.token);
  assert.deepEqual((await call('/health')).data,{status:'ok'});
  assert.equal((await call('/internal/evaluate','POST',{})).status,401);
  assert.equal((await call('/me','DELETE',undefined,a.token)).status,200);
  assert.equal((await call('/me','GET',undefined,a.token)).status,401);
  const rows=await db.prepare('SELECT count(*) AS n FROM events WHERE user_id=?').bind(a.user.id).first();assert.equal(rows.n,0);
});

test('concurrent evaluation cannot spend the same daily budget twice',async()=>{
  const a=(await call('/demo','POST',{})).data;
  const attempts=await Promise.all(Array.from({length:4},()=>call('/evaluate','POST',{},a.token)));
  assert.ok(attempts.every(r=>[200,409].includes(r.status)));
  const items=(await call('/reminders','GET',undefined,a.token)).data.items;
  assert.ok(items.length<=2);assert.equal(new Set(items.map(r=>r.dedupe_key)).size,items.length);
});

test('quiet hours, stale evidence and expired feedback are enforced',async()=>{
  const a=await account();
  await call('/preferences','PATCH',{quiet_start:'12:00',quiet_end:'15:00'},a.token);
  for(let offset=0;offset<3;offset++)await event(a.token,{kind:'sleep',value:6,unit:'h',start_at:new Date(Date.parse(NOW)-offset*86400000-6*3600000).toISOString()});
  assert.equal((await call('/evaluate','POST',{},a.token)).data.created,0);
  await call('/preferences','PATCH',{quiet_start:'00:00',quiet_end:'00:00'},a.token);
  const item=(await call('/reminders','GET',undefined,a.token)).data.items[0];
  item.expires_at='2026-11-07T00:00:00.000Z';
  await db.prepare('UPDATE reminders SET data=? WHERE id=?').bind(JSON.stringify(item),item.id).run();
  assert.equal((await call('/reminders/'+item.id+'/feedback','POST',{action:'snooze'},a.token)).status,409);
  const stale=await account();
  for(let offset=2;offset<5;offset++)await event(stale.token,{kind:'sleep',value:6,unit:'h',start_at:new Date(Date.parse(NOW)-offset*86400000).toISOString()});
  assert.equal((await call('/evaluate','POST',{},stale.token)).data.created,0);
});

test('server-authenticated cron performs evaluation without a browser session',async()=>{
  const response=await mf.dispatchFetch('http://localhost/api/internal/evaluate',{method:'POST',headers:{Authorization:'Bearer test-only-protected-cron-secret-32chars'}});
  assert.equal(response.status,200);assert.ok((await response.json()).evaluated>=1);
});

test('D1 data and hashed session survive a complete Worker restart',async()=>{
  const persist=await mkdtemp(path.join(tmpdir(),'companion-d1-'));
  const options={modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:{DB:'persistent-companion'},d1Persist:persist,
    bindings:{ENVIRONMENT:'test',TEST_NOW:NOW,AUTH_PEPPER:'test-only-32-character-server-pepper'}};
  let instance=new Miniflare(options),local=await instance.getD1Database('DB');
  await migrate(local);
  let response=await instance.dispatchFetch('http://localhost/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'持久用户',email:'persist@example.com',password:'persistent-password',consent_health_data:true})});
  const account=await response.json();assert.equal(response.status,200);
  await instance.dispatchFetch('http://localhost/api/events',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+account.token},body:JSON.stringify({kind:'water',value:500,unit:'ml',start_at:NOW,source:'manual'})});
  await instance.dispose();instance=new Miniflare(options);
  response=await instance.dispatchFetch('http://localhost/api/events',{headers:{Authorization:'Bearer '+account.token}});
  assert.equal(response.status,200);assert.equal((await response.json()).items[0].value,500);await instance.dispose();
});

test('missing pepper fails closed and auth rate limit ignores forged forwarding headers',async()=>{
  const local=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{AUTH_RATE_LIMIT:'1'}});
  try{const database=await local.getD1Database('DB');await migrate(database);
    let response=await local.dispatchFetch('http://localhost/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json','CF-Connecting-IP':'192.0.2.1','X-Forwarded-For':'fake-one'},body:JSON.stringify({name:'x',email:'nopepper@example.com',password:'a-secret-password',consent_health_data:true})});
    assert.equal(response.status,503);assert.ok(!(await response.text()).includes('a-secret-password'));
    response=await local.dispatchFetch('http://localhost/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json','CF-Connecting-IP':'192.0.2.1','X-Forwarded-For':'fake-two'},body:JSON.stringify({email:'nopepper@example.com',password:'a-secret-password'})});
    assert.equal(response.status,429);
  }finally{await local.dispose();}
});

test('streamed UTF-8 request bodies are bounded by bytes rather than characters',async()=>{
  const a=await account();
  const payload=new TextEncoder().encode(JSON.stringify({message:'甲'.repeat(30000)}));
  const stream=new ReadableStream({start(controller){controller.enqueue(payload.subarray(0,40000));controller.enqueue(payload.subarray(40000));controller.close();}});
  const response=await mf.dispatchFetch('http://localhost/api/chat',{method:'POST',headers:{Authorization:'Bearer '+a.token,'Content-Type':'application/json'},body:stream,duplex:'half'});
  assert.equal(response.status,413);
});

test('bounded cron batches rotate a durable cursor instead of starving later users',async()=>{
  const statements=[];for(let i=0;i<61;i++){
    const userId='zzzz-cursor-'+String(i).padStart(3,'0'),user={id:userId,name:'cursor fixture',email:userId+'@fixture.invalid',timezone:'Asia/Shanghai',is_demo:false,consent_health_data:true,created_at:NOW};
    statements.push(db.prepare('INSERT INTO users(id,email,password_hash,salt,data,created_at) VALUES(?,?,?,?,?,?)').bind(userId,user.email,'fixture-non-login','fixture',JSON.stringify(user),NOW));
    statements.push(db.prepare('INSERT INTO preferences(user_id,data,revision) VALUES(?,?,0)').bind(userId,JSON.stringify({enabled:true,model_consent:false,quiet_start:'00:00',quiet_end:'00:00',daily_limit:2,reminder_categories:['sleep'],timezone:'Asia/Shanghai'})));
  }await db.batch(statements);
  for(let i=0;i<3;i++){const e={id:'cursor-sleep-'+i,kind:'sleep',value:6,unit:'h',source:'fixture',start_at:new Date(Date.parse(NOW)-i*86400000-3600000).toISOString(),end_at:null,metadata:{},created_at:NOW,updated_at:NOW};await db.prepare('INSERT INTO events(id,user_id,source,external_id,data) VALUES(?,?,?,?,?)').bind(e.id,'zzzz-cursor-060',e.source,e.id,JSON.stringify(e)).run();}
  const tick=async()=>{const response=await mf.dispatchFetch('http://localhost/api/internal/evaluate',{method:'POST',headers:{Authorization:'Bearer test-only-protected-cron-secret-32chars'}});assert.equal(response.status,200);return response.json();};
  const first=await tick();assert.ok(first.evaluated<=30);
  const early=await db.prepare('SELECT count(*) n FROM reminders WHERE user_id=?').bind('zzzz-cursor-060').first();assert.equal(early.n,0);
  await tick();await tick();await tick();
  assert.ok((await db.prepare('SELECT count(*) n FROM reminders WHERE user_id=?').bind('zzzz-cursor-060').first()).n>=1);
});

test('daily summary freshness uses actual local day end for the 36-hour gate',async()=>{
  const a=await account();await call('/preferences','PATCH',{quiet_start:'00:00',quiet_end:'00:00'},a.token);
  for(let offset=2;offset<10;offset++){const day=new Date(Date.parse(NOW)-offset*86400000).toISOString().slice(0,10);
    await event(a.token,{kind:'steps',value:offset===2?1000:6000,unit:'steps',start_at:day+'T00:00:00.000Z',metadata:{granularity:'daily_summary',summary_date:day,is_complete_day:true}});}
  const result=await call('/evaluate','POST',{},a.token);assert.equal(result.data.created,0);
});

test('per-account event quota rejects new data explicitly instead of hiding latest records',async()=>{
  const a=await account();
  await db.prepare(`WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<2000)
    INSERT INTO events(id,user_id,source,external_id,data)
    SELECT 'quota-'||?||'-'||n,?,'fixture','quota-'||n,
      json_object('id','quota-'||?||'-'||n,'kind','steps','value',1,'unit','count','start_at',?,'source','fixture','external_id','quota-'||n,'metadata',json('{}'),'created_at',?,'updated_at',?) FROM seq`)
    .bind(a.user.id,a.user.id,a.user.id,NOW,NOW,NOW).run();
  const result=await call('/events','POST',{kind:'water',value:50,unit:'ml',start_at:NOW,source:'manual'},a.token);
  assert.equal(result.status,409);assert.match(result.data.detail,/限额/);
});

test('configured wearable binding, signed sync, suppression and remote erasure use scoped D1 mappings',async()=>{
  const uuid='11111111-1111-4111-8111-111111111111',source={provider:'apple',source:'HealthKit',device:'Apple Watch'};
  const secretBytes=new Uint8Array(32).fill(4),secret='whsec_'+btoa(String.fromCharCode(...secretBytes));
  let profiles=0,syncs=0,deleteFails=true;
  const instance=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],
    bindings:{ENVIRONMENT:'test',TEST_NOW:NOW,AUTH_PEPPER:'test-only-32-character-server-pepper',OPEN_WEARABLES_URL:'https://wearable.fixture',OPEN_WEARABLES_API_KEY:'test-only-admin-key',OPEN_WEARABLES_APP_ID:'app',OPEN_WEARABLES_APP_SECRET:'test-only-app-secret',OPEN_WEARABLES_PUBLIC_URL:'https://phone.fixture',OPEN_WEARABLES_WEBHOOK_SECRET:secret},
    outboundService:async request=>{const url=new URL(request.url),reply=data=>new Response(JSON.stringify(data),{headers:{'content-type':'application/json'}});
      if(url.pathname==='/api/v1/users'&&request.method==='POST'){profiles++;return reply({id:uuid});}
      if(url.pathname.endsWith('/token'))return reply({access_token:'scoped-user-jwt',refresh_token:'scoped-user-refresh'});
      if(request.method==='DELETE')return new Response(deleteFails?'failed':null,{status:deleteFails?500:204});
      let data=[];if(url.pathname.endsWith('/activity')){syncs++;data=[{date:'2026-11-08',steps:1400,source}];}
      if(url.pathname.endsWith('/sleep'))data=[0,1,2].map(i=>({id:'sleep-'+i,start_time:new Date(Date.parse(NOW)-i*86400000-7*3600000).toISOString(),end_time:new Date(Date.parse(NOW)-i*86400000-3600000).toISOString(),sleep_duration_seconds:21600,source}));
      return reply({data,pagination:{has_more:false}});
    }});
  try{const database=await instance.getD1Database('DB');await migrate(database);
    let token;const request=async(route,method='GET',data)=>{const response=await instance.dispatchFetch('http://localhost/api'+route,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});return {status:response.status,data:await response.json()};};
    const registration=await request('/auth/register','POST',{name:'穿戴用户',email:'wearable@example.com',password:'a-very-long-password',consent_health_data:true});token=registration.data.token;
    const connected=await request('/integrations/open-wearables/connect','POST',{wearable_user_id:'foreign-user'});assert.equal(connected.status,200,JSON.stringify(connected.data));assert.equal(connected.data.wearable_user_id,uuid);assert.equal(connected.data.host,'https://phone.fixture');assert.equal(connected.data.access_token,'scoped-user-jwt');
    await request('/integrations/open-wearables/connect','POST',{});assert.equal(profiles,1);
    assert.equal((await request('/integrations/open-wearables/sync','POST',{})).data.imported,4);
    assert.equal((await request('/dashboard')).data.metrics.steps,1400);
    const latest=(await request('/events')).data.items.find(e=>e.external_id==='ow:sleep:sleep-0');
    await request('/events/'+latest.id,'DELETE');await request('/integrations/open-wearables/sync','POST',{});
    assert.ok((await request('/events')).data.items.every(e=>e.external_id!==latest.external_id));
    const payload=new TextEncoder().encode(JSON.stringify({type:'sync.completed',data:{user_id:uuid}})),stamp=String(Math.floor(Date.parse(NOW)/1000)),msg='test-message';
    const key=await crypto.subtle.importKey('raw',secretBytes,{name:'HMAC',hash:'SHA-256'},false,['sign']);
    const prefix=new TextEncoder().encode(msg+'.'+stamp+'.'),combined=new Uint8Array(prefix.length+payload.length);combined.set(prefix);combined.set(payload,prefix.length);
    const signature=btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.sign('HMAC',key,combined))));
    const headers={'svix-id':msg,'svix-timestamp':stamp,'svix-signature':'v1,'+signature};const before=syncs;
    assert.equal((await instance.dispatchFetch('http://localhost/api/integrations/open-wearables/webhook',{method:'POST',headers,body:payload})).status,200);
    assert.equal((await instance.dispatchFetch('http://localhost/api/integrations/open-wearables/webhook',{method:'POST',headers,body:payload})).status,200);assert.equal(syncs,before+1);
    assert.equal((await instance.dispatchFetch('http://localhost/api/integrations/open-wearables/webhook',{method:'POST',headers,body:new TextEncoder().encode('{}')})).status,401);
    assert.equal((await request('/me','DELETE')).status,503);assert.equal((await request('/me')).status,200);
    deleteFails=false;assert.equal((await request('/me','DELETE')).status,200);assert.equal((await request('/me')).status,401);
    assert.equal((await database.prepare('SELECT count(*) n FROM webhook_receipts').first()).n,0);
  }finally{await instance.dispose();}
});

test('edge deterministic metrics match the Python policy fixtures',async()=>{
  const fixtures=JSON.parse(await readFile('tests/fixtures/policy-parity.json','utf8'));
  for(const fixture of fixtures){const a=await account();await call('/preferences','PATCH',{timezone:fixture.timezone},a.token);
    for(const e of fixture.events)await event(a.token,e);
    const dashboard=(await call('/dashboard','GET',undefined,a.token)).data;
    assert.deepEqual(dashboard.metrics,fixture.expected,fixture.name);
  }
});

test('edge transports refuse 302 and 307 redirects without forwarding administrator credentials',async()=>{
  const destinations=[];let status=302;
  const instance=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],
    bindings:{ENVIRONMENT:'test',TEST_NOW:NOW,AUTH_PEPPER:'test-only-32-character-server-pepper',OPEN_WEARABLES_URL:'https://wearable.fixture',OPEN_WEARABLES_API_KEY:'test-only-admin-key',OPEN_WEARABLES_APP_ID:'app',OPEN_WEARABLES_APP_SECRET:'test-only-app-secret'},
    outboundService:async request=>{destinations.push(new URL(request.url).hostname);return new Response(null,{status,headers:{Location:'https://untrusted.fixture/collect'}});}});
  try{await migrate(await instance.getD1Database('DB'));
    const registered=await instance.dispatchFetch('http://localhost/api/auth/register',{method:'POST',body:JSON.stringify({name:'redirect fixture',email:'redirect@example.com',password:'a-very-long-password',consent_health_data:true})});const a=await registered.json();
    for(const code of [302,307]){status=code;const response=await instance.dispatchFetch('http://localhost/api/integrations/open-wearables/connect',{method:'POST',headers:{Authorization:'Bearer '+a.token},body:'{}'});assert.equal(response.status,503);}
    assert.deepEqual(destinations,['wearable.fixture','wearable.fixture']);
  }finally{await instance.dispose();}
});

test('optional model runs only after consent and receives bounded authorized event context',async()=>{
  const captured=[];const instance=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],
    bindings:{ENVIRONMENT:'test',TEST_NOW:NOW,AUTH_PEPPER:'test-only-32-character-server-pepper',COMPANION_MODEL_BASE_URL:'https://model.fixture/v1',COMPANION_MODEL_API_KEY:'test-only-model-key',COMPANION_MODEL:'fixture-model'},
    outboundService:async request=>{captured.push(await request.json());return new Response(JSON.stringify({choices:[{message:{content:'你记录的午餐有青菜豆腐。'}}]}),{headers:{'Content-Type':'application/json'}});}});
  try{await migrate(await instance.getD1Database('DB'));let token;
    const request=async(route,method='GET',data)=>{const response=await instance.dispatchFetch('http://localhost/api'+route,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});return {status:response.status,data:await response.json()};};
    token=(await request('/auth/register','POST',{name:'model fixture',email:'model@example.com',password:'a-very-long-password',consent_health_data:true})).data.token;
    await request('/events','POST',{kind:'meal',value:null,unit:'entry',source:'manual',start_at:NOW,metadata:{note:'午餐青菜豆腐 '+('甲'.repeat(500)),unneeded_private:'do-not-send'}});
    assert.equal((await request('/chat','POST',{message:'今天午餐吃了什么？'})).data.mode,'local');assert.equal(captured.length,0);
    await request('/preferences','PATCH',{model_consent:true});assert.equal((await request('/chat','POST',{message:'今天午餐吃了什么？'})).data.mode,'model');
    const system=captured[0].messages[0].content,context=JSON.parse(system.split('\n',2)[1]);
    assert.equal(context.relevant_events.length,1);assert.ok(context.relevant_events[0].note.startsWith('午餐青菜豆腐'));assert.ok(context.relevant_events[0].note.length<=300);assert.ok(!system.includes('do-not-send'));
  }finally{await instance.dispose();}
});
