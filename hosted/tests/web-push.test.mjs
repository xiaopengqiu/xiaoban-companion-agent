import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {createECDH,createDecipheriv,hkdfSync} from 'node:crypto';
import {readFile,readdir} from 'node:fs/promises';
import {Miniflare} from 'miniflare';

const push=await import('../worker/web-push.mjs').catch(error=>{if(error.code==='ERR_MODULE_NOT_FOUND'&&error.message.includes('web-push.mjs'))return null;throw error;});
const recipient=createECDH('prime256v1');recipient.generateKeys();
const auth=crypto.getRandomValues(new Uint8Array(16));
const subscription=(endpoint='https://fcm.googleapis.com/fcm/send/fixture-token')=>({endpoint,expirationTime:null,keys:{p256dh:recipient.getPublicKey().toString('base64url'),auth:Buffer.from(auth).toString('base64url')}});
let env,mf,db;
before(async()=>{
 const signing=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
 const jwk=await crypto.subtle.exportKey('jwk',signing.privateKey);
 env={WEB_PUSH_SUBJECT:'mailto:operator@example.com',WEB_PUSH_PUBLIC_KEY:Buffer.from(await crypto.subtle.exportKey('raw',signing.publicKey)).toString('base64url'),WEB_PUSH_PRIVATE_KEY:jwk.d};
 if(!push)return;
 mf=new Miniflare({modules:true,script:'export default {fetch(){return new Response("ok")}}',compatibilityDate:'2026-07-30',d1Databases:['DB']});db=await mf.getD1Database('DB');
 for(const file of (await readdir('drizzle')).filter(x=>x.endsWith('.sql')).sort())for(const statement of (await readFile('drizzle/'+file,'utf8')).split('--> statement-breakpoint').map(x=>x.trim()).filter(Boolean))await db.prepare(statement).run();
 for(const id of ['push-owner-a','push-owner-b'])await db.prepare('INSERT INTO users(id,email,password_hash,salt,data,created_at) VALUES(?,?,?,?,?,?)').bind(id,id+'@fixture.invalid','unused','unused','{}',new Date().toISOString()).run();
});
after(async()=>{await mf?.dispose();});

test('web push transport module exists',()=>assert.ok(push,'web push transport module must exist'));

test('public capability returns only a validated VAPID public key',async()=>{
 assert.deepEqual(await push.pushPublicConfig({}),{configured:false,public_key:null});
 assert.deepEqual(await push.pushPublicConfig(env),{configured:true,public_key:env.WEB_PUSH_PUBLIC_KEY});
 assert.equal((await push.pushPublicConfig({...env,WEB_PUSH_PUBLIC_KEY:'invalid'})).configured,false);
 assert.equal((await push.pushPublicConfig({...env,WEB_PUSH_PRIVATE_KEY:'invalid'})).configured,false);
 assert.equal((await push.pushPublicConfig({...env,WEB_PUSH_SUBJECT:'http://localhost'})).configured,false);
 assert.ok(!JSON.stringify(await push.pushPublicConfig(env)).includes(env.WEB_PUSH_PRIVATE_KEY));
});

test('subscription validation permits only supported public push services and valid keys',async()=>{
 for(const endpoint of ['https://fcm.googleapis.com/fcm/send/token','https://updates.push.services.mozilla.com/wpush/v2/token','https://wns2-by3p.notify.windows.com/w/?token=fixture-token','https://web.push.apple.com/fixture-token'])assert.equal((await push.validateSubscription(subscription(endpoint))).endpoint,endpoint);
 for(const endpoint of ['http://fcm.googleapis.com/fcm/send/token','https://127.0.0.1/fcm/send/token','https://[::1]/wpush/token','https://192.168.1.2/wpush/token','https://fcm.googleapis.com.evil.test/fcm/send/token','https://evil.test@fcm.googleapis.com/fcm/send/token','https://fcm.googleapis.com:444/fcm/send/token','https://fcm.googleapis.com/other/token','https://fcm.googleapis.com/fcm/send/token#fragment','https://notify.windows.com/w/?token=x','https://x.notify.windows.com.evil.test/w/?token=x','https://x.notify.windows.com/w/?url=http://localhost','https://web.push.apple.com/'])await assert.rejects(push.validateSubscription(subscription(endpoint)),error=>error.status===422,endpoint);
 for(const changes of [{keys:{p256dh:'A'.repeat(87),auth:subscription().keys.auth}},{keys:{p256dh:subscription().keys.p256dh,auth:'a'.repeat(20)}},{keys:{p256dh:subscription().keys.p256dh,auth:subscription().keys.auth+'='}},{expirationTime:0},{expirationTime:Date.now()-1},{user_id:'other'}])await assert.rejects(push.validateSubscription({...subscription(),...changes}),error=>error.status===422);
});

test('encrypted request decrypts at the recipient and carries a verifiable origin-bound VAPID JWT',async()=>{
 let captured;const result=await push.sendWebPush(env,[subscription()],'reminder-fixture',async(url,init)=>{captured={url,init};return new Response('',{status:201});});
 assert.equal(result.sent,1);assert.equal(result.failed,0);assert.equal(captured.init.redirect,'manual');assert.equal(captured.url,subscription().endpoint);
 const headers=new Headers(captured.init.headers);assert.equal(headers.get('Content-Encoding'),'aes128gcm');
 const authorization=headers.get('Authorization'),jwt=authorization.match(/\bt=([^, ]+)/)?.[1],publicKey=authorization.match(/\bk=([^, ]+)/)?.[1];assert.equal(publicKey,env.WEB_PUSH_PUBLIC_KEY);
 const [head,claims,signature]=jwt.split('.');const decoded=JSON.parse(Buffer.from(claims,'base64url'));assert.equal(decoded.aud,'https://fcm.googleapis.com');assert.equal(decoded.sub,env.WEB_PUSH_SUBJECT);
 const key=await crypto.subtle.importKey('raw',Buffer.from(publicKey,'base64url'),{name:'ECDSA',namedCurve:'P-256'},false,['verify']);assert.equal(await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},key,Buffer.from(signature,'base64url'),Buffer.from(head+'.'+claims)),true);
 const encrypted=Buffer.from(captured.init.body),salt=encrypted.subarray(0,16),keyLength=encrypted[20],serverPublic=encrypted.subarray(21,21+keyLength),ciphertext=encrypted.subarray(21+keyLength);
 const shared=recipient.computeSecret(serverPublic),ikm=Buffer.from(hkdfSync('sha256',shared,auth,Buffer.concat([Buffer.from('WebPush: info\0'),recipient.getPublicKey(),serverPublic]),32));
 const contentKey=hkdfSync('sha256',ikm,salt,Buffer.from('Content-Encoding: aes128gcm\0'),16),nonce=hkdfSync('sha256',ikm,salt,Buffer.from('Content-Encoding: nonce\0'),12);
 const decipher=createDecipheriv('aes-128-gcm',contentKey,nonce);decipher.setAuthTag(ciphertext.subarray(-16));const clear=Buffer.concat([decipher.update(ciphertext.subarray(0,-16)),decipher.final()]);const delimiter=clear.findLastIndex(value=>value!==0);assert.equal(clear[delimiter],2);
 const notification=JSON.parse(clear.subarray(0,delimiter));assert.deepEqual(notification,{title:'小伴有一条新提醒',body:'打开小伴，查看今天的陪伴消息。',url:'/',tag:'reminder-fixture',reminder_id:'reminder-fixture'});
 assert.ok(!encrypted.includes(Buffer.from('reminder-fixture')));
});

test('only 404 and 410 mark subscriptions dead, redirects are not followed',async()=>{
 const inputs=[404,410,403,307,201].map((status,i)=>subscription('https://fcm.googleapis.com/fcm/send/'+status+'-'+i));let calls=0;
 const result=await push.sendWebPush(env,inputs,'generic',async(url,init)=>{assert.equal(init.redirect,'manual');const status=[404,410,403,307,201][calls++];return new Response('',{status,headers:status===307?{Location:'http://127.0.0.1/collect'}:{}});});
 assert.equal(calls,5);assert.equal(result.sent,1);assert.equal(result.failed,4);assert.deepEqual(result.expired_endpoints,inputs.slice(0,2).map(x=>x.endpoint));assert.deepEqual(result.sent_endpoints,[inputs[4].endpoint]);
 const invalid=await push.sendWebPush(env,[subscription('https://localhost/private')],'generic',()=>assert.fail('invalid endpoint must never be fetched'));assert.equal(invalid.failed,1);
 assert.equal((await push.sendWebPush({},inputs,'generic',()=>assert.fail('unconfigured transport must never fetch'))).configured,false);
});

test('subscriptions are owner-scoped, cannot be stolen, respect an atomic five-device limit, and cascade',async()=>{
 const a='push-owner-a',b='push-owner-b',shared=subscription('https://fcm.googleapis.com/fcm/send/shared');
 assert.deepEqual(await push.subscribePush(db,a,shared,new Date()),{subscribed:true});
 await assert.rejects(push.subscribePush(db,b,shared,new Date()),error=>error.status===409);
 assert.equal((await push.pushSubscriptions(db,a)).length,1);assert.equal((await push.pushSubscriptions(db,b)).length,0);
 assert.deepEqual(await push.unsubscribePush(db,b,shared.endpoint),{unsubscribed:true});assert.equal((await push.pushSubscriptions(db,a)).length,1);
 const attempts=await Promise.allSettled(Array.from({length:7},(_,i)=>push.subscribePush(db,a,subscription('https://fcm.googleapis.com/fcm/send/device-'+i),new Date())));
 assert.equal(attempts.filter(x=>x.status==='fulfilled').length,4);assert.equal((await push.pushSubscriptions(db,a)).length,5);
 assert.deepEqual(await push.subscribePush(db,a,shared,new Date()),{subscribed:true});
 await push.unsubscribePush(db,a,shared.endpoint);assert.deepEqual(await push.subscribePush(db,b,shared,new Date()),{subscribed:true});
 await db.prepare('DELETE FROM users WHERE id=?').bind(a).run();assert.equal((await push.pushSubscriptions(db,a)).length,0);assert.equal((await push.pushSubscriptions(db,b)).length,1);
});
