import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {workerSource} from './fixtures/worker-source.mjs';

const NOW='2026-11-08T06:00:00.000Z';
const SECRET='test-only-cloud-scheduler-secret-at-least-32';
let mf,db,subscription;
const pushes=[],responses=new Map();
let inspectPush=async()=>{};
async function call(route,method='GET',data,token){
 const response=await mf.dispatchFetch('https://fixture/api'+route,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
 return {status:response.status,data:await response.json()};
}
async function ok(...args){const r=await call(...args);assert.equal(r.status,200,JSON.stringify(r.data));return r.data;}
async function account(){return ok('/auth/register','POST',{name:'合成验收',email:crypto.randomUUID()+'@example.com',password:'long-test-password',consent_health_data:true});}
before(async()=>{
 const vapid=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
 const jwk=await crypto.subtle.exportKey('jwk',vapid.privateKey);
 const raw=Buffer.from(await crypto.subtle.exportKey('raw',vapid.publicKey)).toString('base64url');
 const device=await crypto.subtle.generateKey({name:'ECDH',namedCurve:'P-256'},true,['deriveBits']);
 subscription={endpoint:'https://fcm.googleapis.com/fcm/send/test-device',expirationTime:null,keys:{p256dh:Buffer.from(await crypto.subtle.exportKey('raw',device.publicKey)).toString('base64url'),auth:Buffer.alloc(16,7).toString('base64url')}};
 mf=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{ENVIRONMENT:'test',TEST_NOW:NOW,AUTH_PEPPER:'test-only-32-character-server-pepper',AUTH_RATE_LIMIT:'1000',CRON_TOKEN:SECRET,WEB_PUSH_PUBLIC_KEY:raw,WEB_PUSH_PRIVATE_KEY:jwk.d,WEB_PUSH_SUBJECT:'mailto:operator@example.com',BACKGROUND_CHECK_INTERVAL_MINUTES:'15'},outboundService:async request=>{pushes.push(request.url);await inspectPush(request.url);assert.equal(request.headers.get('Content-Encoding'),'aes128gcm');return new Response(null,{status:responses.get(request.url)||201});}});
 db=await mf.getD1Database('DB');
 for(const file of (await readdir('drizzle')).filter(x=>x.endsWith('.sql')).sort())for(const sql of (await readFile('drizzle/'+file,'utf8')).split('--> statement-breakpoint').map(x=>x.trim()).filter(Boolean))await db.prepare(sql).run();
});
after(async()=>{await mf?.dispose();});

test('background push configuration and subscription writes require a real account',async()=>{
 assert.equal((await call('/push/config')).status,401);
 const a=await account(),b=await account();
 const config=await ok('/push/config','GET',undefined,a.token);
 assert.equal(config.configured,true);assert.equal(typeof config.public_key,'string');assert.equal('private_key' in config,false);
 await ok('/push/subscriptions','POST',subscription,a.token);
 assert.equal((await ok('/push/subscriptions/status?endpoint='+encodeURIComponent(subscription.endpoint),'GET',undefined,a.token)).subscribed,true);
 assert.equal((await ok('/push/subscriptions/status?endpoint='+encodeURIComponent(subscription.endpoint),'GET',undefined,b.token)).subscribed,false);
 assert.equal((await call('/push/subscriptions','POST',subscription,b.token)).status,409);
 assert.equal((await call('/push/test','POST',{endpoint:subscription.endpoint},b.token)).status,404);
 await ok('/push/subscriptions','DELETE',{endpoint:subscription.endpoint},b.token);
 assert.equal((await db.prepare('SELECT count(*) n FROM web_push_subscriptions WHERE user_id=?').bind(a.user.id).first()).n,1);
 await ok('/push/subscriptions','DELETE',{endpoint:subscription.endpoint},a.token);
 assert.equal((await db.prepare('SELECT count(*) n FROM web_push_subscriptions WHERE user_id=?').bind(a.user.id).first()).n,0);
 await ok('/push/subscriptions','POST',subscription,a.token);
 await ok('/me','DELETE',undefined,a.token);
 assert.equal((await db.prepare('SELECT count(*) n FROM web_push_subscriptions WHERE endpoint=?').bind(subscription.endpoint).first()).n,0);
});

test('detached scheduler persists a bounded heartbeat and creates due companionship once',async()=>{
 const a=await account();
 await ok('/preferences','PATCH',{quiet_start:'00:00',quiet_end:'00:00'},a.token);
 const thread=await ok('/threads','POST',{title:'合成验收约定',follow_up_enabled:true,follow_up_at:NOW},a.token);
 assert.equal((await call('/internal/evaluate','POST',{})).status,401);
 const first=await ok('/internal/evaluate','POST',{},SECRET);assert.ok(first.evaluated>=1);assert.equal(first.failed,0);
 const state=await db.prepare("SELECT value FROM service_state WHERE state_key='scheduler_health'").first();
 const health=JSON.parse(state.value);assert.equal(health.last_completed_at,NOW);assert.equal(health.failed,0);
 const status=await ok('/integrations/status','GET',undefined,a.token);
 assert.equal(status.background_check.healthy,true);assert.equal(status.background_check.interval_minutes,15);assert.equal(status.push.web_configured,true);assert.equal(status.push.configured,false);
 assert.equal(JSON.stringify(status).includes(SECRET),false);
 await ok('/internal/evaluate','POST',{},SECRET);
 const reminders=(await ok('/reminders','GET',undefined,a.token)).items.filter(r=>r.evidence_ids.includes(thread.id));assert.equal(reminders.length,1);
 const detail=await call('/admin/background-status','GET',undefined,a.token);assert.equal(detail.status,403);
 await db.prepare("UPDATE users SET data=json_set(data,'$.is_admin',json('true')) WHERE id=?").bind(a.user.id).run();
 const admin=await ok('/admin/background-status','GET',undefined,a.token);assert.equal(admin.last_completed_at,NOW);
});

test('push tests respect quiet hours and cannot be used to flood a device',async()=>{
 const a=await account(), own={...subscription,endpoint:subscription.endpoint+'-quiet'};
 await ok('/push/subscriptions','POST',own,a.token);
 await ok('/preferences','PATCH',{quiet_start:'13:00',quiet_end:'15:00'},a.token);
 assert.equal((await call('/push/test','POST',{endpoint:own.endpoint},a.token)).status,409);
});

test('partial browser delivery retries only failures and removes expired subscriptions',async()=>{
 const a=await account(),targets=['accepted','retry','gone'].map(suffix=>({...subscription,endpoint:subscription.endpoint+'-'+suffix}));
 await ok('/preferences','PATCH',{quiet_start:'00:00',quiet_end:'00:00',daily_limit:5},a.token);
 for(const [index,target] of targets.entries()){await ok('/push/subscriptions','POST',target,a.token);await db.prepare('UPDATE web_push_subscriptions SET created_at=? WHERE endpoint=?').bind(new Date(Date.parse(NOW)+index).toISOString(),target.endpoint).run();}
 responses.set(targets[1].endpoint,503);responses.set(targets[2].endpoint,410);
 await ok('/threads','POST',{title:'关页面后的合成跟进',follow_up_enabled:true,follow_up_at:NOW},a.token);
 let persistedBeforeNext=false;
 inspectPush=async url=>{if(url===targets[1].endpoint){const row=await db.prepare('SELECT data FROM reminders WHERE user_id=?').bind(a.user.id).first();persistedBeforeNext=!!JSON.parse(row.data).push_web_delivered?.length;}};
 await ok('/evaluate','POST',{},a.token);
 assert.equal(persistedBeforeNext,true,'accepted endpoints must persist before another request can time out');inspectPush=async()=>{};
 assert.equal((await ok('/reminders','GET',undefined,a.token)).items[0].delivery_status,'failed');
 assert.equal((await db.prepare('SELECT count(*) n FROM web_push_subscriptions WHERE user_id=?').bind(a.user.id).first()).n,2);
 responses.set(targets[1].endpoint,201);
 await ok('/evaluate','POST',{},a.token);await ok('/evaluate','POST',{},a.token);
 assert.equal(pushes.filter(url=>url===targets[0].endpoint).length,1);
 assert.equal(pushes.filter(url=>url===targets[1].endpoint).length,2);
 assert.equal(pushes.filter(url=>url===targets[2].endpoint).length,1);
 assert.equal((await ok('/reminders','GET',undefined,a.token)).items[0].delivery_status,'queued');
});

test('a full final scheduler batch stops instead of wrapping around the same accounts',async()=>{
 const users=(await db.prepare('SELECT id FROM users ORDER BY id DESC LIMIT 3').all()).results;
 assert.equal(users.length,3);
 await db.prepare("INSERT INTO service_state(state_key,value) VALUES('scheduler_cursor',?) ON CONFLICT(state_key) DO UPDATE SET value=excluded.value").bind(users[2].id).run();
 const result=await ok('/internal/evaluate','POST',{},SECRET);
 assert.equal(result.evaluated,2);assert.equal(result.has_more,false);
 assert.equal((await db.prepare("SELECT value FROM service_state WHERE state_key='scheduler_cursor'").first()).value,'');
});
