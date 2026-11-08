import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {generateKeyPairSync,createHash,verify} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {Miniflare} from 'miniflare';
import {workerSource} from './fixtures/worker-source.mjs';

const origin='https://xiaoban-companion.abloom-haven-3343.chatgpt.site';
const token='test-only-update-publisher-token-with-32-characters';
const {privateKey,publicKey}=generateKeyPairSync('ed25519');
const bindings={AUTH_PEPPER:'test-auth-pepper-with-at-least-32-characters',ADMIN_SETUP_TOKEN:'test-admin-setup-token-with-32-characters',DESKTOP_UPDATE_PUBLISH_TOKEN:token,DESKTOP_UPDATE_SIGNING_KEY:privateKey.export({type:'pkcs8',format:'der'}).toString('base64')};
let mf,db,bucket;
const digest=data=>createHash('sha256').update(data).digest('hex');
const binary=Buffer.concat([Buffer.from('MZ'),Buffer.alloc(128*1024,7)]);
const metadata=(data=binary,extra={})=>({sha256:digest(data),size:data.length,notes:'小伴更新：更安心地陪伴你 🌱\n保留原有数据。',platform:'windows-x64',...extra});
async function instance(extra={}){
 const instance=new Miniflare({modules:true,script:await workerSource(),compatibilityDate:'2026-07-30',d1Databases:['DB'],r2Buckets:['UPDATES'],bindings:{...bindings,...extra}});
 const database=await instance.getD1Database('DB');
 for(const file of (await readdir('drizzle')).filter(x=>x.endsWith('.sql')).sort())for(const sql of (await readFile('drizzle/'+file,'utf8')).split('--> statement-breakpoint').map(s=>s.trim()).filter(Boolean))await database.prepare(sql).run();
 return instance;
}
before(async()=>{mf=await instance();db=await mf.getD1Database('DB');bucket=await mf.getR2Bucket('UPDATES');});
after(async()=>{await mf?.dispose();});
async function publish(version,data=binary,meta=metadata(data),bearer=token,target=mf,headers={}){
 return target.dispatchFetch(origin+'/api/internal/desktop-updates/'+version,{method:'PUT',headers:{Authorization:'Bearer '+bearer,'Content-Type':'application/octet-stream','Content-Length':String(data.length),'X-Update-Metadata':Buffer.from(JSON.stringify(meta)).toString('base64'),...headers},body:data});
}
const latest=(target=mf)=>target.dispatchFetch(origin+'/api/desktop-updates/latest');

test('unpublished feed is public and empty; only independent operator token may publish',async()=>{
 assert.equal((await latest()).status,404);
 const register=await mf.dispatchFetch(origin+'/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'合成管理员',email:'update-test@example.com',password:'test-only-long-password',consent_health_data:true})});
 assert.equal(register.status,200);const admin=await register.json();
 assert.equal((await mf.dispatchFetch(origin+'/api/internal/admin/grant',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+bindings.ADMIN_SETUP_TOKEN},body:JSON.stringify({email:admin.user.email})})).status,200);
 for(const bearer of ['wrong-token',admin.token])assert.equal((await publish('0.4.0',binary,metadata(),bearer)).status,401);
 assert.equal((await bucket.list()).objects.length,0);
 const missing=await instance({DESKTOP_UPDATE_SIGNING_KEY:''});
 try{assert.equal((await publish('0.4.0',binary,metadata(),token,missing)).status,503);assert.equal((await latest(missing)).status,404);}finally{await missing.dispose();}
});

test('binary upload bypasses JSON64KB limit and produces Python-compatible Ed25519 signature',async()=>{
 const response=await publish('0.4.0');assert.equal(response.status,201,await response.clone().text());
 const envelope=await response.json();
 assert.deepEqual(Object.keys(envelope).sort(),['payload','signature']);
 assert.deepEqual(Object.keys(envelope.payload).sort(),['notes','platform','published_at','schema','sha256','size','url','version']);
 assert.equal(envelope.payload.version,'0.4.0');assert.equal(envelope.payload.schema,1);
 assert.equal(envelope.payload.url,origin+'/api/desktop-updates/assets/0.4.0/'+digest(binary)+'.exe');
 const python=existsSync('../.venv-desktop/Scripts/python.exe')?'../.venv-desktop/Scripts/python.exe':'python';
 const canonical=execFileSync(python,['-c',"import json,sys; sys.stdout.buffer.write(json.dumps(json.loads(sys.stdin.buffer.read()),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8'))"],{input:JSON.stringify(envelope.payload)});
 assert.equal(verify(null,canonical,publicKey,Buffer.from(envelope.signature,'base64')),true);
 const feed=await latest();assert.equal(feed.status,200);assert.deepEqual(await feed.json(),envelope);
 const asset=await mf.dispatchFetch(envelope.payload.url);assert.equal(asset.status,200);assert.deepEqual(Buffer.from(await asset.arrayBuffer()),binary);
 assert.equal(asset.headers.get('Content-Length'),String(binary.length));assert.equal(asset.headers.get('X-Content-Type-Options'),'nosniff');assert.match(asset.headers.get('Cache-Control'),/immutable/);
 assert.equal(asset.headers.get('Content-Type'),'application/vnd.microsoft.portable-executable');
 const object=(await bucket.list()).objects[0],stored=await bucket.head(object.key);
 assert.equal(Buffer.from(stored.checksums.sha256).toString('hex'),digest(binary),'R2 stores provider-verified SHA256');
 const head=await mf.dispatchFetch(envelope.payload.url,{method:'HEAD'});assert.equal(head.status,200);assert.equal((await head.arrayBuffer()).byteLength,0);
});

test('published version is immutable and exact retries preserve original envelope',async()=>{
 const original=await (await latest()).json();
 const retry=await publish('0.4.0');assert.equal(retry.status,200);assert.deepEqual(await retry.json(),original);
 const changed=Buffer.from('MZdifferent executable');
 assert.equal((await publish('0.4.0',changed)).status,409);
 assert.equal((await publish('0.4.0',binary,metadata(binary,{notes:'changed notes'}))).status,409);
 assert.equal((await publish('0.3.99')).status,409);
 assert.deepEqual(await (await latest()).json(),original);
 assert.deepEqual(Buffer.from(await (await mf.dispatchFetch(original.payload.url)).arrayBuffer()),binary);
});

test('tampered, truncated, oversized and malformed uploads never become latest',async()=>{
 const original=await (await latest()).json(),count=(await bucket.list()).objects.length;
 for(const [body,meta] of [[binary,metadata(binary,{sha256:'0'.repeat(64)})],[binary,metadata(binary,{size:binary.length+1})],[binary,metadata(binary,{size:binary.length-1})],[binary,metadata(binary,{size:67108865})],[binary,metadata(binary,{platform:'linux-x64'})],[binary,metadata(binary,{sha256:'A'.repeat(64)})],[binary,metadata(binary,{size:1.5})],[binary,metadata(binary,{notes:'x'.repeat(2001)})],[binary,metadata(binary,{extra:'ignored?'})]]){
  const response=await publish('0.4.1',body,meta);assert.ok([413,422].includes(response.status),String(response.status)+' '+await response.text());
 }
 assert.equal((await publish('0.4.1',binary,metadata(),token,mf,{'Content-Type':'application/json'})).status,415);
 assert.equal((await publish('0.4.1',binary,metadata(),token,mf,{'X-Update-Metadata':'!not-base64'})).status,422);
 for(const version of ['01.4.1','1.2','1.2.3-beta','9007199254740992.0.0'])assert.equal((await publish(version)).status,422);
 assert.equal((await bucket.list()).objects.length,count);
 assert.deepEqual(await (await latest()).json(),original);
});

test('only committed immutable paths can be downloaded and methods are explicit',async()=>{
 await bucket.put('arbitrary-secret',Buffer.from('never exposed'));
 for(const path of ['/api/desktop-updates/assets/arbitrary-secret','/api/desktop-updates/assets/0.4.0/'+'f'.repeat(64)+'.exe','/api/desktop-updates/assets/01.4.0/'+digest(binary)+'.exe'])assert.equal((await mf.dispatchFetch(origin+path)).status,404);
 assert.equal((await mf.dispatchFetch(origin+'/api/desktop-updates/latest',{method:'POST'})).status,405);
 assert.equal((await mf.dispatchFetch(origin+'/api/internal/desktop-updates/0.5.0')).status,405);
 assert.equal(await (await bucket.get('arbitrary-secret')).text(),'never exposed');
});

test('concurrent publication and expired-lease takeover keep latest version monotonic',async()=>{
 const results=await Promise.all(['0.5.0','0.6.0'].map(version=>publish(version)));
 for(const response of results)assert.ok([201,409].includes(response.status),await response.clone().text());
 assert.ok(results.some(response=>response.status===201));
 const high=await publish('0.6.0');assert.ok([200,201].includes(high.status),await high.clone().text());
 assert.equal((await publish('0.5.1')).status,409);
 assert.equal((await (await latest()).json()).payload.version,'0.6.0');
 await db.prepare('INSERT INTO service_state(state_key,value) VALUES(?,?) ON CONFLICT(state_key) DO UPDATE SET value=excluded.value').bind('desktop_update_publish_lease',JSON.stringify({owner:'crashed-uploader',expires_at:Date.now()-1})).run();
 assert.equal((await publish('0.7.0')).status,201);
 assert.equal((await (await latest()).json()).payload.version,'0.7.0');
 assert.equal((await db.prepare("SELECT count(*) n FROM service_state WHERE state_key='desktop_update_publish_lease'").first()).n,0);
 assert.equal(await (await bucket.get('arbitrary-secret')).text(),'never exposed');
});

test('a slow expired publisher cannot activate after a newer lease owner',async()=>{
 let finish;
 const gate=new Promise(resolve=>{finish=resolve;});
 const stream=new ReadableStream({async start(controller){controller.enqueue(binary.subarray(0,2));await gate;controller.enqueue(binary.subarray(2));controller.close();}});
 const pending=mf.dispatchFetch(origin+'/api/internal/desktop-updates/0.8.0',{method:'PUT',headers:{Authorization:'Bearer '+token,'Content-Type':'application/octet-stream','Content-Length':String(binary.length),'X-Update-Metadata':Buffer.from(JSON.stringify(metadata())).toString('base64')},body:stream,duplex:'half'});
 let stale;
 try{
  for(let i=0;i<100;i++){stale=await db.prepare('SELECT value FROM service_state WHERE state_key=?').bind('desktop_update_publish_lease').first();if(stale)break;await new Promise(resolve=>setTimeout(resolve,20));}
  assert.ok(stale,'streaming upload acquired lease before consuming entire EXE');
  const expired={...JSON.parse(stale.value),expires_at:Date.now()-1};
  await db.prepare('UPDATE service_state SET value=? WHERE state_key=? AND value=?').bind(JSON.stringify(expired),'desktop_update_publish_lease',stale.value).run();
  const newer=await publish('0.9.0');assert.equal(newer.status,201,await newer.clone().text());
 }finally{finish();}
 const rejected=await pending;assert.equal(rejected.status,409,await rejected.clone().text());
 assert.equal((await (await latest()).json()).payload.version,'0.9.0');
 assert.equal(await db.prepare('SELECT value FROM service_state WHERE state_key=?').bind('desktop_update_version:0.8.0').first(),null);
 assert.equal((await mf.dispatchFetch(origin+'/api/desktop-updates/assets/0.8.0/'+digest(binary)+'.exe')).status,404);
 assert.equal((await publish('0.10.0')).status,201,'version ordering is numeric, not lexicographic');
 assert.equal((await (await latest()).json()).payload.version,'0.10.0');
});
