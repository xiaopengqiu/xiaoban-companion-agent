import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare} from 'miniflare';
import {workerSource} from './fixtures/worker-source.mjs';
let mf,db;
const secret='test-only-single-use-admin-setup-32chars';
before(async()=>{
 mf=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{AUTH_PEPPER:'test-only-auth-pepper-at-least-32chars',ADMIN_SETUP_TOKEN:secret,AUTH_RATE_LIMIT:'1000'},outboundService:async request=>{
  const body=await request.json();assert.equal(body.model,'test-model');assert.equal(request.headers.get('Authorization'),'Bearer test-private-key');
  return Response.json({choices:[{message:{content:'连接成功'}}]});
 }});
 db=await mf.getD1Database('DB');
 for(const file of (await readdir('drizzle')).filter(x=>x.endsWith('.sql')).sort())for(const sql of (await readFile('drizzle/'+file,'utf8')).split('--> statement-breakpoint').map(s=>s.trim()).filter(Boolean))await db.prepare(sql).run();
});
after(async()=>{await mf?.dispose();});
async function call(route,method='GET',data,token,instance=mf){const response=await instance.dispatchFetch('http://localhost/api'+route,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});return {status:response.status,data:await response.json()};}
async function account(instance=mf){const email=crypto.randomUUID()+'@example.com';const r=await call('/auth/register','POST',{name:'合成账号',email,password:'long-test-password',consent_health_data:true},undefined,instance);assert.equal(r.status,200);return {...r.data,email};}
let admin,other;
test('ordinary users cannot configure or test models and bootstrap is owner-secret single-use',async()=>{
 admin=await account();other=await account();
 assert.equal((await call('/admin/model-config','GET',undefined,admin.token)).status,403);
 assert.equal((await call('/internal/admin/grant','POST',{email:admin.email},admin.token)).status,401);
 assert.equal((await call('/internal/admin/grant','POST',{email:admin.email},secret)).status,200);
 assert.equal((await call('/internal/admin/grant','POST',{email:other.email},secret)).status,409);
 assert.equal((await call('/me','GET',undefined,admin.token)).data.is_admin,true);
 for(const [route,method,data]of [['/admin/model-config','GET'],['/admin/model-config','PUT',{mode:'disabled'}],['/admin/model-test','POST',{}]])assert.equal((await call(route,method,data,other.token)).status,403);
});
test('D1 configuration is encrypted, public status redacted, omitted keys retained',async()=>{
 const config={mode:'api',api_url:'https://model.example/v1',api_key:'test-private-key',model:'test-model'};
 let r=await call('/admin/model-config','PUT',config,admin.token);assert.equal(r.status,200,JSON.stringify(r.data));assert.equal(r.data.api_key_configured,true);assert.equal(r.data.api_key,undefined);
 const row=await db.prepare("SELECT value FROM service_state WHERE state_key='model_settings'").first();assert.equal(row.value.includes('test-private-key'),false);
 delete config.api_key;r=await call('/admin/model-config','PUT',config,admin.token);assert.equal(r.status,200);assert.equal(r.data.api_key_configured,true);
 const status=(await call('/integrations/status','GET',undefined,other.token)).data.model;assert.equal(status.configured,true);assert.equal(status.api_url,undefined);
 assert.equal((await call('/admin/model-test','POST',{},admin.token)).data.ok,true);
 for(const bad of [{mode:'api',api_url:'http://evil.example'},{mode:'local',local_provider:'shell'},{mode:'disabled',command:'exec'}])assert.equal((await call('/admin/model-config','PUT',bad,admin.token)).status,422);
 assert.equal((await call('/admin/model-config','PUT',{mode:'local',local_provider:'codex',gateway_url:'https://gateway.example/v1',gateway_key:'private-gateway-token'},admin.token)).status,200);
});

test('administrators can list and delegate roles, ordinary users and demos cannot',async()=>{
 const a=await account();
 assert.equal((await call('/admin/users','GET',undefined,a.token)).status,403);
 const listed=await call('/admin/users?email='+encodeURIComponent(a.email.toUpperCase()),'GET',undefined,admin.token);
 assert.equal(listed.status,200);assert.equal(listed.data.items.length,1);assert.deepEqual(Object.keys(listed.data.items[0]).sort(),['created_at','email','id','is_admin','is_demo','name']);
 assert.equal((await call('/admin/users/'+a.user.id+'/role','POST',{is_admin:true},a.token)).status,403);
 for(const data of [{is_admin:'true'},{is_admin:true,command:'exec'},{}])assert.equal((await call('/admin/users/'+a.user.id+'/role','POST',data,admin.token)).status,422);
 assert.equal((await call('/admin/users/missing/role','POST',{is_admin:true},admin.token)).status,404);
 const granted=await call('/admin/users/'+a.user.id+'/role','POST',{is_admin:true},admin.token);assert.equal(granted.status,200);assert.equal(granted.data.is_admin,true);assert.equal(granted.data.id,a.user.id);
 assert.equal((await call('/admin/model-config','GET',undefined,a.token)).status,200);
 const demo=(await call('/demo','POST',{})).data;
 assert.equal((await call('/admin/users/'+demo.user.id+'/role','POST',{is_admin:true},admin.token)).status,422);
 assert.equal((await call('/admin/users/'+a.user.id+'/role','POST',{is_admin:false},admin.token)).status,200);
 assert.equal((await call('/admin/model-config','GET',undefined,a.token)).status,403);
});

test('concurrent self-demotion and deletion cannot remove the final administrator',async()=>{
 let upstreamCalls=0;
 const instance=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],bindings:{AUTH_PEPPER:'test-only-32-character-server-pepper',ADMIN_SETUP_TOKEN:secret,AUTH_RATE_LIMIT:'1000',OPEN_WEARABLES_URL:'https://wearables.fixture',OPEN_WEARABLES_API_KEY:'test-only-admin-key'},outboundService:async()=>{upstreamCalls++;return new Response(null,{status:204});}});
 try{const database=await instance.getD1Database('DB');for(const file of (await readdir('drizzle')).filter(x=>x.endsWith('.sql')).sort())for(const sql of (await readFile('drizzle/'+file,'utf8')).split('--> statement-breakpoint').map(s=>s.trim()).filter(Boolean))await database.prepare(sql).run();
  const a=await account(instance),b=await account(instance);
  assert.equal((await call('/internal/admin/grant','POST',{email:a.email},secret,instance)).status,200);
  assert.equal((await call('/admin/users/'+a.user.id+'/role','POST',{is_admin:false},a.token,instance)).status,409);
  await database.prepare('INSERT INTO integrations(user_id,data) VALUES(?,?)').bind(a.user.id,JSON.stringify({wearable_user_id:'11111111-1111-4111-8111-111111111111'})).run();
  assert.equal((await call('/me','DELETE',undefined,a.token,instance)).status,409);
  assert.equal(upstreamCalls,0,'Last administrator must be rejected before destructive remote cleanup');
  assert.equal(JSON.parse((await database.prepare('SELECT data FROM integrations WHERE user_id=?').bind(a.user.id).first()).data).upstream_deleted,undefined);
  assert.equal((await call('/admin/users/'+b.user.id+'/role','POST',{is_admin:true},a.token,instance)).status,200);
  const demotions=await Promise.all([a,b].map(user=>call('/admin/users/'+user.user.id+'/role','POST',{is_admin:false},user.token,instance)));
  assert.deepEqual(demotions.map(r=>r.status).sort(),[200,409]);
  const remaining=[];for(const user of [a,b])if((await call('/me','GET',undefined,user.token,instance)).data.is_admin)remaining.push(user);
  assert.equal(remaining.length,1);const survivor=remaining[0],ordinary=survivor===a?b:a;
  assert.equal((await call('/admin/users/'+ordinary.user.id+'/role','POST',{is_admin:true},survivor.token,instance)).status,200);
  const deletions=await Promise.all([a,b].map(user=>call('/me','DELETE',undefined,user.token,instance)));
  assert.deepEqual(deletions.map(r=>r.status).sort(),[200,409]);
  assert.equal((await database.prepare("SELECT count(*) n FROM users WHERE json_extract(data,'$.is_admin')=1").first()).n,1);
 }finally{await instance.dispose();}
});
