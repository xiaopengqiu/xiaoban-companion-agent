/* RFC 8291 encryption and RFC 8292 VAPID are provided by the WebCrypto library. */
import {buildPushPayload} from '@block65/webcrypto-web-push';

export class WebPushError extends Error {constructor(message,status=422){super(message);this.status=status;}}
const invalid=()=>{throw new WebPushError('浏览器推送订阅无效，请重新开启通知');};
const encoder=new TextEncoder();
const encode=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
function decode(value,length){
 if(typeof value!=='string'||!/^[A-Za-z0-9_-]+$/.test(value))invalid();
 let bytes;try{bytes=Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/')+'='.repeat((4-value.length%4)%4)),x=>x.charCodeAt(0));}catch{invalid();}
 if(bytes.length!==length||encode(bytes)!==value)invalid();return bytes;
}
function endpoint(value){
 if(typeof value!=='string'||value.length>4096||value!==value.trim())invalid();
 let url;try{url=new URL(value);}catch{invalid();}
 if(url.protocol!=='https:'||url.username||url.password||url.port||url.hash)invalid();
 const host=url.hostname,path=url.pathname;
 const allowed=host==='fcm.googleapis.com'&&path.startsWith('/fcm/send/')&&path.length>10&&!url.search
  ||host==='updates.push.services.mozilla.com'&&path.startsWith('/wpush/')&&path.length>7&&!url.search
  ||/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+notify\.windows\.com$/.test(host)&&path==='/w/'&&url.searchParams.size===1&&!!url.searchParams.get('token')
  ||host==='web.push.apple.com'&&path.length>1&&!url.search;
 if(!allowed)invalid();return url.href;
}
async function applicationKeys(env){
 try{
  const publicBytes=decode(env.WEB_PUSH_PUBLIC_KEY,65);if(publicBytes[0]!==4)invalid();decode(env.WEB_PUSH_PRIVATE_KEY,32);
  const subject=env.WEB_PUSH_SUBJECT;if(typeof subject!=='string'||subject.length>500)invalid();
  if(subject.startsWith('mailto:')){if(!/^mailto:[^\s@/?#]+@[^\s@/?#]+\.[^\s@/?#]+$/.test(subject))invalid();}
  else{const contact=new URL(subject);if(contact.protocol!=='https:'||contact.username||contact.password||!contact.hostname.includes('.')||contact.hash)invalid();}
  const publicKey=await crypto.subtle.importKey('raw',publicBytes,{name:'ECDSA',namedCurve:'P-256'},false,['verify']);
  const privateKey=await crypto.subtle.importKey('jwk',{kty:'EC',crv:'P-256',x:encode(publicBytes.slice(1,33)),y:encode(publicBytes.slice(33)),d:env.WEB_PUSH_PRIVATE_KEY},{name:'ECDSA',namedCurve:'P-256'},false,['sign']);
  const challenge=encoder.encode('xiaoban-vapid-key-validation');const signed=await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},privateKey,challenge);
  if(!await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},publicKey,signed,challenge))invalid();
  return {subject,publicKey:env.WEB_PUSH_PUBLIC_KEY,privateKey:env.WEB_PUSH_PRIVATE_KEY};
 }catch{return null;}
}
export async function pushPublicConfig(env){const keys=await applicationKeys(env);return {configured:!!keys,public_key:keys?.publicKey||null};}

export async function validateSubscription(input){
 if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(key=>!['endpoint','expirationTime','keys'].includes(key)))invalid();
 const address=endpoint(input.endpoint),keys=input.keys;
 if(!keys||typeof keys!=='object'||Array.isArray(keys)||Object.keys(keys).some(key=>!['p256dh','auth'].includes(key)))invalid();
 const point=decode(keys.p256dh,65);decode(keys.auth,16);if(point[0]!==4)invalid();
 try{await crypto.subtle.importKey('raw',point,{name:'ECDH',namedCurve:'P-256'},false,[]);}catch{invalid();}
 const expirationTime=input.expirationTime??null;if(expirationTime!==null&&(!Number.isFinite(expirationTime)||expirationTime<=Date.now()))invalid();
 return {endpoint:address,expirationTime,keys:{p256dh:keys.p256dh,auth:keys.auth}};
}

export async function sendWebPush(env,subscriptions,reminderId,fetcher=globalThis.fetch){
 const keys=await applicationKeys(env),result={configured:!!keys,sent:0,failed:0,sent_endpoints:[],expired_endpoints:[]};
 if(!keys)return {...result,status:'not_configured'};
 const targets=Array.isArray(subscriptions)?subscriptions.slice(0,5):[];if(!targets.length)return {...result,status:'no_devices'};
 const tag=typeof reminderId==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(reminderId)?reminderId:'companion-reminder';
 const message={data:JSON.stringify({title:'小伴有一条新提醒',body:'打开小伴，查看今天的陪伴消息。',url:'/',tag,reminder_id:tag}),options:{ttl:300,urgency:'normal'}};
 for(const input of targets){try{
  const subscription=await validateSubscription(input),payload=await buildPushPayload(message,subscription,keys);
  const response=await fetcher(subscription.endpoint,{...payload,redirect:'manual',signal:AbortSignal.timeout(15000)});
  if(response.ok){result.sent++;result.sent_endpoints.push(subscription.endpoint);}
  else{result.failed++;if(response.status===404||response.status===410)result.expired_endpoints.push(subscription.endpoint);}
 }catch{result.failed++;}}
 return {...result,status:result.sent&&!result.failed?'queued':result.sent?'partial':'failed'};
}

export async function subscribePush(db,userId,input,now=new Date()){
 const subscription=await validateSubscription(input);
 const result=await db.prepare(`INSERT INTO web_push_subscriptions(id,user_id,endpoint,data,created_at)
  SELECT ?,?,?,?,? WHERE (SELECT count(*) FROM web_push_subscriptions WHERE user_id=?)<5
   OR EXISTS(SELECT 1 FROM web_push_subscriptions WHERE endpoint=? AND user_id=?)
  ON CONFLICT(endpoint) DO UPDATE SET data=excluded.data WHERE web_push_subscriptions.user_id=excluded.user_id`)
  .bind(crypto.randomUUID().replaceAll('-',''),userId,subscription.endpoint,JSON.stringify(subscription),new Date(now).toISOString(),userId,subscription.endpoint,userId).run();
 if(!result.meta.changes)throw new WebPushError('此浏览器订阅已属于其他账号，或当前账号已达5个浏览器设备上限；请先在原账号关闭通知',409);
 return {subscribed:true};
}
export async function unsubscribePush(db,userId,address){await db.prepare('DELETE FROM web_push_subscriptions WHERE user_id=? AND endpoint=?').bind(userId,endpoint(address)).run();return {unsubscribed:true};}
export async function pushSubscriptions(db,userId){const result=await db.prepare('SELECT data FROM web_push_subscriptions WHERE user_id=? ORDER BY created_at,id LIMIT 5').bind(userId).all();return (result.results||[]).map(row=>JSON.parse(row.data));}
