// Server-only Open Wearables 0.11.0 and FCM transports. No Node runtime APIs.
const OW_COMMIT='183e8cc4f6de897dbd288c93d49a691f2cf721ae';
export class IntegrationError extends Error {}
const problem=message=>{throw new IntegrationError(message);};
function instant(value){const date=new Date(value);if(!value||!Number.isFinite(+date)||(typeof value==='string'&&!/(Z|[+-]\d\d:\d\d)$/.test(value)))problem('穿戴时间必须含时区');return date;}
function numeric(value){if(value===null||value===undefined)return null;if(typeof value!=='number'||!Number.isFinite(value)||value<0)problem('穿戴数值格式无效');return value;}
function provenance(row){const source=row.source;if(!source||typeof source!=='object'||!source.provider)problem('穿戴记录缺少来源');return {provider:source.provider,writer:source.source,device:source.device,device_id:source.device_id,zone_offset:row.zone_offset,upstream_version:'0.11.0',upstream_commit:OW_COMMIT};}
function windowOf(row){const start=instant(row.start_time),end=instant(row.end_time);if(+end<=+start)problem('穿戴记录时间顺序无效');return {start,end,seconds:(end-start)/1000};}
function localDay(now,zone){return new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).format(now);}
function dayValid(day){if(!/^\d{4}-\d\d-\d\d$/.test(day))return false;const date=new Date(day+'T00:00:00Z');return Number.isFinite(+date)&&date.toISOString().slice(0,10)===day;}
function normalize(kind,row,now,zone){
  const meta=provenance(row),source='open-wearables';
  if(kind==='activity'){
    const value=numeric(row.steps);if(value===null)return [];
    if(!dayValid(row.date))problem('穿戴日汇总日期无效');
    return [{kind:'steps',value,unit:'steps',start_at:row.date+'T00:00:00.000Z',source,external_id:'ow:steps:'+row.date,metadata:{...meta,granularity:'daily_summary',summary_date:row.date,day_boundary:'upstream_summary_date',timestamp_is_anchor:true,coverage_unknown:true,is_complete_day:row.date<localDay(now,zone),note:'来源平台日汇总；不是实时步数流'}}];
  }
  if(kind==='sleep'||kind==='workouts'){
    if(!row.id)problem('穿戴事件缺少编号');const bounds=windowOf(row);
    let seconds=numeric(kind==='sleep'?row.sleep_duration_seconds:row.duration_seconds);
    let measure='time_asleep';
    if(seconds===null){seconds=numeric(row.duration_seconds);measure='session_duration';}
    if(seconds===null&&kind==='workouts')seconds=bounds.seconds;
    if(seconds===null)return [];
    if(seconds>bounds.seconds+1)problem('穿戴时长超出记录窗口');
    const sleep=kind==='sleep';
    return [{kind:sleep?'sleep':'workout',value:Number((seconds/(sleep?3600:60)).toFixed(4)),unit:sleep?'hours':'minutes',start_at:bounds.start.toISOString(),end_at:bounds.end.toISOString(),source,external_id:`ow:${sleep?'sleep':'workout'}:${row.id}`,metadata:{...meta,granularity:'session',upstream_id:row.id,...(sleep?{duration_measure:measure,is_nap:row.is_nap===true}:{workout_type:row.type,note:row.name||row.type||'运动'})}}];
  }
  if(kind==='meals'){
    if(!row.id)problem('饮食记录缺少编号');const start_at=instant(row.timestamp).toISOString();
    const calories=numeric(row.calories_kcal),water=numeric(row.water_ml),result=[];
    const metadata={...meta,granularity:'entry',upstream_id:row.id,note:row.name||'饮食记录',meal_type:row.meal_type,macros:row.macros||{}};
    if(calories!==null)result.push({kind:'meal',value:calories,unit:'kcal',start_at,source,external_id:'ow:meal:'+row.id,metadata});
    if(water!==null)result.push({kind:'water',value:water,unit:'ml',start_at,source,external_id:'ow:water:'+row.id,metadata});
    return result;
  }
  return [];
}

export class OWClient {
  constructor(env,fetcher=globalThis.fetch){
    this.env=env;this.fetcher=fetcher;
    this.baseUrl=(env.OPEN_WEARABLES_URL||env.OPEN_WEARABLES_BASE_URL||'').replace(/\/$/,'').replace(/\/api\/v1$/,'');
    this.configured=!!(this.baseUrl&&env.OPEN_WEARABLES_API_KEY);
    this.sdkConfigured=this.configured&&!!(env.OPEN_WEARABLES_APP_ID&&env.OPEN_WEARABLES_APP_SECRET);
    this.publicUrl=env.OPEN_WEARABLES_PUBLIC_URL||this.baseUrl;
    if(this.baseUrl){let url;try{url=new URL(this.baseUrl);}catch{problem('穿戴服务地址配置无效');}
      if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||url.pathname!=='/')problem('云端穿戴服务必须配置 HTTPS 主机地址');}
    if(this.publicUrl){let url;try{url=new URL(this.publicUrl);}catch{problem('手机穿戴服务地址配置无效');}
      if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||url.pathname!=='/')problem('手机穿戴服务必须配置 HTTPS 主机地址');}
  }
  uuid(value){if(typeof value!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value))problem('绑定的穿戴用户编号无效');return value;}
  async request(method,path,body,params,allow404=false){
    if(!this.configured)problem('云端尚未配置穿戴服务');
    const url=new URL(this.baseUrl+'/api/v1'+path);if(params)for(const [key,value] of Object.entries(params))url.searchParams.set(key,value);
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
    try{
      let response;try{response=await this.fetcher(url.toString(),{method,headers:{'X-Open-Wearables-API-Key':this.env.OPEN_WEARABLES_API_KEY,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:controller.signal,redirect:'manual'});}catch{problem('穿戴服务连接失败或超时');}
      if(response.status===404&&allow404)return {deleted:true};
      if(!response.ok)problem('穿戴服务返回 HTTP '+response.status);
      if(response.status===204)return {deleted:true};
      let data;try{data=await response.json();}catch{problem('穿戴服务响应格式无效');}
      if(!data||typeof data!=='object'||Array.isArray(data))problem('穿戴服务响应格式无效');return data;
    }finally{clearTimeout(timeout);}
  }
  async createUser(user){const value=await this.request('POST','/users',{external_user_id:user.id,first_name:String(user.name).slice(0,100),email:user.email});this.uuid(value.id);return value;}
  async issueSdkTokens(userId){if(!this.sdkConfigured)problem('请先配置服务端穿戴应用凭证');const value=await this.request('POST',`/users/${this.uuid(userId)}/token`,{app_id:this.env.OPEN_WEARABLES_APP_ID,app_secret:this.env.OPEN_WEARABLES_APP_SECRET});if(!value.access_token||!value.refresh_token)problem('穿戴服务未返回用户范围令牌');return {access_token:value.access_token,refresh_token:value.refresh_token,token_type:value.token_type||'bearer',expires_in:value.expires_in};}
  async deleteUser(userId){return this.request('DELETE',`/users/${this.uuid(userId)}`,undefined,undefined,true);}
  async pages(path,params){const rows=[],seen=new Set();for(let page=0;page<20;page++){
    const value=await this.request('GET',path,undefined,params);
    if(!Array.isArray(value.data)||!value.pagination)problem('穿戴服务分页格式已变化');rows.push(...value.data);
    if(!value.pagination.has_more)return rows;const next=value.pagination.next_cursor;
    if(!next||seen.has(next))problem('穿戴服务游标重复或无效');seen.add(next);params={...params,cursor:next};
  }problem('穿戴服务分页过多，请缩短同步范围');}
  async fetchEvents(userId,start,end,now=new Date(),zone='Asia/Shanghai'){
    this.uuid(userId);const from=instant(start),until=instant(end);now=instant(now);if(+from>=+until)problem('同步时间范围无效');
    const params={start_date:from.toISOString(),end_date:until.toISOString(),limit:100};
    const result=[];
    for(const [kind,suffix] of [['activity','summaries/activity'],['sleep','events/sleep'],['workouts','events/workouts'],['meals','events/meals']]){
      const extra={};if(kind==='activity'){const ceiling=new Date(until);ceiling.setUTCHours(0,0,0,0);if(+ceiling<+until)ceiling.setUTCDate(ceiling.getUTCDate()+1);extra.end_date=ceiling.toISOString();}
      if(kind==='sleep')extra.filter_by_priority='true';
      for(const row of await this.pages(`/users/${userId}/${suffix}`,{...params,...extra})){try{result.push(...normalize(kind,row,now,zone));}catch(error){if(error instanceof IntegrationError)throw error;problem('穿戴事件格式无效');}}
    }
    return result;
  }
}

function decode64(value){return Uint8Array.from(atob(value),character=>character.charCodeAt(0));}
function encoded(value){return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');}
export async function verifyOWWebhook(payload,headers,secret,now=new Date()){
  if(!secret)problem('穿戴 Webhook 签名尚未配置');
  if(!(payload instanceof Uint8Array))problem('签名必须校验原始字节');
  const get=name=>headers instanceof Headers?headers.get(name):headers[name];
  const messageId=get('svix-id'),stamp=get('svix-timestamp'),signature=get('svix-signature');
  if(!messageId||!/^\d+$/.test(stamp||'')||!signature)problem('Webhook 签名字段无效');
  if(Math.abs(Math.floor(+now/1000)-Number(stamp))>300)problem('Webhook 签名已过期');
  try{
    const key=await crypto.subtle.importKey('raw',decode64(secret.replace(/^whsec_/,'')),{name:'HMAC',hash:'SHA-256'},false,['verify']);
    const prefix=new TextEncoder().encode(messageId+'.'+stamp+'.'),content=new Uint8Array(prefix.length+payload.length);content.set(prefix);content.set(payload,prefix.length);
    let valid=false;for(const candidate of signature.split(/\s+/)){const [version,value]=candidate.split(',');if(version==='v1'&&value&&await crypto.subtle.verify('HMAC',key,decode64(value),content)){valid=true;break;}}
    if(!valid)problem('Webhook 签名无效');const data=JSON.parse(new TextDecoder().decode(payload));if(!data||typeof data!=='object'||Array.isArray(data))problem('Webhook 格式无效');return data;
  }catch(error){if(error instanceof IntegrationError)throw error;problem('Webhook 签名或格式无效');}
}

export async function sendFCM(env,deviceTokens,reminderId,fetcher=globalThis.fetch){
  const configured=!!env.FCM_SERVICE_ACCOUNT_JSON,result={configured,sent:0,failed:0,sent_tokens:[],failed_tokens:[]};
  const tokens=[...new Set(deviceTokens.filter(value=>typeof value==='string'&&value))];
  if(!tokens.length)return {...result,status:'no_devices'};if(!configured)return {...result,status:'not_configured'};
  let account,oauth;
  try{
    account=JSON.parse(env.FCM_SERVICE_ACCOUNT_JSON);if(!account.client_email||!account.private_key||!account.project_id)throw new Error('Invalid account');
    const key=await crypto.subtle.importKey('pkcs8',decode64(account.private_key.replace(/-----[^-]+-----/g,'').replace(/\s/g,'')),{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['sign']);
    const encoder=new TextEncoder(),iat=Math.floor(Date.now()/1000);
    const head=encoded(encoder.encode(JSON.stringify({alg:'RS256',typ:'JWT'}))),body=encoded(encoder.encode(JSON.stringify({iss:account.client_email,scope:'https://www.googleapis.com/auth/firebase.messaging',aud:'https://oauth2.googleapis.com/token',iat,exp:iat+3600})));
    const unsigned=head+'.'+body,signature=encoded(await crypto.subtle.sign('RSASSA-PKCS1-v1_5',key,encoder.encode(unsigned)));
    const response=await fetcher('https://oauth2.googleapis.com/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'urn:ietf:params:oauth:grant-type:jwt-bearer',assertion:unsigned+'.'+signature}).toString(),signal:AbortSignal.timeout(20000),redirect:'manual'});
    if(!response.ok)throw new Error('OAuth rejected');oauth=await response.json();if(!oauth.access_token)throw new Error('No token');
  }catch{return {...result,failed:tokens.length,failed_tokens:tokens,status:'credentials_error'};}
  for(const token of tokens){try{
    const response=await fetcher(`https://fcm.googleapis.com/v1/projects/${encodeURIComponent(account.project_id)}/messages:send`,{method:'POST',headers:{Authorization:'Bearer '+oauth.access_token,'Content-Type':'application/json'},body:JSON.stringify({message:{token,notification:{title:'小伴有一条新提醒',body:'打开小伴，查看今天的陪伴消息。'},data:{reminder_id:String(reminderId)}}}),signal:AbortSignal.timeout(20000),redirect:'manual'});
    if(response.ok&&(await response.json()).name){result.sent++;result.sent_tokens.push(token);}else{result.failed++;result.failed_tokens.push(token);}
  }catch{result.failed++;result.failed_tokens.push(token);}}
  return {...result,status:result.sent&&!result.failed?'queued':result.sent?'partial':'failed'};
}
