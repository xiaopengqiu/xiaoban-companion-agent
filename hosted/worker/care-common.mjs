export class CareError extends Error { constructor(status,message){super(message);this.status=status;} }
export const fail=(status,message)=>{throw new CareError(status,message);};
export const json=(data,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
export const uuid=()=>crypto.randomUUID().replaceAll('-','');
export const one=(env,sql,...args)=>env.DB.prepare(sql).bind(...args).first();
export const all=async(env,sql,...args)=>(await env.DB.prepare(sql).bind(...args).all()).results||[];
export const run=(env,sql,...args)=>env.DB.prepare(sql).bind(...args).run();
export const state=async(env,key)=>(await one(env,'SELECT value FROM service_state WHERE state_key=?',key))?.value??null;
export const saveState=(env,key,value)=>run(env,'INSERT INTO service_state(state_key,value) VALUES(?,?) ON CONFLICT(state_key) DO UPDATE SET value=excluded.value',key,JSON.stringify(value));
export const hash=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),v=>v.toString(16).padStart(2,'0')).join('');
export async function input(request,limit=32000){
 const reader=request.body?.getReader();let bytes=0,chunks=[];
 try{if(reader)while(true){const r=await reader.read();if(r.done)break;bytes+=r.value.byteLength;if(bytes>limit){await reader.cancel();fail(413,'配置或消息过大');}chunks.push(r.value);}
  const raw=new Uint8Array(bytes);let offset=0;for(const c of chunks){raw.set(c,offset);offset+=c.length;}
  const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw)||'{}');
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error();return value;
 }catch(error){if(error instanceof CareError)throw error;fail(422,'请输入有效 JSON 对象');}finally{reader?.releaseLock();}
}
export function fields(value,allowed){if(Object.keys(value).some(k=>!allowed.includes(k)))fail(422,'配置或消息字段无效');}
export function text(value,label,max=2000,min=1){if(typeof value!=='string'||value.trim().length<min||value.length>max)fail(422,label+'无效');return value.trim();}
export const adminGuard="EXISTS(SELECT 1 FROM users actor WHERE actor.id=? AND json_type(actor.data,'$.is_admin')='true' AND COALESCE(json_extract(actor.data,'$.is_demo'),0)=0)";
export async function requireAdmin(env,user){
 if(!user||!await one(env,"SELECT 1 FROM users WHERE id=? AND json_type(data,'$.is_admin')='true' AND COALESCE(json_extract(data,'$.is_demo'),0)=0",user.id))fail(403,'仅管理员可以管理主动关心与通知服务');
}
export function localParts(now,zone){
 const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(now).map(x=>[x.type,x.value]));
 return {date:`${p.year}-${p.month}-${p.day}`,time:`${p.hour}:${p.minute}`};
}
export function quiet(now,prefs){const {time}=localParts(now,prefs.timezone||'Asia/Shanghai'),start=prefs.quiet_start||'22:00',end=prefs.quiet_end||'08:00';return start!==end&&(start<end?time>=start&&time<end:time>=start||time<end);}
export async function lease(env,userId,key,now,fn){
 const owner=uuid();const result=await run(env,'INSERT INTO leases(lease_key,user_id,owner,expires_at) VALUES(?,?,?,?) ON CONFLICT(lease_key) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at WHERE leases.expires_at<=?',key,userId,owner,+now+120000,+now);
 if(!result.meta.changes)fail(409,'当前操作正在处理，请稍后重试');
 try{return await fn(owner);}finally{await run(env,'DELETE FROM leases WHERE lease_key=? AND owner=?',key,owner);}
}
