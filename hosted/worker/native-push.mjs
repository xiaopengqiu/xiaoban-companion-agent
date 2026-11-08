import {CareError,fail,json,uuid,one,all,run,state,input,fields,text,requireAdmin,adminGuard} from './care-common.mjs';
const KEY='native_push_settings',PURPOSE='xiaoban-native-push-v1';
const FIREBASE=['api_key','app_id','messaging_sender_id','project_id'];
const VENDORS=['huawei_app_id','xiaomi_app_id','xiaomi_app_key','oppo_app_key','oppo_app_secret','vivo_app_id','vivo_app_key','honor_app_id'];
const CLIENT_SCOPE={runtime:['app_key','client_options.firebase','client_options.manufacturers.xiaomi_app_id','client_options.manufacturers.xiaomi_app_key','client_options.manufacturers.oppo_app_key','client_options.manufacturers.oppo_app_secret'],requires_rebuild:['client_options.manufacturers.huawei_app_id','client_options.manufacturers.vivo_app_id','client_options.manufacturers.vivo_app_key','client_options.manufacturers.honor_app_id']};
const emptyOptions=()=>({firebase:Object.fromEntries(FIREBASE.map(k=>[k,''])),manufacturers:Object.fromEntries(VENDORS.map(k=>[k,'']))});
const defaults=()=>({enabled:false,provider:'disabled',app_key:'',master_secret:'',revision:0,client_options:emptyOptions()});
const b64=value=>btoa(String.fromCharCode(...new Uint8Array(value)));
const unb64=value=>Uint8Array.from(atob(value),c=>c.charCodeAt(0));
async function key(env){const secret=env.COMPANION_CONFIG_ENCRYPTION_KEY||env.AUTH_PEPPER;if(typeof secret!=='string'||secret.length<32)fail(503,'服务端配置加密尚未就绪');return crypto.subtle.importKey('raw',await crypto.subtle.digest('SHA-256',new TextEncoder().encode(PURPOSE+'\0'+secret)),'AES-GCM',false,['encrypt','decrypt']);}
async function encrypt(env,value){const iv=crypto.getRandomValues(new Uint8Array(12)),raw=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode(PURPOSE)},await key(env),new TextEncoder().encode(JSON.stringify(value)));return JSON.stringify({schema:1,iv:b64(iv),data:b64(raw)});}
export async function getNativePushConfig(env){
 const raw=await state(env,KEY);if(raw===null)return defaults();
 try{const doc=JSON.parse(raw);if(doc.schema!==1)throw Error();const value=await crypto.subtle.decrypt({name:'AES-GCM',iv:unb64(doc.iv),additionalData:new TextEncoder().encode(PURPOSE)},await key(env),unb64(doc.data));return {...defaults(),...JSON.parse(new TextDecoder().decode(value))};}
 catch{fail(503,'推送配置无法读取，请管理员检查服务加密设置');}
}
const configured=c=>c.enabled&&c.provider==='jpush'&&!!c.app_key&&!!c.master_secret;
function publicConfig(c,admin=false){const result={enabled:c.enabled,provider:c.provider,configured:configured(c),app_key:c.app_key,revision:c.revision,client_options:c.client_options,client_config_scope:CLIENT_SCOPE,plan:'free'};if(admin)result.master_secret_configured=!!c.master_secret;return result;}
function options(inputValue,previous){
 if(!inputValue||typeof inputValue!=='object'||Array.isArray(inputValue))fail(422,'客户端推送参数无效');fields(inputValue,['firebase','manufacturers']);
 const result=structuredClone(previous);for(const [section,keys]of [['firebase',FIREBASE],['manufacturers',VENDORS]])if(section in inputValue){const values=inputValue[section];if(!values||typeof values!=='object'||Array.isArray(values))fail(422,'客户端推送参数无效');fields(values,keys);for(const [name,value]of Object.entries(values)){if(typeof value!=='string'||value.length>300||/[\r\n\0]/.test(value))fail(422,'客户端推送参数无效');result[section][name]=value.trim();}}
 return result;
}
export async function sendNativePush(env,userId,data={type:'care_test'},{beforeSend}={}){
 const c=await getNativePushConfig(env);if(!configured(c))return {status:'not_configured',configured:false,queued:0,sent:0,failed:0};
 let devices=await all(env,'SELECT registration_id FROM native_push_devices WHERE user_id=? AND config_revision=? ORDER BY id LIMIT 5',userId,c.revision);
 if(!devices.length)return {status:'no_devices',configured:true,queued:0,sent:0,failed:0};
 if(beforeSend&&!await beforeSend())return {status:'cancelled',configured:true,queued:0,sent:0,failed:0};
 const latest=await getNativePushConfig(env);if(!configured(latest)||latest.revision!==c.revision)return {status:'cancelled',configured:false,queued:0,sent:0,failed:0};
 // Logout or another account's login can rebind an RID during readiness checks.
 devices=await all(env,'SELECT registration_id FROM native_push_devices WHERE user_id=? AND config_revision=? ORDER BY id LIMIT 5',userId,c.revision);
 if(!devices.length)return {status:'no_devices',configured:true,queued:0,sent:0,failed:0};
 try{
  const response=await fetch('https://api.jpush.cn/v3/push',{method:'POST',redirect:'manual',signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json',Authorization:'Basic '+btoa(c.app_key+':'+c.master_secret)},body:JSON.stringify({platform:['android'],audience:{registration_id:devices.map(d=>d.registration_id)},notification:{android:{alert:data.type==='care'?'小伴有一条新的关心，打开查看。':'小伴通知测试，请打开应用查看。',title:'小伴',channel_id:'xiaoban_care',extras:{type:data.type,...(data.care_id?{care_id:data.care_id}:{})}}},options:{time_to_live:3600,apns_production:false}})});
  const failed={status:'failed',configured:true,queued:0,sent:0,failed:devices.length};if(!response.ok)return failed;const value=await input(response,16000);if(value.error||!['string','number'].includes(typeof value.msg_id))return failed;return {status:'queued',configured:true,queued:devices.length,sent:devices.length,failed:0};
 }catch{return {status:'failed',configured:true,queued:0,sent:0,failed:devices.length};}
}
export async function handleNativePush(request,env,user){
 const path=new URL(request.url).pathname.slice(4),method=request.method;
 if(!['/admin/push-config','/admin/push-test','/push/native-config','/push/devices'].includes(path))return null;
 try{
  if(path.startsWith('/admin/'))await requireAdmin(env,user);
  if(path==='/admin/push-config'){
   if(method==='GET')return json(publicConfig(await getNativePushConfig(env),true));
   if(method!=='PATCH')fail(405,'方法不支持');const data=await input(request);fields(data,['enabled','provider','app_key','master_secret','clear_master_secret','client_options']);const raw=await state(env,KEY),old=await getNativePushConfig(env),c=structuredClone(old);
   if('enabled'in data){if(typeof data.enabled!=='boolean')fail(422,'推送开关无效');c.enabled=data.enabled;}
   if('provider'in data){if(!['disabled','jpush'].includes(data.provider))fail(422,'只支持免费基础推送');c.provider=data.provider;}
   if('app_key'in data){c.app_key=text(data.app_key,'AppKey',64,0);if(c.app_key&&!/^[a-fA-F0-9]{24}$/.test(c.app_key))fail(422,'请填写24位极光AppKey');}
   if('clear_master_secret'in data&&typeof data.clear_master_secret!=='boolean')fail(422,'清除密钥开关无效');
   if('master_secret'in data){const value=text(data.master_secret,'服务密钥',256,0);if(value){if(value.length<16||/[^\x21-\x7E]/.test(value))fail(422,'服务密钥无效');c.master_secret=value;}}
   if(data.clear_master_secret){if(data.master_secret?.trim())fail(422,'不能同时清除和设置密钥');c.master_secret='';c.enabled=false;}
   if('client_options'in data)c.client_options=options(data.client_options,c.client_options);
   if(c.enabled&&c.provider==='disabled')fail(422,'启用通知前请选择推送渠道');
   c.revision=old.revision+1;const value=await encrypt(env,c);
   // CAS prevents concurrent admin forms from silently replacing another secret.
   const result=await run(env,`INSERT INTO service_state(state_key,value) SELECT ?,? WHERE (SELECT value FROM service_state WHERE state_key=?) IS ? AND ${adminGuard} ON CONFLICT(state_key) DO UPDATE SET value=excluded.value WHERE service_state.value IS ? AND ${adminGuard}`,KEY,value,KEY,raw,user.id,raw,user.id);
   if(!result.meta.changes){await requireAdmin(env,user);fail(409,'推送配置已经变化，请刷新后重试');}return json(publicConfig(c,true));
  }
  if(path==='/push/native-config'){if(method!=='GET')fail(405,'方法不支持');return json(publicConfig(await getNativePushConfig(env)));}
  if(path==='/admin/push-test'){if(method!=='POST')fail(405,'方法不支持');fields(await input(request),[]);await requireAdmin(env,user);return json(await sendNativePush(env,user.id,{type:'care_test'},{beforeSend:async()=>{await requireAdmin(env,user);return true;}}));}
  const data=await input(request);fields(data,method==='POST'?['provider','registration_id','config_revision']:['registration_id']);const registration=text(data.registration_id,'设备推送标识',250,8);if(!/^[A-Za-z0-9_-]+$/.test(registration))fail(422,'设备推送标识无效');
  if(method==='DELETE'){await run(env,'DELETE FROM native_push_devices WHERE user_id=? AND registration_id=?',user.id,registration);return json({unregistered:true});}
  if(method!=='POST')fail(405,'方法不支持');const c=await getNativePushConfig(env);if(!configured(c))fail(503,'系统通知尚未配置');if(data.provider!=='jpush'||!Number.isSafeInteger(data.config_revision)||data.config_revision!==c.revision)fail(409,'通知配置已变化，请重新开启');
  const existing=await one(env,'SELECT id,user_id FROM native_push_devices WHERE registration_id=?',registration);if(existing?.user_id!==user.id&&(await one(env,'SELECT count(*) n FROM native_push_devices WHERE user_id=?',user.id)).n>=5)fail(409,'每个账号最多绑定5个通知设备');
  await run(env,'INSERT INTO native_push_devices(id,user_id,registration_id,config_revision,data) VALUES(?,?,?,?,?) ON CONFLICT(registration_id) DO UPDATE SET user_id=excluded.user_id,config_revision=excluded.config_revision,data=excluded.data',existing?.id||uuid(),user.id,registration,c.revision,JSON.stringify({platform:'android',created_at:new Date().toISOString()}));return json({registered:true,provider:'jpush',config_revision:c.revision});
 }catch(error){if(error instanceof CareError)return json({detail:error.message},error.status);return json({detail:'通知服务暂时不可用'},503);}
}
