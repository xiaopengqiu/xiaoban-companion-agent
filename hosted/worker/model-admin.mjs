// Only administrators select transports. Browser users never supply execution arguments.
const encoder=new TextEncoder();
const fields=new Set(['mode','api_url','api_key','model','local_provider','local_model','gateway_url','gateway_key']);
export class ModelRuntimeError extends Error {}
const fail=message=>{throw new ModelRuntimeError(message);};
const reply=(data,status=200)=>Response.json(data,{status,headers:{'cache-control':'no-store','x-content-type-options':'nosniff'}});
const base64=data=>btoa(String.fromCharCode(...new Uint8Array(data)));
const unbase64=text=>Uint8Array.from(atob(text),c=>c.charCodeAt(0));
function defaults(env){return {mode:env.COMPANION_MODEL_BASE_URL&&env.COMPANION_MODEL_API_KEY&&env.COMPANION_MODEL?'api':'disabled',api_url:env.COMPANION_MODEL_BASE_URL||'',api_key:env.COMPANION_MODEL_API_KEY||'',model:env.COMPANION_MODEL||'',local_provider:'codex',local_model:'',gateway_url:'',gateway_key:''};}
function configured(c){return c.mode==='api'?!!(c.api_url&&c.api_key&&c.model):c.mode==='local'?!!(c.gateway_url&&c.gateway_key&&['codex','claude'].includes(c.local_provider)):false;}
export function publicModelStatus(c){return {configured:!!c.configured,mode:c.mode,provider:c.mode==='local'?c.local_provider:c.mode==='api'?'api':null};}
function publicConfig(c){return {...Object.fromEntries(Object.entries(c).filter(([k])=>!['api_key','gateway_key'].includes(k))),api_key_configured:!!c.api_key,gateway_key_configured:!!c.gateway_key,runtime:'cloud'};}
function validate(data,old){
 if(!data||typeof data!=='object'||Array.isArray(data)||Object.keys(data).some(k=>!fields.has(k)))fail('模型配置字段无效');
 const c={...old,...data};delete c.configured;
 if(typeof c.mode!=='string'||!['disabled','api','local'].includes(c.mode))fail('调用模式无效');
 if(!['codex','claude'].includes(c.local_provider))fail('本地服务只支持 Claude 或 Codex');
 for(const key of fields){if(typeof c[key]!=='string'||c[key].length>(key.endsWith('key')?8192:1000))fail('模型配置字段无效');c[key]=c[key].trim();}
 for(const key of ['api_url','gateway_url'])if(c[key]){let u;try{u=new URL(c[key]);}catch{fail('请填写有效 HTTPS 地址');}if(u.protocol!=='https:'||u.username||u.password||u.search||u.hash)fail('模型与网关地址必须是无凭证的 HTTPS 地址');c[key]=c[key].replace(/\/$/,'');}
 if(c.local_model&&!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$/.test(c.local_model))fail('本地模型名称无效');
 if(c.mode==='api'&&!(c.api_url&&c.api_key&&c.model))fail('请填写 API 地址、密钥和模型名称');
 if(c.mode==='local'&&!(c.gateway_url&&c.gateway_key))fail('云端本地模式需要 HTTPS 网关地址和访问令牌');
 return c;
}
async function cryptoKey(env){const secret=env.COMPANION_CONFIG_ENCRYPTION_KEY||env.AUTH_PEPPER;if(typeof secret!=='string'||secret.length<32)fail('服务端尚未配置模型设置加密密钥');const hash=await crypto.subtle.digest('SHA-256',encoder.encode('xiaoban-model-settings-v1\0'+secret));return crypto.subtle.importKey('raw',hash,'AES-GCM',false,['encrypt','decrypt']);}
async function encrypt(env,c){const iv=crypto.getRandomValues(new Uint8Array(12)),data=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode('xiaoban-model-settings-v1')},await cryptoKey(env),encoder.encode(JSON.stringify(c)));return JSON.stringify({version:1,iv:base64(iv),data:base64(data)});}
async function decrypt(env,text){try{const doc=JSON.parse(text);if(doc.version!==1)fail('模型设置版本无效');const raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:unbase64(doc.iv),additionalData:encoder.encode('xiaoban-model-settings-v1')},await cryptoKey(env),unbase64(doc.data));return JSON.parse(new TextDecoder().decode(raw));}catch{fail('模型设置无法解密，请检查服务端加密密钥');}}
export async function getModelConfig(env){const row=await env.DB.prepare("SELECT value FROM service_state WHERE state_key='model_settings'").first();const c=row?await decrypt(env,row.value):defaults(env);return {...c,configured:configured(c)};}
async function boundedJson(request,limit=16000){const reader=request.body?.getReader();let chunks=[],size=0;try{if(reader)while(true){const r=await reader.read();if(r.done)break;size+=r.value.byteLength;if(size>limit){await reader.cancel();fail('请求内容过大');}chunks.push(r.value);}const raw=new Uint8Array(size);let offset=0;for(const c of chunks){raw.set(c,offset);offset+=c.length;}const text=new TextDecoder('utf-8',{fatal:true}).decode(raw);return text?JSON.parse(text):{};}catch(error){if(error instanceof ModelRuntimeError)throw error;fail('请输入有效 JSON 对象');}finally{reader?.releaseLock();}}
export async function completeModel(env,messages){
 const c=await getModelConfig(env);if(!c.configured)fail('模型服务尚未配置');
 const local=c.mode==='local',url=local?c.gateway_url:c.api_url,key=local?c.gateway_key:c.api_key;
 try{
  const target=new URL(url);if(target.protocol!=='https:')throw Error();
  const payload={model:local?c.local_provider:c.model,messages,max_tokens:700,temperature:.4};if(local&&c.local_model)payload.local_model=c.local_model;
  const response=await fetch(url.replace(/\/$/,'')+'/chat/completions',{method:'POST',redirect:'manual',headers:{'Content-Type':'application/json',Authorization:'Bearer '+key},signal:AbortSignal.timeout(local?50000:20000),body:JSON.stringify(payload)});
  if(!response.ok)throw Error();const result=await boundedJson(response,128000),text=result.choices?.[0]?.message?.content;
  if(typeof text!=='string'||!text.trim())throw Error();return text.trim().slice(0,6000);
 }catch{fail('模型服务暂时不可用，请检查管理员配置');}
}
function equal(a,b){if(typeof a!=='string'||typeof b!=='string'||a.length!==b.length)return false;let delta=0;for(let i=0;i<a.length;i++)delta|=a.charCodeAt(i)^b.charCodeAt(i);return delta===0;}
function userSummary(value){return {id:value.id,name:value.name,email:value.email,is_admin:value.is_admin===true,is_demo:value.is_demo===true,created_at:value.created_at};}
export async function handleModelAdmin(request,env,user){
 const route=new URL(request.url).pathname;
 if(route==='/api/internal/admin/grant'){
  const secret=env.ADMIN_SETUP_TOKEN||'',supplied=(request.headers.get('Authorization')||'').replace(/^Bearer /,'');
  if(secret.length<32||!equal(secret,supplied))return reply({detail:'需要服务端初始化授权'},401);
  if(request.method!=='POST')return reply({detail:'方法不支持'},405);
  try{
   const data=await boundedJson(request);if(!data||Object.keys(data).length!==1||typeof data.email!=='string')return reply({detail:'请指定已注册账号的邮箱'},422);
   if(await env.DB.prepare("SELECT 1 FROM service_state WHERE state_key='admin_bootstrap_used'").first())return reply({detail:'管理员初始化已经完成'},409);
   const target=await env.DB.prepare('SELECT id,data FROM users WHERE email=?').bind(data.email.trim().toLowerCase()).first();if(!target||JSON.parse(target.data).is_demo)return reply({detail:'请先注册一个正式的小伴账号'},404);
   const result=await env.DB.batch([
    env.DB.prepare("UPDATE users SET data=json_set(data,'$.is_admin',json('true')) WHERE id=? AND NOT EXISTS(SELECT 1 FROM service_state WHERE state_key='admin_bootstrap_used')").bind(target.id),
    env.DB.prepare("INSERT OR IGNORE INTO service_state(state_key,value) SELECT 'admin_bootstrap_used','true' WHERE changes()>0")]);
   return result[0].meta.changes?reply({granted:true}):reply({detail:'管理员初始化已经完成'},409);
  }catch(error){return reply({detail:error instanceof ModelRuntimeError?error.message:'管理员初始化暂不可用'},422);}
 }
 const rolePath=route.match(/^\/api\/admin\/users\/([^/]+)\/role$/);
 if(!['/api/admin/model-config','/api/admin/model-test','/api/admin/users'].includes(route)&&!rolePath||!user)return null;
 const actor=await env.DB.prepare("SELECT 1 FROM users WHERE id=? AND json_extract(data,'$.is_admin')=1 AND COALESCE(json_extract(data,'$.is_demo'),0)=0").bind(user.id).first();
 if(!actor)return reply({detail:'仅管理员可以管理用户和模型服务'},403);
 try{
  if(route==='/api/admin/users'&&request.method==='GET'){
   const url=new URL(request.url),email=url.searchParams.get('email');if(email!==null&&(!email.trim()||email.length>200))return reply({detail:'请填写有效的邮箱筛选条件'},422);
   const query=email===null?env.DB.prepare('SELECT data FROM users ORDER BY created_at DESC,id DESC LIMIT 100'):env.DB.prepare('SELECT data FROM users WHERE email=? ORDER BY created_at DESC,id DESC LIMIT 100').bind(email.trim().toLowerCase());
   return reply({items:(await query.all()).results.map(row=>userSummary(JSON.parse(row.data)))});
  }
  if(rolePath&&request.method==='POST'){
   const data=await boundedJson(request);if(!data||typeof data!=='object'||Array.isArray(data)||Object.keys(data).length!==1||typeof data.is_admin!=='boolean')return reply({detail:'请明确选择管理员权限'},422);
   const target=await env.DB.prepare('SELECT data FROM users WHERE id=?').bind(rolePath[1]).first();if(!target)return reply({detail:'用户不存在'},404);
   const value=JSON.parse(target.data);if(value.is_demo)return reply({detail:'演示账号不能成为管理员'},422);
   const updated=await env.DB.prepare("UPDATE users SET data=json_set(data,'$.is_admin',json(?)) WHERE id=? AND COALESCE(json_extract(data,'$.is_demo'),0)=0 AND (?=1 OR COALESCE(json_extract(data,'$.is_admin'),0)<>1 OR (SELECT count(*) FROM users WHERE json_extract(data,'$.is_admin')=1)>1) AND EXISTS(SELECT 1 FROM users actor WHERE actor.id=? AND json_extract(actor.data,'$.is_admin')=1 AND COALESCE(json_extract(actor.data,'$.is_demo'),0)=0)")
    .bind(JSON.stringify(data.is_admin),rolePath[1],Number(data.is_admin),user.id).run();
   if(!updated.meta.changes)return reply({detail:'必须保留至少一名管理员，或当前权限已变化'},409);
   return reply(userSummary({...value,is_admin:data.is_admin}));
  }
  if(route.endsWith('model-test')&&request.method==='POST'){
   try{await completeModel(env,[{role:'user',content:'这是模型连接测试。请只回复：连接成功。'}]);return reply({ok:true,detail:'模型服务已返回有效回复'});}catch{return reply({ok:false,detail:'模型调用失败，请检查管理员配置及本地 CLI 登录状态'});}
  }
  if(route.endsWith('model-config')&&request.method==='GET')return reply(publicConfig(await getModelConfig(env)));
  if(route.endsWith('model-config')&&request.method==='PUT'){
   const config=validate(await boundedJson(request),await getModelConfig(env)),encrypted=await encrypt(env,config);
   const saved=await env.DB.prepare("INSERT INTO service_state(state_key,value) SELECT 'model_settings',? WHERE EXISTS(SELECT 1 FROM users WHERE id=? AND json_extract(data,'$.is_admin')=1) ON CONFLICT(state_key) DO UPDATE SET value=excluded.value WHERE EXISTS(SELECT 1 FROM users WHERE id=? AND json_extract(data,'$.is_admin')=1)").bind(encrypted,user.id,user.id).run();
   if(!saved.meta.changes)return reply({detail:'当前账号已无管理员权限'},403);
   return reply(publicConfig({...config,configured:configured(config)}));
  }
  return reply({detail:'方法不支持'},405);
 }catch(error){return reply({detail:error instanceof ModelRuntimeError?error.message:'模型设置暂不可用'},422);}
}
