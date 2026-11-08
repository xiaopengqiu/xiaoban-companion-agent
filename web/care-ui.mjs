// Account-scoped care inbox. Passive refresh never creates a model conversation.
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const asItems=value=>Array.isArray(value)?value:[];
const errorText=error=>String(error?.message||'暂时连接不到小伴，可以稍后重试。').slice(0,300);
const date=value=>{const d=new Date(value);return Number.isFinite(+d)?d.toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';};
const newId=()=>globalThis.crypto?.randomUUID?.()||`care-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export class CareInbox {
  constructor({request,onChange=()=>{},requestId=newId}){this.request=request;this.onChange=onChange;this.requestId=requestId;this.token=undefined;this.generation=0;this.reset(null);}
  reset(token){token=token||null;if(this.token===token)return;this.token=token;this.generation++;this.loadRevision=0;this.selection=0;this.detailRevision=0;this.items=[];this.unread_count=0;this.status={};this.preferences=null;this.selectedId=null;this.detail=null;this.loaded=false;this.loading=false;this.error='';this.drafts=new Map();this.requests=new Map();this.busy=new Set();}
  _ticket(){return {token:this.token,generation:this.generation};}
  _current(ticket){return !!ticket.token&&ticket.token===this.token&&ticket.generation===this.generation;}
  _call(path,options,ticket){return this.request(path,{...options,sessionToken:ticket.token});}
  _notify(){this.onChange(this.snapshot());}
  snapshot(){return {items:this.items.map(t=>({...t})),unread_count:this.unread_count,status:{...this.status},preferences:this.preferences?{...this.preferences}:null,selectedId:this.selectedId,detail:this.detail?{...this.detail,thread:{...this.detail.thread},messages:this.detail.messages.map(m=>({...m}))}:null,draft:this.drafts.get(this.selectedId)||'',pendingRequest:this.requests.has(this.selectedId)?{...this.requests.get(this.selectedId)}:null,sending:this.busy.has(this.selectedId),loading:this.loading,loaded:this.loaded,error:this.error};}
  setDraft(value,id=this.selectedId){if(id)this.drafts.set(id,String(value).slice(0,2000));}
  async load({notify=true}={}){
    if(!this.token)return false;const ticket=this._ticket(),revision=++this.loadRevision;
    try{const [list,prefs]=await Promise.all([this._call('/care',{},ticket),this._call('/care/preferences',{},ticket)]);
      if(!this._current(ticket)||revision!==this.loadRevision)return false;
      this.items=asItems(list.items).map(t=>({...t}));this.unread_count=Number.isInteger(list.unread_count)?Math.max(0,list.unread_count):this.items.filter(t=>t.unread).length;
      this.status=list.status&&typeof list.status==='object'?{...list.status}:{};this.preferences={enabled:prefs.enabled===true};this.loaded=true;this.error='';if(notify)this._notify();return true;
    }catch(error){if(!this._current(ticket)||revision!==this.loadRevision)return false;this.error=errorText(error);if(notify)this._notify();return false;}
  }
  _acceptDetail(data,id){
    if(!data||data.thread?.id!==id||!Array.isArray(data.messages))throw Error('暂时无法读取这段关心，请刷新后重试。');
    this.detail={...data,thread:{...data.thread},messages:data.messages.map(m=>({...m}))};
    const previous=this.requests.get(id),receipt=previous&&data.messages.find(m=>m.role==='user'&&m.request_id===previous.id&&m.reply_status==='complete');
    if(receipt){if((this.drafts.get(id)||'').trim()===previous.message)this.drafts.set(id,'');this.requests.delete(id);}
    const pending=[...data.messages].reverse().find(m=>m.role==='user'&&m.request_id&&m.reply_status==='pending');
    if(pending)this.requests.set(id,{id:pending.request_id,message:pending.content,phase:'pending',saved:true});
    const index=this.items.findIndex(t=>t.id===id);if(index>=0)this.items[index]={...this.items[index],...data.thread};
  }
  async open(id){
    if(!this.token||typeof id!=='string'||!id)return false;const ticket=this._ticket(),selection=++this.selection,revision=++this.detailRevision;
    this.selectedId=id;this.detail=null;this.loading=true;this.error='';this._notify();
    try{const data=await this._call('/care/'+encodeURIComponent(id),{},ticket);
      if(!this._current(ticket)||selection!==this.selection||revision!==this.detailRevision||this.selectedId!==id)return false;
      this._acceptDetail(data,id);this.loading=false;this._notify();await this.markRead(id);return true;
    }catch(error){if(!this._current(ticket)||selection!==this.selection)return false;this.loading=false;this.error=errorText(error);this._notify();return false;}
  }
  async refreshSelected(){
    const id=this.selectedId;if(!id||this.busy.has(id)||!this.token)return false;const ticket=this._ticket(),revision=++this.detailRevision,selection=this.selection;
    try{const data=await this._call('/care/'+encodeURIComponent(id),{},ticket);
      if(!this._current(ticket)||revision!==this.detailRevision||selection!==this.selection||id!==this.selectedId)return false;
      this._acceptDetail(data,id);this._notify();await this.markRead(id);return true;
    }catch{return false;}
  }
  async markRead(id=this.selectedId){
    if(!id||!this.token)return false;const ticket=this._ticket();
    try{await this._call('/care/'+encodeURIComponent(id)+'/read',{method:'POST',body:'{}'},ticket);
      if(!this._current(ticket))return false;const row=this.items.find(t=>t.id===id);if(row?.unread){row.unread=false;this.unread_count=Math.max(0,this.unread_count-1);}if(this.detail?.thread.id===id)this.detail.thread.unread=false;this._notify();return true;
    }catch(error){if(this._current(ticket)&&this.selectedId===id){this.error='关心消息已打开，已读状态暂未同步，可稍后刷新。';this._notify();}return false;}
  }
  async setEnabled(enabled,{modelAllowed=false,consentGiven=false}={}){
    if(!this.token)throw Error('请先登录。');if(enabled&&!modelAllowed&&!consentGiven)throw Error('请先明确授权模型处理必要的个人摘要。');
    const ticket=this._ticket();if(enabled&&!modelAllowed)await this._call('/preferences',{method:'PATCH',body:JSON.stringify({model_consent:true})},ticket);
    if(!this._current(ticket))return false;const result=await this._call('/care/preferences',{method:'PATCH',body:JSON.stringify({enabled:!!enabled})},ticket);
    if(!this._current(ticket))return false;this.preferences={enabled:result.enabled===true};this.error='';this._notify();return true;
  }
  async send(){
    const id=this.selectedId;if(!id||this.requests.has(id)||this.busy.has(id)||!this.token)return false;
    const message=(this.drafts.get(id)||'').trim();if(!message||message.length>2000)return false;
    const request={id:this.requestId(),message,phase:'retry',saved:false};this.requests.set(id,request);return this._submit(id,request);
  }
  async retry(){const id=this.selectedId,request=this.requests.get(id);if(!id||!request||this.busy.has(id)||!this.token)return false;return this._submit(id,request);}
  async _submit(id,request){
    const ticket=this._ticket();this.busy.add(id);this.detailRevision++;this.error='';this._notify();
    try{const data=await this._call('/care/'+encodeURIComponent(id)+'/reply',{method:'POST',timeoutMs:65000,body:JSON.stringify({message:request.message,request_id:request.id})},ticket);
      if(!this._current(ticket))return false;
      if(!data||data.thread?.id!==id||!Array.isArray(data.messages)||!['pending','complete'].includes(data.reply_status))throw Error('回复状态尚未确认，可以稍后重试。');
      const pending=data.reply_status==='pending';
      if(pending)this.requests.set(id,{...request,phase:'pending',saved:true});else this.requests.delete(id);
      if((this.drafts.get(id)||'').trim()===request.message)this.drafts.set(id,'');
      if(this.selectedId===id){this._acceptDetail(data,id);if(pending)this.requests.set(id,{...request,phase:'pending',saved:true});}
      this.error='';return true;
    }catch(error){if(!this._current(ticket))return false;this.requests.set(id,{...request,phase:request.saved?'pending':'retry'});if(this.selectedId===id)this.error=errorText(error);return false;
    }finally{if(this._current(ticket)){this.busy.delete(id);this._notify();}}
  }
}

export function careConfigPayload(values){
  const result={enabled:!!values.enabled};for(const [name,min,max] of [['analysis_interval_minutes',15,1440],['cooldown_minutes',15,10080],['daily_limit',1,5]]){const n=Number(values[name]);if(!Number.isInteger(n)||n<min||n>max)throw Error(name==='analysis_interval_minutes'?'分析间隔应为 15–1440 分钟。':name==='cooldown_minutes'?'发信间隔应为 15–10080 分钟。':'每天最多关心应为 1–5 条。');result[name]=n;}return result;
}
export const nativeClientFields={firebase:{api_key:'Firebase API Key',app_id:'Firebase App ID',messaging_sender_id:'Messaging Sender ID',project_id:'Firebase Project ID'},manufacturers:{huawei_app_id:'华为 App ID',xiaomi_app_id:'小米 App ID',xiaomi_app_key:'小米 App Key',oppo_app_key:'OPPO App Key',oppo_app_secret:'OPPO App Secret',vivo_app_id:'vivo App ID',vivo_app_key:'vivo App Key',honor_app_id:'荣耀 App ID'}};
export function pushConfigPayload(values){
  const provider=values.provider||'disabled';if(!['disabled','jpush'].includes(provider))throw Error('请选择受支持的免费推送方式。');
  const result={enabled:!!values.enabled,provider,app_key:String(values.app_key||'').trim(),client_options:{}};
  const options=values.client_options||{};if(Object.keys(options).some(k=>!Object.hasOwn(nativeClientFields,k)))throw Error('客户端参数无效。');
  for(const [group,fields] of Object.entries(nativeClientFields)){const incoming=options[group]||{};if(typeof incoming!=='object'||Array.isArray(incoming)||Object.keys(incoming).some(k=>!Object.hasOwn(fields,k)))throw Error('客户端参数无效。');result.client_options[group]={};for(const field of Object.keys(fields)){if(incoming[field]!==undefined&&typeof incoming[field]!=='string')throw Error('客户端参数无效。');const value=String(incoming[field]||'').trim();if(value.length>1000)throw Error('客户端参数过长。');result.client_options[group][field]=value;}}
  const secret=String(values.master_secret||'').trim();if(secret&&values.clear_master_secret)throw Error('请分别选择更新或清除密钥。');if(secret)result.master_secret=secret;if(values.clear_master_secret)result.clear_master_secret=true;return result;
}

export function careStatusText(view={}){
  const code=view.status?.reason_code;
  if(code==='model_unconfigured'||code==='model_missing')return '主动关心服务暂未就绪。你仍可查看已有消息，或主动和小伴聊聊。';
  if(code==='model_consent_required')return '需要你明确允许模型处理必要摘要，才会进行主动关心。';
  if(code==='quiet_hours')return '现在处于免打扰时段，小伴会安静等候。';
  if(code==='disabled')return '主动关心服务尚未开启，已有消息仍会保留。';
  if(code==='user_disabled'||view.preferences?.enabled===false)return '主动关心目前关闭。开启后，小伴只在有必要时联系你。';
  if(code==='error'||code==='model_error')return '本次分析暂未完成，小伴不会因此额外发消息。';
  return '小伴会按设置分析近期情况，没有需要注意的事情就安静陪着。';
}
export function renderCareList(view){return view.items?.length?view.items.map(t=>`<button class="care-thread-card ${view.selectedId===t.id?'selected':''}" type="button" data-care-open="${escape(t.id)}" aria-pressed="${view.selectedId===t.id}"><div><strong>${escape(t.title||'小伴的关心')}</strong>${t.unread?'<span class="care-unread-dot" aria-label="未读"></span>':''}</div><p>${escape(t.preview||'点开接着聊聊')}</p><small>${escape(date(t.updated_at||t.created_at))} · ${Number(t.message_count)||1} 条消息</small></button>`).join(''):'<div class="empty care-empty"><span aria-hidden="true">✳</span><h3>暂时没有新的关心</h3><p>没有需要注意的事情时，小伴不会额外发消息。你也随时可以主动聊聊。</p></div>';}
export function renderCareMessages(view){return (view.detail?.messages||[]).map(m=>`<article class="care-message ${m.role==='user'?'user':'assistant'}"><span>${m.role==='user'?'你':'小伴'}</span><p>${escape(m.content)}</p><small>${escape(date(m.created_at))}${m.role==='user'&&m.reply_status==='pending'?' · 回复已保存，等待小伴回应':''}</small></article>`).join('');}
export function renderCareThread(view){
  if(!view.selectedId)return '<div class="care-thread-placeholder"><span aria-hidden="true">✳</span><h3>有些事情，值得接着聊。</h3><p>选择一条关心，就能看到小伴为什么来问候，也可以说说后来的变化。</p></div>';
  if(view.loading&&!view.detail)return '<div class="empty" role="status">正在打开这段关心…</div>';
  if(!view.detail)return '<div class="empty">暂时未能打开这段关心。<br>可以点选消息重试。</div>';
  const pending=view.pendingRequest;
  return `<div class="care-conversation-heading"><div><span class="eyebrow">接着说，也有人听</span><h2 id="care-thread-title">${escape(view.detail.thread.title||'小伴的关心')}</h2></div><span class="pill neutral">AI 主动关心</span></div><div class="care-message-list" id="care-messages" aria-live="polite">${renderCareMessages(view)}</div><div id="care-reply-status" class="care-reply-status" role="status">${view.sending?'小伴正在认真看你的回复…':pending?.saved?'你的回复已保存，小伴暂未完成回应。可以稍后重试。':pending?'上一条回复尚未确认。重试会使用同一个编号，不会重复发送。':''}</div><form id="care-reply-form"><label for="care-reply-input">想接着说点什么？</label><textarea id="care-reply-input" rows="3" maxlength="2000" placeholder="说说后来的变化，或此刻的感受…">${escape(view.draft)}</textarea><div class="care-compose-actions"><span>这段对话只属于你的账号。</span><button class="btn secondary small" type="button" id="care-reply-retry" ${pending?'':'hidden'} ${view.sending?'disabled':''}>重试回应</button><button class="btn primary small" id="care-reply-send" type="submit" ${view.sending||pending||!view.draft?.trim()?'disabled':''}>发送回复</button></div></form>`;
}
export function renderCareConsent(view,prefs={}){
  const enabled=view.preferences?.enabled===true;
  return `<section class="panel care-preferences"><div><h2>关心的分寸，由你决定。</h2><p>允许小伴把必要的近期记录、记忆和在意的事交给已配置的模型，判断是否值得主动问候。</p></div><form id="care-preferences-form"><label class="check"><input name="care_enabled" type="checkbox" ${enabled?'checked':''} ${view.preferences?'':'disabled'}><span>允许 AI 主动关心我<br><small>没有需要注意的事情就不发消息，并遵守免打扰和每天上限。</small></span></label>${prefs.model_consent?'':'<label class="check"><input type="checkbox" name="care_model_consent"><span>我授权模型处理必要的个人摘要，用于聊天与主动关心。<br><small>关闭主动关心后，已有的聊天模型授权不会被一并撤销。</small></span></label>'}<p class="error" id="care-preferences-error" role="alert"></p><button type="submit" class="btn secondary small" ${view.preferences?'':'disabled'}>保存主动关心偏好</button></form><p class="subtle-note" id="care-readiness">${escape(careStatusText(view))}</p>${view.status?.next_check_at?`<p class="subtle-note">下次计划分析：${escape(date(view.status.next_check_at))}。实际检查需后台服务可用。</p>`:''}</section>`;
}
export function renderCareInbox(view,prefs={}){return `<div id="care-root"><section class="intro-strip">主动关心是一段可以回复的对话。小伴会先判断有没有值得关心的事，再决定是否联系你。</section>${renderCareConsent(view,prefs)}<section class="care-inbox panel"><div class="care-list-column"><div class="panel-head"><h2>关心消息 <span class="count-note" id="care-unread-count">${Math.max(0,Number(view.unread_count)||0)} 条未读</span></h2><button type="button" class="text-link" id="care-refresh">刷新</button></div><div id="care-list">${renderCareList(view)}</div></div><div class="care-conversation" id="care-thread" data-care-thread="${escape(view.selectedId||'')}">${renderCareThread(view)}</div></section><p class="error" id="care-error" role="alert">${escape(view.error||'')}</p></div>`;}

export function renderCareAdmin(user,care,push){
  if(user?.is_admin!==true)return '';
  const careForm=care?`<form id="admin-care-form"><label class="check"><input type="checkbox" name="enabled" ${care.enabled?'checked':''}><span>启用 AI 主动关心分析</span></label><div class="form-row"><div class="form-field"><label for="care-analysis-interval">每隔多少分钟分析一次</label><input id="care-analysis-interval" type="number" name="analysis_interval_minutes" min="${escape(care.min_analysis_interval_minutes??15)}" max="1440" step="${escape(care.scheduler_interval_minutes||1)}" value="${escape(care.analysis_interval_minutes??120)}" required></div><div class="form-field"><label for="care-send-cooldown">两次主动发信至少间隔多少分钟</label><input id="care-send-cooldown" type="number" name="cooldown_minutes" min="15" max="10080" step="1" value="${escape(care.cooldown_minutes??240)}" required></div></div><div class="form-field"><label for="care-max-daily">每个账号每天最多关心</label><input id="care-max-daily" type="number" name="daily_limit" min="1" max="5" step="1" value="${escape(care.daily_limit??2)}" required></div><p class="subtle-note">每次分析不等于每次发信。AI 判断无需注意、模型不可用或不满足免打扰等条件时，不生成关心消息。</p>${care.scheduler_interval_minutes?`<p class="subtle-note">当前云端约每 ${escape(care.scheduler_interval_minutes)} 分钟运行一轮，请按该周期的整数倍设置分析间隔。账号分批处理，实际检查可能延迟。</p>`:''}<div class="care-service-health"><span class="pill ${care.model_ready?'':'neutral'}">${care.model_ready?'模型已就绪':'模型尚未就绪'}</span><span class="pill ${care.scheduler_configured?'':'neutral'}">${care.scheduler_configured?'后台定时已配置':'后台定时尚未启用'}</span></div>${care.last_scheduler_run_at?`<p class="subtle-note">最近后台检查：${escape(date(care.last_scheduler_run_at))}</p>`:''}<p class="error" id="admin-care-error" role="alert"></p><p class="admin-result" id="admin-care-result" role="status"></p><div class="integration-actions"><button class="btn primary small" type="submit">保存分析频率</button><button class="btn secondary small" id="admin-care-check" type="button">立即检查我的账号</button></div><p class="subtle-note">立即检查只针对当前管理员账号，仍需个人授权，并遵守免打扰、去重和发信上限。</p></form>`:'<p class="error">暂时无法读取主动关心设置，请刷新后再试。</p>';
  const pushForm=push?`<form id="admin-native-push-form"><label class="check"><input type="checkbox" name="enabled" ${push.enabled?'checked':''}><span>启用安卓应用外通知</span></label><div class="form-field"><label for="native-push-provider">推送方式</label><select id="native-push-provider" name="provider"><option value="disabled" ${push.provider!=='jpush'?'selected':''}>暂不开启</option><option value="jpush" ${push.provider==='jpush'?'selected':''}>极光基础推送（免费版）</option></select></div><div class="form-row"><div class="form-field"><label for="native-push-appkey">极光 AppKey</label><input id="native-push-appkey" name="app_key" value="${escape(push.app_key||'')}" maxlength="128" autocomplete="off"></div><div class="form-field"><label for="native-push-secret">服务器 MasterSecret</label><input id="native-push-secret" name="master_secret" type="password" maxlength="8192" autocomplete="new-password" placeholder="${push.master_secret_configured?'已保存，留空保留':'尚未配置'}"></div></div><label class="check"><input type="checkbox" name="clear_master_secret"><span>清除已保存的 MasterSecret（同时关闭系统推送）</span></label><details class="care-sdk-options"><summary>手机 SDK 与厂商通道参数</summary><p>这些是手机 SDK 所需的客户端参数，会提供给已登录的客户端。服务器 MasterSecret 不会提供给手机。极光 AppKey 和服务器密钥可保存后生效。华为、vivo、荣耀的客户端参数更改后需要重新打包；各厂商正式通道还需在平台开通。免费版资源和机型支持以平台实际状态为准。</p>${Object.entries(nativeClientFields).map(([group,fields])=>`<fieldset><legend>${group==='firebase'?'Google / Firebase 客户端配置':'国内厂商客户端配置'}</legend><div class="form-row">${Object.entries(fields).map(([key,label])=>`<div class="form-field"><label for="sdk-${group}-${key}">${label}</label><input id="sdk-${group}-${key}" name="sdk_${group}_${key}" value="${escape(push.client_options?.[group]?.[key]||'')}" maxlength="1000" autocomplete="off"></div>`).join('')}</div></fieldset>`).join('')}</details><p class="subtle-note">应用内关心消息始终独立保存。推送未配置、权限未允许或渠道不可用时，仍可在消息中心查看。</p><p class="error" id="admin-native-push-error" role="alert"></p><p class="admin-result" id="admin-native-push-result" role="status"></p><div class="integration-actions"><button class="btn primary small" type="submit">保存免费推送配置</button><button id="admin-native-push-test" class="btn secondary small" type="button">给我的已注册手机发测试通知</button></div><p class="subtle-note">测试通知只发送给当前管理员账号已注册的设备，不广播给其他用户。</p></form>`:'<p class="error">暂时无法读取安卓通知配置，请刷新后再试。</p>';
  return `<section class="panel settings-card admin-card"><div class="panel-head"><h2>AI 主动关心</h2><span class="pill amber">仅管理员</span></div>${careForm}</section><section class="panel settings-card admin-card"><div class="panel-head"><h2>安卓系统通知配置</h2><span class="pill amber">仅管理员</span></div>${pushForm}</section>`;
}
