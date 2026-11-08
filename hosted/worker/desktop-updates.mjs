/* Public signed portable releases. Executable publication is independent of app roles. */
const ORIGIN='https://xiaoban-companion.abloom-haven-3343.chatgpt.site';
const MAX_BYTES=64*1024*1024;
const LATEST='desktop_update_latest';
const LEASE='desktop_update_publish_lease';
const VERSION_PREFIX='desktop_update_version:';
const encoder=new TextEncoder();
const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
class UpdateError extends Error{constructor(status,message){super(message);this.status=status;}}
const fail=(status,message)=>{throw new UpdateError(status,message);};
function equal(a,b){if(typeof a!=='string'||typeof b!=='string'||a.length!==b.length)return false;let n=0;for(let i=0;i<a.length;i++)n|=a.charCodeAt(i)^b.charCodeAt(i);return n===0;}
function versionParts(value){if(typeof value!=='string'||! /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value))return null;const parts=value.split('.').map(Number);return parts.every(Number.isSafeInteger)?parts:null;}
function compareVersions(a,b){const x=versionParts(a),y=versionParts(b);if(!x||!y)fail(503,'更新版本记录无效');for(let i=0;i<3;i++)if(x[i]!==y[i])return x[i]>y[i]?1:-1;return 0;}
function decodeBase64(value,max=16384){if(typeof value!=='string'||!value||value.length>max||value.length%4!==0||! /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value))throw Error('base64');return Uint8Array.from(atob(value),c=>c.charCodeAt(0));}
const hex=buffer=>Array.from(new Uint8Array(buffer),b=>b.toString(16).padStart(2,'0')).join('');
const hashBytes=value=>Uint8Array.from(value.match(/../g),b=>parseInt(b,16));
const objectKey=(version,sha256)=>`desktop-updates/windows-x64/${version}/${sha256}.exe`;
function metadataOf(request){
 let metadata;
 try{metadata=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(decodeBase64(request.headers.get('X-Update-Metadata'))));}catch{fail(422,'更新元数据无效');}
 if(!metadata||typeof metadata!=='object'||Array.isArray(metadata)||Object.keys(metadata).sort().join(',')!=='notes,platform,sha256,size')fail(422,'更新元数据字段无效');
 if(typeof metadata.sha256!=='string'||! /^[0-9a-f]{64}$/.test(metadata.sha256)||!Number.isSafeInteger(metadata.size)||metadata.size<1)fail(422,'更新文件摘要或大小无效');
 if(metadata.size>MAX_BYTES)fail(413,'更新文件不能超过 64 MiB');
 if(metadata.platform!=='windows-x64'||typeof metadata.notes!=='string'||Array.from(metadata.notes).length>2000||/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(metadata.notes))fail(422,'更新平台或说明无效');
 return metadata;
}
/* Payload fields use integer values and valid Unicode, matching Python ensure_ascii=False. */
function canonical(value){if(value===null||typeof value!=='object')return JSON.stringify(value);if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';}
async function getState(db,key){return (await db.prepare('SELECT value FROM service_state WHERE state_key=?').bind(key).first())?.value??null;}
function envelopeOf(raw){try{const envelope=JSON.parse(raw);if(!envelope?.payload||typeof envelope.signature!=='string')throw 0;return envelope;}catch{fail(503,'更新发布记录暂时不可用');}}
async function signingKey(env){
 try{return await crypto.subtle.importKey('pkcs8',decodeBase64(env.DESKTOP_UPDATE_SIGNING_KEY,4096),{name:'Ed25519'},false,['sign']);}catch{fail(503,'更新签名服务尚未配置');}
}
async function drainOperatorBody(request,env){
 /* Finish a bounded, authenticated upload even for an idempotent retry or a
    metadata conflict. Returning while its HTTP/1 body is still arriving can
    reset the connection before the publisher receives the response. */
 const length=Number(request.headers.get('Content-Length'));
 if(!request.body||request.bodyUsed||request.body.locked||!Number.isSafeInteger(length)||length<1||length>MAX_BYTES||!equal(request.headers.get('Authorization'),'Bearer '+env.DESKTOP_UPDATE_PUBLISH_TOKEN))return;
 const reader=request.body.getReader();let received=0;
 const timer=setTimeout(()=>{reader.cancel().catch(()=>{});},120000);
 try{while(true){const {done,value}=await reader.read();if(done)break;received+=value.byteLength;if(received>MAX_BYTES){await reader.cancel();break;}}}catch{}finally{clearTimeout(timer);reader.releaseLock();}
}
async function publish(request,env,version){
 if(!env.DESKTOP_UPDATE_PUBLISH_TOKEN||env.DESKTOP_UPDATE_PUBLISH_TOKEN.length<32)fail(503,'更新发布服务尚未配置');
 if(!equal(request.headers.get('Authorization'),'Bearer '+env.DESKTOP_UPDATE_PUBLISH_TOKEN))fail(401,'更新发布凭证无效');
 if(!env.DB||!env.UPDATES)fail(503,'更新存储服务尚未配置');
 if(!versionParts(version))fail(422,'更新版本必须是规范的三段数字');
 if(request.headers.get('Content-Type')?.toLowerCase()!=='application/octet-stream'||!['','identity'].includes((request.headers.get('Content-Encoding')||'').toLowerCase()))fail(415,'请上传原始二进制更新文件');
 const metadata=metadataOf(request),length=request.headers.get('Content-Length');
 if(length===null)fail(411,'上传更新文件需要 Content-Length');
 if(! /^(0|[1-9]\d*)$/.test(length)||!Number.isSafeInteger(Number(length)))fail(422,'更新文件长度无效');
 if(Number(length)>MAX_BYTES)fail(413,'更新文件不能超过 64 MiB');
 if(Number(length)!==metadata.size||!request.body)fail(422,'更新文件长度与元数据不一致');
 const key=await signingKey(env),db=env.DB;
 const lease=JSON.stringify({owner:crypto.randomUUID(),expires_at:Date.now()+300000});
 const claim=await db.prepare("INSERT INTO service_state(state_key,value) VALUES(?,?) ON CONFLICT(state_key) DO UPDATE SET value=excluded.value WHERE CAST(json_extract(service_state.value,'$.expires_at') AS INTEGER)<=?").bind(LEASE,lease,Date.now()).run();
 if(!claim.meta.changes)fail(409,'已有更新正在发布，请稍后重试');
 try{
  const previousRaw=await getState(db,LATEST),previous=previousRaw===null?null:envelopeOf(previousRaw);
  const versionKey=VERSION_PREFIX+version,existingRaw=await getState(db,versionKey);
  if(existingRaw!==null){const existing=envelopeOf(existingRaw);if(['sha256','size','platform','notes'].every(field=>existing.payload[field]===metadata[field]))return json(existing);fail(409,'此版本已经发布，不能修改文件或说明');}
  if(previous&&compareVersions(version,previous.payload.version)<=0)fail(409,'更新版本必须高于已发布版本');
  const path=objectKey(version,metadata.sha256),oldObject=await env.UPDATES.head(path);
  if(oldObject){if(oldObject.size!==metadata.size||!oldObject.checksums.sha256||hex(oldObject.checksums.sha256)!==metadata.sha256)fail(409,'此更新对象已存在且校验信息不一致');}
  else{
   /* FixedLengthStream enforces the byte cap without buffering the EXE in Worker RAM.
      R2 verifies SHA256 before committing the object, including chunked input. */
   const stream=new FixedLengthStream(metadata.size),abort=new AbortController();
   const timer=setTimeout(()=>abort.abort(new Error('upload deadline')),120000);
   const pipe=request.body.pipeTo(stream.writable,{signal:abort.signal});
   const put=env.UPDATES.put(path,stream.readable,{sha256:hashBytes(metadata.sha256),onlyIf:new Headers({'If-None-Match':'*'}),httpMetadata:{contentType:'application/vnd.microsoft.portable-executable',cacheControl:'public, max-age=31536000, immutable'}});
   try{const [,stored]=await Promise.all([pipe,put]);if(!stored)fail(409,'更新对象已存在，请重试');if(stored.size!==metadata.size||!stored.checksums.sha256||hex(stored.checksums.sha256)!==metadata.sha256)fail(422,'更新文件校验失败');}
   catch(error){abort.abort();await Promise.allSettled([pipe,put]);if(error instanceof UpdateError)throw error;fail(422,'更新文件长度、摘要或上传流校验失败');}
   finally{clearTimeout(timer);}
  }
  const payload={schema:1,version,platform:metadata.platform,url:ORIGIN+`/api/desktop-updates/assets/${version}/${metadata.sha256}.exe`,sha256:metadata.sha256,size:metadata.size,published_at:new Date().toISOString(),notes:metadata.notes};
  const signature=btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.sign('Ed25519',key,encoder.encode(canonical(payload))))));
  const envelope={payload,signature},raw=JSON.stringify(envelope);
  /* Atomic lease ownership + latest-value CAS prevents a slow expired publisher
     from activating after a newer lease owner. No request-time schema changes. */
  const guard="EXISTS(SELECT 1 FROM service_state WHERE state_key=? AND value=? AND CAST(json_extract(value,'$.expires_at') AS INTEGER)>?) AND (SELECT value FROM service_state WHERE state_key=?) IS ?";
  const guardArgs=[LEASE,lease,Date.now(),LATEST,previousRaw];
  const results=await db.batch([
   db.prepare(`INSERT INTO service_state(state_key,value) SELECT ?,? WHERE ${guard} ON CONFLICT(state_key) DO NOTHING`).bind(versionKey,raw,...guardArgs),
   db.prepare(`INSERT INTO service_state(state_key,value) SELECT ?,? WHERE ${guard} AND EXISTS(SELECT 1 FROM service_state WHERE state_key=? AND value=?) ON CONFLICT(state_key) DO UPDATE SET value=excluded.value WHERE service_state.value IS ?`).bind(LATEST,raw,...guardArgs,versionKey,raw,previousRaw),
  ]);
  if(!results[1].meta.changes)fail(409,'发布租约已过期或已有新版本，请重新检查');
  return json(envelope,201);
 }finally{await db.prepare('DELETE FROM service_state WHERE state_key=? AND value=?').bind(LEASE,lease).run();}
}
async function asset(request,env,version,sha256){
 if(!env.DB||!env.UPDATES)fail(503,'更新存储服务尚未配置');
 if(!versionParts(version))fail(404,'更新文件不存在');
 const raw=await getState(env.DB,VERSION_PREFIX+version);
 if(raw===null||envelopeOf(raw).payload.sha256!==sha256)fail(404,'更新文件不存在');
 const payload=envelopeOf(raw).payload,key=objectKey(version,sha256);
 const object=await (request.method==='HEAD'?env.UPDATES.head(key):env.UPDATES.get(key));
 if(!object||object.size!==payload.size||!object.checksums.sha256||hex(object.checksums.sha256)!==sha256)fail(503,'更新文件暂时不可用');
 return new Response(request.method==='HEAD'?null:object.body,{headers:{'Content-Type':'application/vnd.microsoft.portable-executable','Content-Length':String(object.size),'Content-Disposition':`attachment; filename="Xiaoban-${version}-windows-x64.exe"`,'Cache-Control':'public, max-age=31536000, immutable','ETag':object.httpEtag,'X-Content-Type-Options':'nosniff'}});
}

export async function handleDesktopUpdates(request,env){
 const path=new URL(request.url).pathname;
 if(!path.startsWith('/api/desktop-updates/')&&!path.startsWith('/api/internal/desktop-updates/'))return null;
 try{
  if(path==='/api/desktop-updates/latest'){
   if(!['GET','HEAD'].includes(request.method))fail(405,'方法不支持');
   if(!env.DB)fail(503,'更新数据服务尚未配置');
   const raw=await getState(env.DB,LATEST);if(raw===null)fail(404,'尚未发布便携版更新');
   const response=json(envelopeOf(raw));return request.method==='HEAD'?new Response(null,{status:200,headers:response.headers}):response;
  }
  const upload=path.match(/^\/api\/internal\/desktop-updates\/([^/]+)$/);
  if(upload){if(request.method!=='PUT')fail(405,'方法不支持');try{return await publish(request,env,upload[1]);}finally{await drainOperatorBody(request,env);}}
  const download=path.match(/^\/api\/desktop-updates\/assets\/([^/]+)\/([0-9a-f]{64})\.exe$/);
  if(download){if(!['GET','HEAD'].includes(request.method))fail(405,'方法不支持');return await asset(request,env,download[1],download[2]);}
  fail(404,'更新接口不存在');
 }catch(error){if(error instanceof UpdateError)return json({detail:error.message},error.status);return json({detail:'更新服务暂时不可用，请稍后重试'},503);}
}
