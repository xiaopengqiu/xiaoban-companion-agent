'use strict';
import {requestChat} from './chat-stream.mjs';
import {SessionGuard, ReminderTracker} from './lifecycle.mjs';
import {getBackgroundPushState,enableBackgroundPush,stopBackgroundPush,testBackgroundPush} from './push-client.mjs';
import {memoryGroups, threadPayload, dueThreads, DueCheckGate, localInput, modelConfigPayload} from './companion-ui.mjs';
import {CareInbox,renderCareInbox,renderCareConsent,renderCareAdmin,renderCareList,renderCareThread,renderCareMessages,careStatusText,careConfigPayload,pushConfigPayload,nativeClientFields} from './care-ui.mjs';
const $ = (s, root = document) => root.querySelector(s);
const app = $('#app');
const cloudRuntime = $('meta[name="companion-runtime"]')?.content === 'cloud';
const state = {token: localStorage.getItem('companion_token'), page: 'today', dashboard: null, memories: [], events: [], reminders: [], messages: [], preferences: null, integrations: null, threads: [], suggestions: [], chatDraft: '', lastReplyMode: null, adminConfig: null, adminCareConfig:null, adminPushConfig:null, adminUsers: [], adminSearch: '', adminUsersLoaded: false, sending: false, chatStatus:'', syncWarning:false};
const sessionGuard = new SessionGuard(state.token);
const reminderTracker = new ReminderTracker();
const dueGate = new DueCheckGate();
const careInbox = new CareInbox({request:api,onChange:updateCareSurface});
const careRendered = new WeakMap();
let polling = false;
let pushCleanup=Promise.resolve();
let browserPushState={enabled:false,supported:false};
const activeRequests = new Set();
let authRevision = 0;
let chatRequest=null;
let chatRevision=0;
const paths = {
  home: '<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  memory: '<path d="M9 4a4 4 0 0 0-6 5 4 4 0 0 0 0 7 4 4 0 0 0 6 4V4Zm6 0a4 4 0 0 1 6 5 4 4 0 0 1 0 7 4 4 0 0 1-6 4V4ZM6 9l3 1m-3 6 3-2m9-5-3 1m3 6-3-2"/>',
  settings: '<path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3" fill="currentColor" stroke="none"/><circle cx="16" cy="17" r="3" fill="currentColor" stroke="none"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  moon: '<path d="M20 14a9 9 0 0 1-10-11A9 9 0 1 0 20 14Z"/>',
  steps: '<path d="M6 3c3 0 5 3 4 6s-4 3-5 1S4 3 6 3Zm12 7c-3 0-5 3-4 6s4 3 5 1 1-7-1-7ZM6 15c-2 0-2 4 0 4s3-4 0-4Zm12 6h-3"/>',
  activity: '<path d="m2 12 4 0 3-7 5 14 3-7h5"/>',
  water: '<path d="M12 3S5 11 5 15a7 7 0 0 0 14 0c0-4-7-12-7-12Z"/>',
  meal: '<path d="M4 3v7c0 3 6 3 6 0V3M7 3v18m12 0V3c-5 3-5 10 0 10"/>',
  mood: '<circle cx="12" cy="12" r="9"/><path d="M8 14s1 3 4 3 4-3 4-3M8 9h1m6 0h1"/>',
  send: '<path d="m3 3 18 9-18 9 4-9-4-9Zm4 9h14"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  bell: '<path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5l-2 3ZM10 21h4"/>',
  device: '<rect x="7" y="6" width="10" height="12" rx="3"/><path d="M9 6V2h6v4M9 18v4h6v-4m-5-6h4"/>',
  logout: '<path d="M9 4H4v16h5m6-13 5 5-5 5M8 12h12"/>',
  edit: '<path d="m15 4 5 5M4 20l5-1L20 8c2-2-2-6-4-4L5 15l-1 5Z"/>',
  trash: '<path d="M4 6h16M9 3h6M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
};
const icon = name => `<span class="icon" aria-hidden="true"><svg viewBox="0 0 24 24">${paths[name] || paths.activity}</svg></span>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {steps:'步数', sleep:'睡眠', workout:'运动', meal:'饮食', water:'饮水', mood:'心情', preference:'偏好', goal:'目标', fact:'生活记录', pattern:'行为模式', activity:'活动', context:'近期处境', relationship:'重要关系', boundary:'陪伴边界', life:'重要事情'};
const eventIcon = {sleep:'moon', workout:'activity', steps:'steps', water:'water', meal:'meal', mood:'mood'};
function eventUnit(event){const unit=event.unit||'';return ({h:'小时',hour:'小时',hours:'小时',min:'分钟',minute:'分钟',minutes:'分钟',s:'秒',seconds:'秒',steps:'步',count:event.kind==='steps'?'步':'次',score:'分',rating:'分',entry:'次',meal:'份',kcal:'千卡',ml:'ml',l:'升'})[unit]||unit;}
function eventSource(event){return event.source==='manual'?'手动记录':event.source==='synthetic_demo'?'模拟数据':event.source==='open-wearables'?'设备同步':event.source;}
const statusLabel = {pending:'待提醒',sent:'已提醒',done:'已完成',dismissed:'已跳过',snoozed:'稍后提醒',cancelled:'已取消',expired:'已过期',disabled:'已关闭'};
const deliveryLabel = {inbox:'已存入消息箱',pending:'等待发送',queued:'已提交推送',sent:'已提交推送',failed:'推送未送达',not_configured:'仅消息箱',skipped:'未发送',acknowledged:'已收到反馈',scheduled:'稍后再提醒',cancelled:'已撤销',expired:'已过期',dismissed:'已跳过',sending:'正在提交推送',partial:'部分推送已提交'};
const ruleModeLabel=cloudRuntime?'规则陪伴模式':'本地陪伴模式';
const ruleReplyLabel=cloudRuntime?'规则模式 · 使用已记录事实与规则生成回复':'本地模式 · 使用已记录事实与规则生成回复';
const brand = () => '<div class="brand"><span class="leaf" aria-hidden="true">✳</span><div><strong>小伴</strong><small>YOUR EVERYDAY COMPANION</small></div></div>';
let toastTimer;
function toast(message, bad=false) { if(state.syncWarning&&!bad){message='本地操作已完成；外部语义索引同步待重试。';bad=true;}const el=$('#toast'); el.textContent=message; el.className=`toast show${bad?' bad':''}`; clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove('show'),4500); }
function friendlyError(error) { return error?.message || '暂时无法完成，请稍后再试。'; }
async function api(path, options={}) {
  const requestToken = options.sessionToken ?? state.token;
  const {sessionToken, timeoutMs=path==='/chat'||path.includes('/sync')?60000:15000, signal:callerSignal, ...fetchOptions} = options;
  const headers = {'Content-Type':'application/json', ...(requestToken?{Authorization:`Bearer ${requestToken}`}:{})};
  const controller = new AbortController();
  activeRequests.add(controller);
  let timedOut=false;
  const cancel=()=>controller.abort();
  if(callerSignal){if(callerSignal.aborted)cancel();else callerSignal.addEventListener('abort',cancel,{once:true});}
  const timer=setTimeout(()=>{timedOut=true;controller.abort();},Math.min(90000,Math.max(1,timeoutMs)));
  try {
    let response;
    try { response=await fetch(`/api${path}`, {...fetchOptions,credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{...headers,...fetchOptions.headers}}); }
    catch { throw new Error(timedOut?'请求超时，请稍后重试。':controller.signal.aborted?'请求已取消。':'连接不到小伴服务，请检查网络后重试。'); }
    let data; try { data=await response.json(); } catch { if(timedOut)throw new Error('请求超时，请稍后重试。');data={}; }
    if (!response.ok) {
      if (response.status===401 && !path.startsWith('/auth/') && state.token===requestToken) logout({revoke:false});
      // Auth failures never display arbitrary server diagnostics or echoed input.
      if(path==='/auth/login'&&response.status===401)throw new Error('邮箱或密码不正确。');
      if(path.startsWith('/auth/')&&response.status===422)throw new Error('登录信息格式有误，请检查邮箱和密码。');
      if(response.status===429)throw new Error('尝试次数较多，请稍后再试。');
      const detail=Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('；'):data.detail;
      const error=new Error(typeof detail==='string'?detail:`请求未完成（${response.status}）`);error.status=response.status;throw error;
    }
    if(requestToken===state.token&&data.memory_sync==='failed')state.syncWarning=true;
    if(requestToken===state.token&&['synced','cleared'].includes(data.memory_sync))state.syncWarning=false;
    return data;
  } finally {
    clearTimeout(timer);activeRequests.delete(controller);
    callerSignal?.removeEventListener('abort',cancel);
  }
}
const post=(path,body={})=>api(path,{method:'POST',body:JSON.stringify(body)});
const patch=(path,body)=>api(path,{method:'PATCH',body:JSON.stringify(body)});
function localDate(value, options={}) { const date=new Date(value); if(Number.isNaN(date.getTime()))return '时间未知'; return new Intl.DateTimeFormat('zh-CN',{timeZone:state.dashboard?.user?.timezone||'Asia/Shanghai',...options}).format(date); }
function number(value, digits=0){return value===null||value===undefined?'—':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:digits});}
function items(result){return Array.isArray(result)?result:(result?.items||[]);}
function errorTarget(form,error){const target=$('.error',form); if(target)target.textContent=friendlyError(error);else toast(friendlyError(error),true);}
function renderAuth(mode='login') {
  authRevision+=1;
  const register=mode==='register';
  app.innerHTML=`<main class="welcome"><section class="welcome-story"><div>${brand()}<h1>让每一天，<br>都被好好记得。</h1><p>记住你的习惯，理解你的日常。<br>在合适的时候，陪你向前一小步。</p></div><div class="story-note">${icon('shield')}<span>你决定记住什么，也能随时更正或删除。</span></div></section><section class="auth"><h2>${register?'从认识你开始':'欢迎回来'}</h2><p>${register?'告诉小伴怎么称呼你，慢慢建立属于你的陪伴。':'给自己一点时间，看看今天过得怎么样。'}</p><p class="demo-note">${cloudRuntime?'云端账号 · 个人记录保存在在线服务':'本机服务 · 个人记录保存在当前服务中'}</p><form id="auth-form">${register?'<div class="form-field"><label for="name">怎么称呼你</label><input id="name" name="name" required maxlength="60" autocomplete="name" placeholder="你的名字"></div>':''}<div class="form-field"><label for="email">邮箱</label><input id="email" name="email" type="email" required autocomplete="email" placeholder="you@example.com"></div><div class="form-field"><label for="password">密码${register?'（至少 10 位）':''}</label><input id="password" name="password" type="password" required ${register?'minlength="10"':''} autocomplete="${register?'new-password':'current-password'}" placeholder="输入密码"></div>${register?'<label class="check"><input type="checkbox" name="consent" required><span>我同意小伴'+(cloudRuntime?'在在线服务中':'')+'保存我主动提供及授权同步的健康与行为数据，用于个人记忆和陪伴提醒。可随时删除。</span></label>':''}<p class="error" role="alert"></p><button class="btn primary" type="submit">${register?'创建我的小伴':'登录'} ${icon('arrow')}</button></form><div class="auth-switch">${register?'已经有账号？':'第一次见面？'}<button id="switch-auth">${register?'登录':'创建账号'}</button></div><div class="or">先感受一下</div><button id="demo-start" class="btn secondary">${icon('activity')} 用模拟数据体验</button><p class="demo-note">独立演示账号 · 合成数据 · 无需连接设备</p></section></main>`;
  $('#switch-auth').onclick=()=>renderAuth(register?'login':'register');
  $('#demo-start').onclick=async function(){const attempt=++authRevision;this.disabled=true;this.textContent='正在准备你的体验…';try{const result=await post('/demo');if(attempt===authRevision)await signIn(result);}catch(e){if(attempt===authRevision){toast(friendlyError(e),true);this.disabled=false;this.textContent='用模拟数据体验';}}};
  $('#auth-form').onsubmit=async e=>{e.preventDefault();const attempt=++authRevision;const form=e.currentTarget;const submit=$('button[type=submit]',form);submit.disabled=true;$('.error',form).textContent='';const fields=new FormData(form);const body={email:fields.get('email'),password:fields.get('password')};if(register)Object.assign(body,{name:fields.get('name'),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'Asia/Shanghai',consent_health_data:fields.has('consent')});try{const result=await post(register?'/auth/register':'/auth/login',body);if(attempt===authRevision)await signIn(result);}catch(error){if(attempt===authRevision){$('#password',form).value='';errorTarget(form,error);submit.disabled=false;}}finally{body.password='';}};
}
async function signIn(data){await pushCleanup;state.token=data.token;state.syncWarning=false;sessionGuard.replace(state.token);localStorage.setItem('companion_token',state.token);state.page='today';if(await loadData()){reminderTracker.reset(state.reminders);render();}}
async function loadData(){
  const ticket=sessionGuard.capture(),historyRevision=chatRevision,chatAtLoad=chatRequest;
  const read=path=>api(path,{sessionToken:ticket.token});
  let results;
  try{results=await Promise.all([read('/dashboard'),read('/memories'),read('/events'),read('/reminders'),read('/chat'),read('/preferences'),read('/integrations/status'),read('/threads')]);}
  catch(error){if(!sessionGuard.isCurrent(ticket))return false;throw error;}
  const pushState=cloudRuntime?await getBackgroundPushState(read):{enabled:false,supported:false};
  let adminConfig=null,adminCareConfig=null,adminPushConfig=null,adminUsers=[],adminUsersLoaded=false;
  if(results[0]?.user?.is_admin===true){
    const userPath='/admin/users'+(state.adminSearch?'?email='+encodeURIComponent(state.adminSearch):'');
    const [configResult,usersResult,careResult,pushResult]=await Promise.allSettled([read('/admin/model-config'),read(userPath),read('/admin/care-config'),read('/admin/push-config')]);
    if(careResult.status==='fulfilled')adminCareConfig=careResult.value;
    if(pushResult.status==='fulfilled')adminPushConfig=pushResult.value;
    if(configResult.status==='fulfilled')adminConfig=configResult.value;
    if(usersResult.status==='fulfilled'){adminUsers=items(usersResult.value);adminUsersLoaded=true;}
  }
  if(!sessionGuard.isCurrent(ticket))return false;
  browserPushState=pushState;
  state.adminConfig=adminConfig;state.adminCareConfig=adminCareConfig;state.adminPushConfig=adminPushConfig;state.adminUsers=adminUsers;state.adminUsersLoaded=adminUsersLoaded;
  const messages=state.sending||chatRequest||chatAtLoad||chatRevision!==historyRevision?state.messages:items(results[4]);
  [state.dashboard,state.memories,state.events,state.reminders,state.messages,state.preferences,state.integrations,state.threads]=[results[0],items(results[1]),items(results[2]),items(results[3]),messages,results[5],results[6],items(results[7])];
  careInbox.reset(ticket.token);await careInbox.load({notify:false});
  if(!sessionGuard.isCurrent(ticket))return false;
  return true;
}
async function refresh(){if(await loadData())render();}
const pageNames={today:'今日陪伴',care:'关心消息',threads:'在意的事',memories:'了解我的',timeline:'日常记录',settings:'陪伴设置'};
const threadStatus={active:'正在经历',paused:'先放一放',completed:'已经告一段落'};
const supportStyles={listen:{title:'听我说',description:'先听一听，留一点表达空间'},reflect:{title:'一起理清',description:'一起看看现在最在意什么'},plan:{title:'给我建议',description:'找一个可以开始的小行动'}};
function backgroundNote(){if(!cloudRuntime)return '小伴运行时会检查到期跟进；关闭本机服务后停止。';const check=state.integrations?.background_check;return check?.healthy?`云端约每 ${check.interval_minutes} 分钟检查，关闭页面也会继续。接收系统通知需在当前浏览器开启后台通知；检查和送达可能延迟。`:check?.configured?'云端后台检查已配置，最近运行尚待确认；提醒仍可在消息箱查看。':'云端后台检查尚未连接；页面打开时可检查到期跟进。';}
function render(){
  if(!state.dashboard)return;
  const user=state.dashboard.user;
  const titles={care:'有需要时，小伴会来关心。',today:'今天，想从哪件事说起？',threads:'你在意的事，小伴接着记。',timeline:'日常的点滴，都在这里。',memories:'慢慢了解，也随你改变。',settings:'用你舒服的方式陪伴。'};
  const descriptions={care:'先认真了解，再轻轻问候；你可以接着说，也可以按自己的节奏回应。',today:`${esc(user.name)}，${localDate(new Date(),{month:'long',day:'numeric',weekday:'long'})} · 留一点时间给自己。`,threads:'记录正在经历的事情、后来的变化，以及你希望被跟进的时间。',timeline:'身体记录为你的生活补充线索，未记录的部分保持未知。',memories:'你确认的偏好、处境和边界，都可以随时更正或删除。',settings:'交流的方式、主动的频率与时机，都由你来决定。'};
  app.innerHTML=`<div class="shell"><aside class="sidebar">${brand()}<nav class="nav" aria-label="主要导航">${Object.entries(pageNames).map(([id,label])=>`<button data-page="${id}" class="${state.page===id?'active':''}" ${state.page===id?'aria-current="page"':''}>${icon({today:'home',care:'bell',threads:'bell',timeline:'clock',memories:'memory',settings:'settings'}[id])}<span>${label}</span>${id==='care'?`<span class="care-nav-count" ${careInbox.snapshot().unread_count?'':'hidden'}>${careInbox.snapshot().unread_count}</span>`:''}</button>`).join('')}</nav><div class="side-bottom"><div class="side-note"><strong>记得你在意的事。</strong>一起经历，也允许改变。<br>按你舒服的节奏来。</div><div class="account"><span class="avatar">${esc((user.name||'你').slice(0,1))}</span><div class="account-name">${esc(user.name)}<small>${user.is_demo?'模拟体验账号':cloudRuntime?'云端个人空间':'我的个人空间'}</small></div><button id="logout" aria-label="退出登录" title="退出登录">${icon('logout')}</button></div></div></aside><main class="workspace"><div class="topbar"><div class="breadcrumbs">日常 / <strong>${pageNames[state.page]}</strong></div><div class="top-actions">${user.is_demo?'<span class="pill amber"><i class="dot"></i>模拟数据</span>':''}<span class="pill neutral">${state.integrations?.model?.configured&&state.preferences?.model_consent?'模型辅助已开启':ruleModeLabel}</span><button class="btn secondary small" id="refresh" aria-label="刷新数据">${icon('refresh')}刷新</button></div></div><div class="page-title"><div><h1>${titles[state.page]}</h1><p>${descriptions[state.page]}</p></div><button class="btn primary" id="primary-action">${icon('plus')}${state.page==='memories'?'补充理解':state.page==='today'||state.page==='threads'?'记一件在意的事':'记录日常'}</button></div><div class="content-grid${state.page==='care'?' care-page-grid':''}"><div class="main-column">${{care:()=>renderCareInbox(careInbox.snapshot(),state.preferences,state.threads),today:renderToday,threads:renderThreads,timeline:renderTimeline,memories:renderMemories,settings:renderSettings}[state.page]()}</div>${state.page==='care'?'':renderChat()}</div><footer class="page-footer"><span>小伴 · 认真记住，也轻轻陪你。</span><span>${user.is_demo?'当前为合成演示数据。真实设备需在手机端授权。':'你决定分享和记住什么。'} · ${esc(user.timezone)}${cloudRuntime?' · 云端账号，记录保存在在线服务':' · 当前本机服务'}</span></footer></main></div>`;
  bindShell();
}
function supportPicker(){const current=state.preferences?.support_style||'reflect';return `<div class="support-picker" role="group" aria-label="这次希望怎样被陪伴">${Object.entries(supportStyles).map(([key,value])=>`<button type="button" data-support-style="${key}" class="${current===key?'selected':''}" aria-pressed="${current===key}" title="${value.description}">${value.title}</button>`).join('')}</div>`;}
function renderThreadCard(t,compact=false){return `<article class="thread-card ${t.status!=='active'?'thread-muted':''}"><div class="thread-top"><span class="pill ${t.status==='active'?'':'neutral'}">${threadStatus[t.status]||esc(t.status)}</span>${t.follow_up_enabled&&t.status==='active'?'<span class="thread-optin">'+icon('bell')+'已约定跟进</span>':''}</div><h3>${esc(t.title)}</h3>${t.details?`<p class="thread-details">${esc(t.details)}</p>`:''}<div class="thread-dates">${t.event_at?`<span>${icon('clock')}事情时间：${localDate(t.event_at,{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}</span>`:''}${t.follow_up_enabled&&t.follow_up_at?`<span>${icon('bell')}希望跟进：${localDate(t.follow_up_at,{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}${new Date(t.follow_up_at)<new Date()?' · 时间已到，按设置检查':''}</span>`:'<span>尚未约定主动跟进</span>'}</div>${!compact&&t.outcome?`<div class="thread-outcome"><span>后来的变化</span><p>${esc(t.outcome)}</p></div>`:''}<div class="thread-actions"><button class="btn soft small" data-edit-thread="${esc(t.id)}">${compact?'接着记':'更新进展'}</button><button class="text-link" data-chat-thread="${esc(t.id)}">聊聊这件事 ${icon('arrow')}</button>${compact?'':`<button class="thread-delete" data-delete-thread="${esc(t.id)}" aria-label="删除这件事">${icon('trash')}</button>`}</div></article>`;}
function renderThreads(){const active=state.threads.filter(t=>t.status==='active'),others=state.threads.filter(t=>t.status!=='active');return `<section class="intro-strip">一次面试、一段忙碌的日子、一个慢慢实现的愿望，都可以接着记。主动跟进需要你明确开启并选择时间。<p class="subtle-note">${backgroundNote()}</p></section><section class="panel threads-panel"><div class="panel-head"><h2>正在经历 <span class="count-note">${active.length}</span></h2><button class="text-link" data-new-thread>记一件事 ${icon('plus')}</button></div>${active.length?`<div class="thread-grid">${active.map(t=>renderThreadCard(t)).join('')}</div>`:'<div class="empty">还没有正在跟进的事情。<br>可以从“最近有什么让你惦记”开始。</div>'}</section>${others.length?`<section class="panel threads-panel"><div class="panel-head"><h2>先放下的与已经经历的</h2></div><div class="thread-grid">${others.map(t=>renderThreadCard(t)).join('')}</div></section>`:''}`;}

function renderToday(){
  const d=state.dashboard,m=d.metrics||{},active=state.threads.filter(t=>t.status==='active'),contexts=state.memories.filter(m=>m.is_active!==false&&m.category==='context').slice(-2),known=state.memories.filter(m=>m.is_active!==false&&m.origin==='user');
  return `<section class="hero companion-hero"><div class="hero-copy"><span class="eyebrow">ONE THING AT A TIME</span><h2>${contexts.length?'这些日子，小伴记得。':'不用一次讲完，我们慢慢来。'}</h2><p>${contexts.length?contexts.map(m=>esc(m.text)).join('<br>'):'最近在忙什么，有什么让你期待，或是有点惦记？<br>你可以从一件小事说起。'}</p><button class="btn soft small hero-chat" data-focus-chat>聊聊今天 ${icon('arrow')}</button></div><div class="hero-art" aria-hidden="true"><svg viewBox="0 0 150 150"><circle cx="92" cy="63" r="47" fill="#d9e4bf"/><path d="M78 140V65" stroke="#79956c" stroke-width="3"/><path d="M79 103C42 108 30 73 39 66c29-2 42 16 40 37Z" fill="#a8bd8c"/><path d="M79 82C76 48 111 40 119 51c2 22-15 32-40 31Z" fill="#809f72"/><path d="M51 80q21 10 28 25m0-24q12-14 27-20" stroke="#eef2dd" fill="none" stroke-width="1.5"/></svg></div></section><section class="panel threads-panel"><div class="panel-head"><h2>你在意的，小伴接着记</h2><button data-page="threads" class="text-link">全部 ${icon('arrow')}</button></div>${active.length?active.slice(0,3).map(t=>renderThreadCard(t,true)).join(''):'<div class="life-empty"><div class="life-empty-symbol">'+icon('bell')+'</div><p>有些事，值得有人接着记。<br><span>面试、项目、一次出行，或想慢慢做好的事。</span></p><button class="btn secondary small" data-new-thread>记下第一件事</button></div>'}</section><section class="panel understanding-preview"><div class="panel-head"><h2>小伴慢慢了解的你</h2><button data-page="memories" class="text-link">看看与更正 ${icon('arrow')}</button></div>${known.length?`<div class="known-notes">${known.slice(-3).reverse().map(m=>`<button data-edit-memory="${esc(m.id)}"><span>${labels[m.category]||'你确认的事'}</span><p>${esc(m.text)}</p></button>`).join('')}</div>`:'<p class="muted">你喜欢怎样休息，最近在经历什么，希望小伴怎样陪你——由你决定记住哪些。</p><button class="text-link" data-new-memory>告诉小伴一件事 '+icon('plus')+'</button>'}</section><section class="panel reminders-card"><div class="panel-head"><h2>留给你的陪伴消息</h2><button id="evaluate" class="text-link">检查现在 ${icon('refresh')}</button></div>${renderReminders()}<p class="subtle-note">${backgroundNote()}</p></section><details class="panel health-details"><summary><span>${icon('activity')}身体与日常节奏</span><small>步数、睡眠、饮水等记录</small></summary><div class="health-content"><section class="metrics" aria-label="今日数据">${[{key:'steps',title:'今日步数',icon:'steps',unit:'步'},{key:'sleep_hours',title:'最近睡眠',icon:'moon',unit:'小时',digits:1},{key:'workout_minutes',title:'今日运动',icon:'activity',unit:'分钟'},{key:'water_ml',title:'今日饮水',icon:'water',unit:'ml'}].map(x=>`<article class="metric panel"><div class="metric-label">${icon(x.icon)}${x.title}</div><div class="metric-value">${number(m[x.key],x.digits||0)}<span>${x.unit}</span></div><div class="metric-foot">${m[x.key]==null?'等待记录或设备同步':d.user.is_demo?'合成演示记录':'已记录数据'}</div></article>`).join('')}</section><div class="health-chart"><div class="panel-head"><h3>近 7 天步数</h3><button class="text-link" data-new-event>补一条记录 ${icon('plus')}</button></div>${renderChart(d.series||[])}${d.insights?.[0]?`<p class="subtle-note">${esc(d.insights[0].body)}</p>`:''}</div></div></details>`;
}

function renderChart(series){const rows=series.slice(-7);if(!rows.length)return '<div class="empty">记录还在路上，日常的节奏会慢慢出现。</div>';const values=rows.map(r=>Math.max(0,Number(r.steps)||0));const max=Math.max(6000,...values)*1.15;const width=600,height=177,pad=29,inner=width-2*pad,spacing=inner/rows.length,base=144;return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="近七天步数趋势"><defs><linearGradient id="bar-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#87a783"/><stop offset="1" stop-color="#bed0ac"/></linearGradient></defs>${[0,1,2].map(i=>`<line x1="0" y1="${24+i*60}" x2="600" y2="${24+i*60}" stroke="#edf0e8" stroke-dasharray="4 5"/><text x="0" y="${19+i*60}">${number(max*(1-i/2))}</text>`).join('')}${rows.map((r,i)=>{const h=values[i]/max*113,x=pad+i*spacing+spacing/2;const date=r.date?.slice(5).replace('-','/');return `<rect x="${x-15}" y="${base-h}" width="30" height="${Math.max(h,2)}" rx="5" fill="${i===rows.length-1?'#3c785f':'url(#bar-fill)'}"><title>${esc(r.date)}：${number(values[i])} 步</title></rect><text text-anchor="middle" x="${x}" y="168">${esc(date)}</text>`;}).join('')}</svg>`;}
function renderReminders(){
  if(!state.reminders.length)return `<div class="empty">${icon('bell')}<br>暂时没有需要提醒的事。<br>按自己的节奏，过好今天。</div>`;
  return state.reminders.slice(0,10).map(r=>{const active=['pending','sent','snoozed'].includes(r.status);return `<article class="reminder ${active?'':'complete'}"><div class="reminder-icon ${esc(r.category)}">${icon(r.category==='sleep'?'moon':r.category==='meal'?'meal':'activity')}</div><div class="reminder-copy"><h3>${esc(r.title)}</h3><p>${esc(r.body)}</p><div class="reminder-meta"><span>${statusLabel[r.status]||esc(r.status)}</span><span>·</span><span>${deliveryLabel[r.delivery_status]||esc(r.delivery_status||'仅消息箱')}</span>${r.created_at?`<span>· ${localDate(r.created_at,{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}</span>`:''}</div>${active?`<div class="reminder-actions"><button data-feedback="done" data-id="${esc(r.id)}">已完成</button><button data-feedback="snooze" data-id="${esc(r.id)}">30 分钟后</button><button data-feedback="dismiss" data-id="${esc(r.id)}">今天跳过</button><button data-feedback="disable" data-id="${esc(r.id)}">不再提醒这类</button></div>`:''}</div></article>`;}).join('');
}
function renderTimeline(){
  const events=[...state.events].sort((a,b)=>new Date(b.start_at)-new Date(a.start_at));let last='';
  return `<section class="intro-strip">你的主动记录与设备数据会一起保存在这里。未采集到数据时，小伴会保留未知状态。</section><section class="panel timeline-card"><div class="panel-head"><h2>行为时间线</h2><span class="pill neutral">${events.length} 条记录</span></div>${events.length?events.map(e=>{const date=localDate(e.start_at,{month:'long',day:'numeric',weekday:'long'});const group=date!==last?`<div class="timeline-date">${date}</div>`:'';last=date;return `${group}<article class="event"><div class="event-icon">${icon(eventIcon[e.kind])}</div><div class="event-body"><h3>${labels[e.kind]||esc(e.kind)}${e.value!=null?` · ${number(e.value,1)} ${esc(eventUnit(e))}`:''}</h3><p>${esc(e.metadata?.note||e.metadata?.food||'')||'已保存这次记录'}</p><small class="muted">${localDate(e.start_at,{hour:'2-digit',minute:'2-digit'})} · ${esc(eventSource(e))}${e.metadata?.granularity==='daily_summary'?' · 日汇总':''}</small></div><button data-delete-event="${esc(e.id)}" aria-label="删除这条${esc(labels[e.kind]||'行为')}记录">${icon('trash')}</button></article>`;}).join(''):'<div class="empty">还没有记录。点击“记录日常”，从一杯水开始。</div>'}</section>`;
}
function renderMemoryCard(m){return `<article class="panel memory-card ${m.is_active===false?'memory-expired':''}"><div class="memory-pills"><span class="pill ${m.category==='pattern'?'amber':m.is_active===false?'neutral':''}">${labels[m.category]||esc(m.category)}</span>${m.is_active===false?'<span class="pill neutral">已到期</span>':''}</div><p>${esc(m.text)}</p><div class="memory-expiry">${m.expires_at?`${m.is_active===false?'到期于':'参考至'} ${localDate(m.expires_at,{year:'numeric',month:'numeric',day:'numeric'})}`:'持续参考 · 你可以随时更正'}</div><div class="memory-meta"><span>${m.origin==='user'?'你确认的内容':m.category==='pattern'?'来自行为记录 · 可更正':'已有记忆'}${m.evidence_ids?.length?` · ${m.evidence_ids.length} 条依据`:''}</span><div><button data-edit-memory="${esc(m.id)}" aria-label="编辑记忆">${icon('edit')}</button><button data-delete-memory="${esc(m.id)}" aria-label="删除记忆">${icon('trash')}</button></div></div></article>`;}
function renderMemories(){return `<section class="intro-strip">${icon('memory')} 你可以确认、更正小伴对你的了解。到期的处境保留在这里，不再作为当前理解；聊天中的建议在你保存前不会成为个人事实。</section>${memoryGroups(state.memories).filter(g=>g.items.length||g.id==='stable'||g.id==='context'||g.id==='boundary').map(g=>`<section class="memory-group"><div class="memory-group-head"><div><h2>${g.title}<span class="count-note">${g.items.length}</span></h2><p>${g.description}</p></div>${['stable','context','boundary'].includes(g.id)?`<button class="text-link" data-new-memory-category="${g.id==='stable'?'preference':g.id}">${icon('plus')}补充</button>`:''}</div><div class="memory-grid">${g.items.length?g.items.map(renderMemoryCard).join(''):`<div class="empty compact-empty">${g.id==='context'?'比如：这周在赶项目，希望少一点打扰。':g.id==='boundary'?'比如：我倾诉时请先听，不要立即给建议。':'比如：我喜欢散步，也重视留给自己的时间。'}</div>`}</div></section>`).join('')}`;}

function renderSettings(){const p=state.preferences||{},i=state.integrations||{};return `${renderCareConsent(careInbox.snapshot(),p)}<section class="panel settings-card"><h2>轻提醒，按你的节奏</h2><form id="preferences-form"><div class="settings-row"><div><h3>开启主动陪伴</h3><p>小伴会依据新记录和你的目标，选择值得提醒的事情。</p></div><label class="switch"><input name="enabled" type="checkbox" ${p.enabled?'checked':''} aria-label="开启主动陪伴"><span></span></label></div><div class="settings-form"><div class="form-field"><label for="support-style">通常希望小伴怎样陪伴</label><select id="support-style" name="support_style">${Object.entries(supportStyles).map(([key,value])=>`<option value="${key}" ${(p.support_style||'reflect')===key?'selected':''}>${value.title} · ${value.description}</option>`).join('')}</select></div><label class="check"><input type="checkbox" name="life_followups_enabled" ${p.life_followups_enabled!==false?'checked':''}><span>允许跟进我明确约定的事情<br><small>仍需逐件开启、选择时间，并遵守免打扰和每天的提醒上限。</small></span></label><div class="form-row"><div class="form-field"><label for="quiet-start">免打扰开始</label><input id="quiet-start" type="time" name="quiet_start" value="${esc(p.quiet_start||'22:00')}" required></div><div class="form-field"><label for="quiet-end">免打扰结束</label><input id="quiet-end" type="time" name="quiet_end" value="${esc(p.quiet_end||'08:00')}" required></div></div><div class="form-field"><label for="daily-limit">每天最多主动提醒</label><select id="daily-limit" name="daily_limit">${[0,1,2,3,4,5,10].map(n=>`<option value="${n}" ${Number(p.daily_limit)===n?'selected':''}>${n} 条</option>`).join('')}</select></div><div class="form-field"><label>你想关注的事情</label><div class="inline-actions">${['sleep','activity','meal'].map(c=>`<label class="check"><input type="checkbox" name="category" value="${c}" ${(p.reminder_categories||[]).includes(c)?'checked':''}>${labels[c]}</label>`).join('')}</div></div><p class="subtle-note">时区：${esc(p.timezone||state.dashboard.user.timezone)}。${backgroundNote()}</p><p class="error" role="alert"></p><button type="submit" class="btn primary">保存陪伴偏好</button></div></form></section><section class="panel settings-card"><h2>连接你的日常</h2><p>手机端授权健康数据后，运动和睡眠会同步到个人时间线。支持范围取决于设备和健康平台。</p><div class="integration-state"><span class="pill ${i.open_wearables?.connected?'':'neutral'}">${icon('device')}${i.open_wearables?.connected?'设备账号已绑定':i.open_wearables?.configured?'数据服务已配置':'数据服务未配置'}</span><span class="pill neutral">${i.model?.configured?'语言模型已配置':ruleModeLabel}</span><span class="pill neutral">${i.push?.configured?'手机推送已配置':'消息箱已启用'}</span></div><div class="integration-actions"><button id="connect-wearables" class="btn secondary small">连接 Open Wearables</button><button id="sync-wearables" class="btn soft small">${icon('refresh')}同步已连接数据</button>${cloudRuntime?`<button id="browser-notifications" class="btn secondary small">${browserPushState.enabled?'重新连接后台通知':'开启浏览器后台通知'}</button>${browserPushState.enabled?'<button id="test-browser-notifications" class="btn soft small">发送测试通知</button><button id="stop-browser-notifications" class="btn soft small">关闭本浏览器通知</button>':''}`:'<button id="browser-notifications" class="btn secondary small">开启页面内提醒</button>'}</div><p class="subtle-note">健康读取与授权需要在 Flutter 手机端完成。${cloudRuntime?`后台通知：${browserPushState.enabled?'本浏览器已订阅':'本浏览器尚未订阅'}。网页关闭后可由浏览器接收；系统关闭、浏览器完全退出或通知被禁用时可能无法送达。${browserPushState.reason?esc(browserPushState.reason):''}`:'页面内提醒需要保持此页面打开。'}</p></section><section class="panel settings-card"><h2>你的记忆，由你掌握</h2><p>${cloudRuntime?'记录、记忆和提醒保存在在线服务中，使用同一云端账号访问。':'记录、记忆和提醒保存在当前本机服务的数据目录。'}你可以逐条删除，也可以删除整个账号。</p><div class="danger-zone"><button id="delete-account" class="btn danger small">删除账号与全部数据</button><button id="logout-settings" class="btn secondary small">退出登录</button></div></section>${renderAdmin()}${renderAdminUsers()}${renderCareAdmin(state.dashboard?.user,state.adminCareConfig,state.adminPushConfig)}`;}
function renderAdmin(){
  if(state.dashboard?.user?.is_admin!==true)return '';
  const c=state.adminConfig;if(!c)return `<section class="panel settings-card admin-card"><span class="pill amber">管理员</span><h2>模型调用配置</h2><p>暂时无法读取配置。此入口仅向管理员开放。</p><button class="btn secondary small" id="reload-admin">重新读取</button><p class="error" id="admin-error" role="alert"></p></section>`;
  return `<section class="panel settings-card admin-card"><div class="panel-head"><h2>模型调用配置</h2><span class="pill amber">仅管理员</span></div><p>配置会供当前服务的用户使用。普通用户只能决定是否授权模型处理自己的摘要。</p><form id="admin-model-form" autocomplete="off"><div class="form-field"><label for="admin-model-mode">调用方式</label><select id="admin-model-mode" name="mode"><option value="disabled" ${c.mode==='disabled'?'selected':''}>关闭模型，使用规则陪伴</option><option value="api" ${c.mode==='api'?'selected':''}>兼容 API 服务</option><option value="local" ${c.mode==='local'?'selected':''}>本地 Codex / Claude</option></select></div><div id="admin-api-fields"><div class="form-field"><label for="admin-api-url">API 地址</label><input type="url" id="admin-api-url" name="api_url" value="${esc(c.api_url||'')}" placeholder="https://api.example.com/v1" maxlength="500"></div><div class="form-field"><label for="admin-api-key">API Key${c.api_key_configured?' · 已保存，留空保留':''}</label><input type="password" id="admin-api-key" name="api_key" autocomplete="new-password" placeholder="输入新的服务端密钥" maxlength="4096"></div></div><div class="form-field" id="admin-model-field"><label for="admin-model-name">API 模型名称</label><input id="admin-model-name" name="model" value="${esc(c.model||'')}" maxlength="200" placeholder="服务支持的模型名称"></div><div id="admin-local-fields"><div class="form-field"><label for="admin-local-provider">本地调用工具</label><select id="admin-local-provider" name="local_provider"><option value="codex" ${c.local_provider==='codex'?'selected':''}>Codex CLI</option><option value="claude" ${c.local_provider==='claude'?'selected':''}>Claude CLI</option></select></div><div class="form-field"><label for="admin-local-model">本地 CLI 具体模型（可选）</label><input id="admin-local-model" name="local_model" value="${esc(c.local_model||'')}" maxlength="100" pattern="[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}" placeholder="留空使用该 CLI 的默认模型"><small class="muted">与 API 模式的模型名称分别保存。</small></div>${c.runtime==='cloud'?'<p class="modal-note">在线版本通过你部署的 HTTPS 网关，连接网关服务器上已安装、已登录的工具。运行位置是服务器，不是访问者的电脑。</p>':'<p class="modal-note">由当前服务所在电脑调用已安装、已登录的工具，需要该电脑保持运行；不会自动使用远端访问者电脑上的登录账号。</p>'}<div class="form-field"><label for="admin-gateway-url">HTTPS 网关地址${c.runtime==='cloud'?'':'（本机直接调用可留空）'}</label><input type="url" id="admin-gateway-url" name="gateway_url" value="${esc(c.gateway_url||'')}" maxlength="500" placeholder="https://your-gateway.example.com"></div><div class="form-field"><label for="admin-gateway-key">网关令牌${c.gateway_key_configured?' · 已保存，留空保留':''}</label><input type="password" id="admin-gateway-key" name="gateway_key" autocomplete="new-password" maxlength="4096" placeholder="输入新的网关令牌"></div></div><p class="subtle-note">“本地调用”通常仍由 CLI 连接厂商云模型，并使用服务端登录账号的额度，不等于离线模型。密钥仅提交给当前服务保存，读取时不返回。测试会实际调用已保存的服务，可能产生使用量。</p><p class="error" id="admin-error" role="alert"></p><p class="admin-result" id="admin-result" role="status"></p><div class="integration-actions"><button type="submit" class="btn primary small">保存管理员配置</button><button type="button" class="btn secondary small" id="admin-model-test">测试已保存的配置</button></div></form></section>`;
}
function bindAdmin(){
  if(state.dashboard?.user?.is_admin!==true)return;
  const ticket=sessionGuard.capture();
  if($('#reload-admin'))$('#reload-admin').onclick=async function(){this.disabled=true;try{const config=await api('/admin/model-config',{sessionToken:ticket.token});if(!sessionGuard.isCurrent(ticket))return;state.adminConfig=config;render();}catch(error){if(sessionGuard.isCurrent(ticket))$('#admin-error').textContent=friendlyError(error);this.disabled=false;}};
  const form=$('#admin-model-form');if(!form)return;
  const updateMode=()=>{const mode=$('#admin-model-mode').value;$('#admin-api-fields').hidden=mode!=='api';$('#admin-local-fields').hidden=mode!=='local';$('#admin-model-field').hidden=mode!=='api';};
  $('#admin-model-mode').onchange=updateMode;updateMode();
  form.onsubmit=async e=>{e.preventDefault();if(!sessionGuard.isCurrent(ticket))return;const button=$('button[type=submit]',form);button.disabled=true;try{const payload=modelConfigPayload(Object.fromEntries(new FormData(form)));const config=await api('/admin/model-config',{method:'PUT',body:JSON.stringify(payload),sessionToken:ticket.token});if(!sessionGuard.isCurrent(ticket))return;state.adminConfig=config;await refresh();if(sessionGuard.isCurrent(ticket))toast('管理员模型配置已保存。');}catch(error){if(sessionGuard.isCurrent(ticket)&&form.isConnected){$('#admin-error').textContent=friendlyError(error);button.disabled=false;}}};
  $('#admin-model-test').onclick=async function(){if(!sessionGuard.isCurrent(ticket))return;this.disabled=true;$('#admin-error').textContent='';$('#admin-result').textContent='正在测试已保存的服务…';try{const result=await api('/admin/model-test',{method:'POST',body:'{}',timeoutMs:60000,sessionToken:ticket.token});if(sessionGuard.isCurrent(ticket)&&form.isConnected){$('#admin-result').textContent=result.detail||(result.ok?'调用测试成功。':'调用测试未通过。');this.disabled=false;}}catch(error){if(sessionGuard.isCurrent(ticket)&&form.isConnected){$('#admin-result').textContent='';$('#admin-error').textContent=friendlyError(error);this.disabled=false;}}};
}

function renderAdminUsers(){
  if(state.dashboard?.user?.is_admin!==true)return '';
  const rows=state.adminUsers||[];
  return `<section class="panel settings-card admin-card"><div class="panel-head"><h2>账号与管理员权限</h2><span class="pill amber">仅管理员</span></div><p>管理员可管理模型调用方式、服务密钥和用户的管理员权限。这里仅显示账号信息，不显示个人健康记录或聊天。</p><form id="admin-user-search" class="admin-user-search"><div class="form-field"><label for="admin-user-email">按邮箱精确查找（留空显示账号列表）</label><input id="admin-user-email" name="email" type="email" maxlength="200" value="${esc(state.adminSearch)}" placeholder="user@example.com" autocomplete="off"></div><button class="btn secondary small" type="submit">查找账号</button></form><p class="error" id="admin-users-error" role="alert"></p>${state.adminUsersLoaded?(rows.length?`<div class="admin-users">${rows.map(user=>`<article class="admin-user"><div class="admin-user-identity"><strong>${esc(user.name||'未设置称呼')}</strong><span>${esc(user.email)}</span><small>${user.created_at?'注册于 '+localDate(user.created_at,{year:'numeric',month:'numeric',day:'numeric'}):''}</small></div><div class="admin-user-actions"><span class="pill ${user.is_demo?'neutral':user.is_admin?'amber':'neutral'}">${user.is_demo?'演示账号':user.is_admin?'管理员':'普通用户'}</span>${user.is_demo?'<small class="muted">演示账号不可授权</small>':`<button type="button" class="btn ${user.is_admin?'secondary':'soft'} small" data-admin-role="${esc(user.id)}">${user.is_admin?'撤销管理员':'授权管理员'}</button>`}</div></article>`).join('')}</div>`:'<div class="empty">未找到匹配的正式账号。请对方先注册，再按邮箱查找。</div>'):'<div class="empty">账号列表暂时无法读取。可以通过上方查找重试。</div>'}<p class="subtle-note">系统会保留至少一位管理员；最后一位管理员的权限不能撤销。</p></section>`;
}
function bindAdminUsers(){
  if(state.dashboard?.user?.is_admin!==true)return;
  const ticket=sessionGuard.capture(),form=$('#admin-user-search');if(!form)return;
  form.onsubmit=async e=>{e.preventDefault();if(!sessionGuard.isCurrent(ticket))return;const email=String(new FormData(form).get('email')||'').trim().toLowerCase(),button=$('button',form);button.disabled=true;try{const result=await api('/admin/users'+(email?'?email='+encodeURIComponent(email):''),{sessionToken:ticket.token});if(!sessionGuard.isCurrent(ticket))return;state.adminSearch=email;state.adminUsers=items(result);state.adminUsersLoaded=true;render();}catch(error){if(sessionGuard.isCurrent(ticket)&&form.isConnected){$('#admin-users-error').textContent=friendlyError(error);button.disabled=false;}}};
  document.querySelectorAll('[data-admin-role]').forEach(button=>button.onclick=()=>{
    if(!sessionGuard.isCurrent(ticket))return;
    const target=state.adminUsers.find(user=>user.id===button.dataset.adminRole);if(!target||target.is_demo)return;
    const grant=target.is_admin!==true,self=target.id===state.dashboard.user.id;
    const description=(grant?'将为账号 '+target.email+' 授予管理员权限。该账号将可以管理模型调用方式、服务密钥，并授权或撤销其他账号的管理员权限。':'将撤销账号 '+target.email+' 的管理员权限。该账号仍可使用自己的个人空间，但不能再管理模型服务或用户权限。')+(self&&!grant?' 这是你当前登录的账号；撤销成功后，管理入口会关闭。':'');
    confirmAction(grant?'授权这个账号为管理员？':'撤销这个账号的管理员权限？',description,async()=>{
      await api('/admin/users/'+encodeURIComponent(target.id)+'/role',{method:'POST',body:JSON.stringify({is_admin:grant}),sessionToken:ticket.token});
      if(!sessionGuard.isCurrent(ticket))return;await refresh();if(sessionGuard.isCurrent(ticket))toast(grant?'已授予该账号管理员权限。':'已撤销该账号的管理员权限。');
    },grant?'确认授权管理员':'确认撤销管理员');
  });
}

function renderChat(){const messages=state.messages;return `<aside class="panel chat-panel" aria-label="和小伴聊天"><div class="chat-heading"><div class="companion-face" aria-hidden="true">✳</div><div><h2>小伴在这里</h2><p>${supportStyles[state.preferences?.support_style||'reflect'].description}</p></div><span class="dot" title="服务已连接"></span></div><div class="support-caption">这次想怎样被陪伴？</div>${supportPicker()}<div id="chat-body" class="chat-body" aria-live="polite"><p class="chat-intro">不用组织好语言，慢慢说就可以。</p>${messages.length?messages.map(m=>`<div class="message ${m.role==='user'?'user':'assistant'}${m.streaming?' streaming':''}" ${m.streaming?'aria-busy="true"':''} ${m.streaming&&!m.content?'hidden':''}>${esc(m.content)}</div>`).join(''):'<div class="message assistant">最近有什么让你惦记的事？你可以说说正在经历的生活，也可以先告诉我希望怎样被陪伴。</div>'}${state.sending&&!messages.some(m=>m.streaming&&m.content)?'<div class="chat-thinking" role="status">小伴正在回应…</div>':''}</div>${state.suggestions.length?`<div class="confirmation-suggestions"><p>要把这段分享接着记吗？<br><small>先看看并修改，保存后才会加入你的记忆或事情。</small></p>${state.suggestions.map((s,i)=>`<button type="button" data-save-suggestion="${i}">${icon(s.type==='thread'?'bell':'memory')}<span>${s.type==='thread'?'记为在意的事':'记为近期处境'}</span>${icon('plus')}</button>`).join('')}</div>`:messages.length?'':`<div class="suggestions"><button data-prompt="我想和你聊聊最近在忙的事情。">聊聊最近的生活 ${icon('arrow')}</button><button data-prompt="你记得我在意的哪些事？">聊聊你记住的我 ${icon('arrow')}</button></div>`}${state.chatStatus?`<div class="chat-status" role="status">${esc(state.chatStatus)}</div>`:''}<form class="chat-form" id="chat-form"><textarea id="chat-input" aria-label="给小伴发消息" placeholder="今天想跟小伴说点什么…" rows="2" maxlength="2000" required>${esc(state.chatDraft)}</textarea>${state.sending?'<button type="button" id="stop-chat" aria-label="停止生成">停止</button>':''}<button type="submit" aria-label="发送消息" ${state.sending?'disabled':''}>${icon('send')}</button></form><div class="chat-foot">${state.lastReplyMode==='model'?'模型辅助 · 结合已授权的个人理解与事情回应':state.lastReplyMode==='local_fallback'?'模型暂不可用 · 当前为规则回应':state.integrations?.model?.configured?(state.preferences?.model_consent?'模型辅助已授权':'规则模式 · 模型授权尚未开启'):'规则陪伴 · 尚未配置语言模型'}</div></aside>`;}

function bindShell(){
  if(state.syncWarning)$('.page-title').insertAdjacentHTML('afterend','<p class="intro-strip" role="alert">本地记录已保存；外部语义索引同步尚未完成。恢复服务后重试，当前回答只采用本地有效记忆。</p>');
  const preferenceForm=$('#preferences-form');
  if(preferenceForm){
    $('.settings-form',preferenceForm).insertAdjacentHTML('beforebegin',`<div class="settings-row"><div><h3>允许模型辅助理解</h3><p>启用后，当前服务会把必要的个人摘要交给已配置的模型处理。通过服务器上的 Codex/Claude 调用时，通常仍会连接厂商云模型，使用服务器登录账号的额度；本地调用不代表离线推理。关闭时使用已保存事实与规则。</p></div><label class="switch"><input name="model_consent" type="checkbox" ${state.preferences?.model_consent?'checked':''} aria-label="允许模型处理个人摘要"><span></span></label></div>`);
    $('.settings-form',preferenceForm).insertAdjacentHTML('afterbegin',`<div class="form-field"><label for="user-timezone">我的时区</label><input id="user-timezone" name="timezone" value="${esc(state.preferences?.timezone||'Asia/Shanghai')}" placeholder="例如 Asia/Shanghai" required maxlength="80"></div>`);
  }
  document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{state.page=b.dataset.page;render();window.scrollTo(0,0);if(state.page==='care')refreshCareInbox();});
  $('#logout').onclick=logout;if($('#logout-settings'))$('#logout-settings').onclick=logout;
  $('#refresh').onclick=async function(){this.disabled=true;try{await refresh();toast('日常已更新');}catch(e){toast(friendlyError(e),true);this.disabled=false;}};
  $('#primary-action').onclick=()=>state.page==='memories'?openMemory():['today','threads'].includes(state.page)?openThread():openEvent();
  document.querySelectorAll('[data-new-thread]').forEach(b=>b.onclick=()=>openThread());
  document.querySelectorAll('[data-new-memory]').forEach(b=>b.onclick=()=>openMemory());
  document.querySelectorAll('[data-new-memory-category]').forEach(b=>b.onclick=()=>openMemory(null,{category:b.dataset.newMemoryCategory}));
  document.querySelectorAll('[data-new-event]').forEach(b=>b.onclick=()=>openEvent());
  document.querySelectorAll('[data-focus-chat]').forEach(b=>b.onclick=()=>{const input=$('#chat-input');input?.scrollIntoView({block:'center',behavior:'smooth'});input?.focus();});
  document.querySelectorAll('[data-edit-thread]').forEach(b=>b.onclick=()=>openThread(state.threads.find(t=>t.id===b.dataset.editThread)));
  document.querySelectorAll('[data-chat-thread]').forEach(b=>b.onclick=()=>{const t=state.threads.find(t=>t.id===b.dataset.chatThread);if(t)sendChat('我想接着聊聊这件事：'+t.title);});
  document.querySelectorAll('[data-delete-thread]').forEach(b=>b.onclick=()=>confirmAction('忘记这件事？','这件事和依赖它的待发跟进会被删除或撤销。',async()=>{await api('/threads/'+encodeURIComponent(b.dataset.deleteThread),{method:'DELETE'});await refresh();toast('这件事已删除。');}));
  bindChat();
  if($('#evaluate'))$('#evaluate').onclick=async function(){this.disabled=true;try{const r=await post('/evaluate');await refresh();toast(r.created?`小伴整理了 ${r.created} 条新提醒`:'已检查：暂时没有需要新增的提醒。');}catch(e){toast(friendlyError(e),true);this.disabled=false;}};
  document.querySelectorAll('[data-feedback]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await post(`/reminders/${encodeURIComponent(b.dataset.id)}/feedback`,{action:b.dataset.feedback,minutes:30});await refresh();toast({done:'记下啦，这一小步也值得肯定。',snooze:cloudRuntime?'已设置稍后提醒。云端定时尚未开启，可稍后回到消息箱检查。':'好的，30 分钟后再看看。',dismiss:'今天先跳过，按你的节奏来。',disable:'已关闭这一类提醒。'}[b.dataset.feedback]);}catch(e){toast(friendlyError(e),true);b.disabled=false;}});
  document.querySelectorAll('[data-edit-memory]').forEach(b=>b.onclick=()=>openMemory(state.memories.find(m=>m.id===b.dataset.editMemory)));
  document.querySelectorAll('[data-delete-memory]').forEach(b=>b.onclick=()=>confirmAction('忘记这条记忆？','小伴会删除它，并更新相关的待发提醒。',async()=>{await api(`/memories/${encodeURIComponent(b.dataset.deleteMemory)}`,{method:'DELETE'});await refresh();toast('这条记忆已经删除。');}));
  document.querySelectorAll('[data-delete-event]').forEach(b=>b.onclick=()=>confirmAction('删除这次记录？','依赖这次记录的行为模式和待发提醒也会重新处理。',async()=>{await api(`/events/${encodeURIComponent(b.dataset.deleteEvent)}`,{method:'DELETE'});await refresh();toast('记录已删除。');}));
  bindAdmin();
  bindAdminUsers();
  bindCareUi();bindCareAdmin();
  if($('#preferences-form'))$('#preferences-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,fields=new FormData(form),button=$('button[type=submit]',form);button.disabled=true;try{await patch('/preferences',{enabled:fields.has('enabled'),model_consent:fields.has('model_consent'),timezone:fields.get('timezone'),quiet_start:fields.get('quiet_start'),quiet_end:fields.get('quiet_end'),daily_limit:Number(fields.get('daily_limit')),reminder_categories:fields.getAll('category'),support_style:fields.get('support_style'),life_followups_enabled:fields.has('life_followups_enabled')});await refresh();toast('已记住你喜欢的陪伴方式。');}catch(error){errorTarget(form,error);button.disabled=false;}};
  if($('#connect-wearables'))$('#connect-wearables').onclick=async function(){this.disabled=true;try{const r=await post('/integrations/open-wearables/connect');await refresh();toast(r.configured?'设备账号已准备好，请在手机端授权同步。':r.detail||'请先配置服务端的 Open Wearables 连接。',!r.configured);}catch(e){toast(friendlyError(e),true);this.disabled=false;}};
  if($('#sync-wearables'))$('#sync-wearables').onclick=async function(){this.disabled=true;try{const r=await post('/integrations/open-wearables/sync');await refresh();toast(`同步完成，新增 ${r.imported} 条记录。`);}catch(e){toast(friendlyError(e),true);this.disabled=false;}};
  if($('#browser-notifications'))$('#browser-notifications').onclick=async function(){
    if(!cloudRuntime){if(!('Notification'in window)){toast('当前环境不支持系统通知。',true);return;}const permission=await Notification.requestPermission();toast(permission==='granted'?'已开启页面内提醒，请保持小伴运行。':'通知未开启，仍可在消息箱查看。');return;}
    const ticket=sessionGuard.capture(),capturedApi=(path,options={})=>api(path,{...options,sessionToken:ticket.token});this.disabled=true;
    try{browserPushState=await enableBackgroundPush(capturedApi,{isCurrent:()=>sessionGuard.isCurrent(ticket)});if(!sessionGuard.isCurrent(ticket))return;await refresh();toast('已订阅后台通知。可以发送测试通知，确认设备是否收到。');}catch(error){if(sessionGuard.isCurrent(ticket)){toast(friendlyError(error),true);this.disabled=false;}}
  };
  if($('#test-browser-notifications'))$('#test-browser-notifications').onclick=async function(){const ticket=sessionGuard.capture();this.disabled=true;try{const result=await testBackgroundPush((path,options={})=>api(path,{...options,sessionToken:ticket.token}));if(sessionGuard.isCurrent(ticket))toast(result.detail,!result.accepted);}catch(error){if(sessionGuard.isCurrent(ticket))toast(friendlyError(error),true);}finally{this.disabled=false;}};
  if($('#stop-browser-notifications'))$('#stop-browser-notifications').onclick=async function(){const ticket=sessionGuard.capture();this.disabled=true;try{await stopBackgroundPush((path,options={})=>api(path,{...options,sessionToken:ticket.token}));if(!sessionGuard.isCurrent(ticket))return;browserPushState={...browserPushState,enabled:false};await refresh();toast('已关闭本浏览器的后台通知。');}catch(error){if(sessionGuard.isCurrent(ticket)){toast(friendlyError(error),true);this.disabled=false;}}};
  if($('#delete-account'))$('#delete-account').onclick=()=>confirmAction('删除账号与全部数据？','记录、记忆、对话、提醒和登录凭证将被删除。此操作无法撤销。',async()=>{await api('/me',{method:'DELETE'});logout();toast('账号与个人数据已删除。');});
}
function logout({revoke=true,clearStorage=true,cleanupPush=true}={}){const oldToken=state.token;for(const controller of activeRequests)controller.abort();activeRequests.clear();if(clearStorage)localStorage.removeItem('companion_token');state.token=null;sessionGuard.replace(null);state.dashboard=null;state.messages=[];state.memories=[];state.events=[];state.reminders=[];state.preferences=null;state.integrations=null;state.threads=[];state.suggestions=[];state.chatDraft='';state.lastReplyMode=null;state.chatStatus='';chatRequest=null;chatRevision+=1;state.adminConfig=null;state.adminCareConfig=null;state.adminPushConfig=null;careInbox.reset(null);state.adminUsers=[];state.adminSearch='';state.adminUsersLoaded=false;dueGate.reset();$('#modal')?.close();state.sending=false;state.syncWarning=false;reminderTracker.reset([]);renderAuth();if(cloudRuntime&&oldToken&&cleanupPush){pushCleanup=stopBackgroundPush((path,options={})=>api(path,{...options,sessionToken:oldToken})).catch(()=>{});}browserPushState={enabled:false,supported:false};if(revoke&&oldToken){pushCleanup=pushCleanup.finally(()=>{const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),5000);fetch('/api/auth/logout',{method:'POST',headers:{Authorization:`Bearer ${oldToken}`},credentials:'same-origin',cache:'no-store',keepalive:true,signal:controller.signal}).catch(()=>{}).finally(()=>clearTimeout(timer));});}}
function bindChat(){
  const chat=$('#chat-body');if(!chat)return;chat.scrollTop=chat.scrollHeight;
  const button=$('#chat-form button[type=submit]');button.disabled=state.sending;
  if($('#stop-chat'))$('#stop-chat').onclick=stopChat;
  if(state.sending)$('.chat-foot').textContent=state.messages.some(m=>m.streaming&&m.content)?'小伴正在回应…':'小伴正在查看记录…';
  $('#chat-form').onsubmit=async e=>{e.preventDefault();await sendChat($('#chat-input').value);};
  $('#chat-input').oninput=e=>{state.chatDraft=e.currentTarget.value;};
  document.querySelectorAll('[data-save-suggestion]').forEach(b=>b.onclick=()=>{const suggestion=state.suggestions[Number(b.dataset.saveSuggestion)];if(suggestion?.type==='memory')openMemory(null,suggestion);else if(suggestion?.type==='thread')openThread(null,suggestion);});
  document.querySelectorAll('[data-support-style]').forEach(b=>b.onclick=async()=>{const ticket=sessionGuard.capture();const style=b.dataset.supportStyle;if(style===(state.preferences?.support_style||'reflect'))return;b.disabled=true;try{const p=await api('/preferences',{method:'PATCH',body:JSON.stringify({support_style:style}),sessionToken:ticket.token});if(!sessionGuard.isCurrent(ticket))return;state.preferences={...state.preferences,...p,support_style:style};if($('#support-style'))$('#support-style').value=style;updateChatPanel();}catch(error){if(sessionGuard.isCurrent(ticket)){toast(friendlyError(error),true);b.disabled=false;}}});
  $('#chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chat-form').requestSubmit();}};
  document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>sendChat(b.dataset.prompt));
}
function updateChatPanel(){const panel=$('.chat-panel');if(!panel||!state.dashboard)return;panel.outerHTML=renderChat();bindChat();}
function stopChat(){if(chatRequest){chatRequest.stopped=true;chatRequest.controller.abort();}}
function updateStreamBubble(message){
  const bubble=$('.message.streaming'),body=$('#chat-body');
  const nearBottom=body&&body.scrollHeight-body.scrollTop-body.clientHeight<80;
  if(bubble){bubble.textContent=message.content;bubble.hidden=false;}
  $('.chat-thinking')?.remove();
  const footer=$('.chat-foot');if(footer)footer.textContent='小伴正在回应…';
  if(nearBottom)body.scrollTop=body.scrollHeight;
}
async function sendChat(text){
  const message=text.trim();if(!message||state.sending||!state.token)return;
  const ticket=sessionGuard.capture(),controller=new AbortController();
  const request={ticket,controller,revision:++chatRevision,stopped:false};chatRequest=request;activeRequests.add(controller);
  const current=()=>sessionGuard.isCurrent(ticket)&&chatRequest===request;
  const assistant={role:'assistant',content:'',streaming:true};
  state.sending=true;state.chatStatus='';state.chatDraft='';state.suggestions=[];state.messages.push({role:'user',content:message},assistant);updateChatPanel();
  try{
    const response=await requestChat({message,token:ticket.token,signal:controller.signal,onDelta:text=>{
      if(!current())return;assistant.content+=text;updateStreamBubble(assistant);
    }});
    if(!current())return;
    assistant.content=response.reply;assistant.streaming=false;state.sending=false;
    state.suggestions=(Array.isArray(response.suggestions)?response.suggestions:[]).filter(s=>s&&['memory','thread'].includes(s.type)).slice(0,2);state.lastReplyMode=response.mode;
    if(response.memory_sync==='failed')state.syncWarning=true;
    if(['synced','cleared'].includes(response.memory_sync))state.syncWarning=false;
    updateChatPanel();
    if(response.evidence?.length){const evidence=document.createElement('div');evidence.className='chat-evidence';evidence.textContent='依据：'+[...new Set(response.evidence.map(e=>e.label||e.id))].slice(0,6).join(' · ');$('#chat-body')?.append(evidence);}
    const memories=await api('/memories',{sessionToken:ticket.token}).catch(()=>null);
    if(current()&&memories)state.memories=items(memories);
  }catch(error){
    if(!current())return;
    if(error?.status===401){logout({revoke:false});return;}
    assistant.streaming=false;state.sending=false;
    if(!assistant.content)state.messages=state.messages.filter(item=>item!==assistant);
    state.chatStatus=request.stopped?'已停止生成':'回复中断：'+friendlyError(error);updateChatPanel();
  }finally{
    activeRequests.delete(controller);
    if(current()){chatRequest=null;state.sending=false;const button=$('#chat-form button[type=submit]');if(button)button.disabled=false;}
  }
}
function openModal(title,html){$('#modal-content').innerHTML=`<div class="modal-heading"><h2>${esc(title)}</h2><button type="button" id="close-modal" aria-label="关闭">×</button></div>${html}`;$('#close-modal').onclick=()=>$('#modal').close();$('#modal').showModal();}
function confirmAction(title,body,action,label='确认删除'){
  const ticket=sessionGuard.capture();openModal(title,`<p class="modal-note">${esc(body)}</p><p class="error" role="alert"></p><div class="modal-actions"><button class="btn secondary" id="cancel-modal">取消</button><button class="btn danger" id="confirm-modal">${esc(label)}</button></div>`);
  $('#cancel-modal').onclick=()=>$('#modal').close();
  $('#confirm-modal').onclick=async function(){if(!sessionGuard.isCurrent(ticket))return;this.disabled=true;try{await action();if(sessionGuard.isCurrent(ticket)&&this.isConnected)$('#modal').close();}catch(error){if(sessionGuard.isCurrent(ticket)&&this.isConnected){$('.error',$('#modal')).textContent=friendlyError(error);this.disabled=false;}}};
}
async function saveModal(form,path,data,ticket,method,success){
  if(!sessionGuard.isCurrent(ticket))return;
  const button=$('button[type=submit]',form);button.disabled=true;
  try{await api(path,{method,body:JSON.stringify(data),sessionToken:ticket.token});if(!sessionGuard.isCurrent(ticket))return;const savedType=path.startsWith('/memories')?'memory':path.startsWith('/threads')?'thread':null;state.suggestions=state.suggestions.filter(s=>s.type!==savedType);await refresh();if(!sessionGuard.isCurrent(ticket))return;if(form.isConnected)$('#modal').close();toast(success);}
  catch(error){if(sessionGuard.isCurrent(ticket)&&form.isConnected){errorTarget(form,error);button.disabled=false;}}
}
function openMemory(memory=null,suggestion={}){
  const ticket=sessionGuard.capture(),category=memory?.category==='pattern'?'fact':memory?.category||suggestion.category||'preference';
  const expiry=memory?memory.expires_at:suggestion.expires_at||(category==='context'?new Date(Date.now()+7*86400000).toISOString():null);
  openModal(memory?'更正小伴对你的了解':suggestion.text?'确认要记住的内容':'告诉小伴一件事',`<form id="memory-form"><div class="form-field"><label for="memory-category">这属于</label><select id="memory-category" name="category">${['preference','goal','fact','context','relationship','boundary'].map(c=>`<option value="${c}" ${c===category?'selected':''}>${labels[c]}</option>`).join('')}</select></div><div class="form-field"><label for="memory-text">希望小伴记住什么</label><textarea id="memory-text" name="text" required maxlength="1000" placeholder="比如：这周正在赶项目，希望先少一点运动提醒">${esc(memory?.text||suggestion.text||'')}</textarea></div><label class="check"><input type="checkbox" id="memory-expiry-enabled" name="expiry_enabled" ${expiry?'checked':''}><span>只在一段时间内参考这条理解</span></label><div class="form-field" id="memory-expiry-field"><label for="memory-expiry">参考到什么时候（本机时区）</label><input type="datetime-local" id="memory-expiry" name="expires_at" value="${localInput(expiry)}"><button type="button" id="renew-memory" class="text-link">从今天起再参考 7 天</button></div><p class="modal-note">保存表示你确认了这段内容。到期后仍可查看和更正，后续交流不再把它作为当前处境。你也可以选择持续参考。</p><p class="error" role="alert"></p><div class="modal-actions"><button class="btn primary" type="submit">${memory?'保存更正':'确认并记住'}</button></div></form>`);
  function syncExpiry(){const enabled=$('#memory-expiry-enabled').checked;$('#memory-expiry-field').hidden=!enabled;$('#memory-expiry').required=enabled;}
  $('#memory-expiry-enabled').onchange=syncExpiry;syncExpiry();
  $('#memory-category').onchange=e=>{if(!memory&&e.target.value==='context'&&!$('#memory-expiry').value){$('#memory-expiry-enabled').checked=true;$('#memory-expiry').value=localInput(new Date(Date.now()+7*86400000));syncExpiry();}};
  $('#renew-memory').onclick=()=>{$('#memory-expiry').value=localInput(new Date(Date.now()+7*86400000));};
  $('#memory-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,fields=new FormData(form);try{let expires=null;if(fields.has('expiry_enabled')){const date=new Date(fields.get('expires_at'));if(!Number.isFinite(+date))throw new Error('请选择有效的到期时间。');expires=date.toISOString();}await saveModal(form,memory?'/memories/'+encodeURIComponent(memory.id):'/memories',{text:fields.get('text'),category:fields.get('category'),expires_at:expires},ticket,memory?'PATCH':'POST','已保存你确认的理解。');}catch(error){if(sessionGuard.isCurrent(ticket))errorTarget(form,error);}};
}
function openThread(thread=null,suggestion={}){
  const ticket=sessionGuard.capture(),value=thread||suggestion;
  openModal(thread?'接着记这件事':suggestion.title?'确认这件在意的事':'记一件在意的事',`<form id="thread-form"><div class="form-field"><label for="thread-title">这件事是</label><input id="thread-title" name="title" required maxlength="120" value="${esc(value.title||'')}" placeholder="比如：周五的面试，或这周忙碌的项目"></div><div class="form-field"><label for="thread-details">目前的情况</label><textarea id="thread-details" name="details" maxlength="2000" placeholder="你在意什么，或者希望小伴了解的背景">${esc(value.details||'')}</textarea></div><div class="form-row"><div class="form-field"><label for="thread-status">现在的状态</label><select id="thread-status" name="status">${Object.entries(threadStatus).map(([key,label])=>`<option value="${key}" ${(value.status||'active')===key?'selected':''}>${label}</option>`).join('')}</select></div><div class="form-field"><label for="thread-event-at">事情时间（可选，本机时区）</label><input type="datetime-local" id="thread-event-at" name="event_at" value="${localInput(value.event_at)}"></div></div><div class="form-field"><label for="thread-outcome">后来的变化或结果（可选）</label><textarea id="thread-outcome" name="outcome" maxlength="2000" placeholder="后来发生了什么？你的想法有变化吗？">${esc(value.outcome||'')}</textarea></div><div class="followup-consent"><label class="check"><input type="checkbox" id="thread-followup-enabled" name="follow_up_enabled" ${value.follow_up_enabled?'checked':''}><span>我希望小伴主动跟进这件事</span></label><div class="form-field" id="thread-followup-field"><label for="thread-followup-at">希望何时联系我（本机时区）</label><input type="datetime-local" id="thread-followup-at" name="follow_up_at" value="${localInput(value.follow_up_at)}"></div><p class="subtle-note">${backgroundNote()} 跟进会遵守免打扰、总开关和每日次数；不会因为填写了事情时间就自动联系。</p>${state.preferences?.enabled===false||state.preferences?.life_followups_enabled===false?'<p class="badge-note">你的主动陪伴或事情跟进总开关当前关闭，约定将保存，开启后才会评估。</p>':''}</div><p class="error" role="alert"></p><div class="modal-actions"><button class="btn primary" type="submit">${thread?'保存这次进展':'确认并保存这件事'}</button></div></form>`);
  function syncFollowup(){const enabled=$('#thread-followup-enabled').checked;$('#thread-followup-field').hidden=!enabled;$('#thread-followup-at').required=enabled;}
  $('#thread-followup-enabled').onchange=syncFollowup;syncFollowup();
  $('#thread-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,fields=new FormData(form);try{const values=Object.fromEntries(fields);values.follow_up_enabled=fields.has('follow_up_enabled');const payload=threadPayload(values,thread);await saveModal(form,thread?'/threads/'+encodeURIComponent(thread.id):'/threads',payload,ticket,thread?'PATCH':'POST',payload.follow_up_enabled?'事情与跟进约定已保存。':'这件事已记下，未开启主动跟进。');}catch(error){if(sessionGuard.isCurrent(ticket))errorTarget(form,error);}};
}
function openEvent(){const ticket=sessionGuard.capture(),now=new Date();const localInput=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,16);openModal('记下日常的一小步',`<form id="event-form"><div class="form-row"><div class="form-field"><label for="event-kind">记录什么</label><select id="event-kind" name="kind">${['meal','water','workout','sleep','steps','mood'].map(k=>`<option value="${k}">${labels[k]}</option>`).join('')}</select></div><div class="form-field" id="event-value-field"><label for="event-value" id="event-value-label">数值</label><input id="event-value" name="value" type="number" step="any" min="0" max="100000" placeholder="可选"></div></div><div class="form-field"><label for="event-time">记录时间（本机时区）</label><input type="datetime-local" id="event-time" name="start_at" value="${localInput}" required></div><div class="form-field"><label for="event-note">想补充什么</label><textarea id="event-note" name="note" maxlength="1000" placeholder="午餐吃了什么？今天运动感觉如何？"></textarea></div><p class="error" role="alert"></p><div class="modal-actions"><button class="btn primary" type="submit">保存这次记录</button></div></form>`);
  const config={meal:{label:'餐次',unit:'entry',required:false},water:{label:'饮水量（ml）',unit:'ml',required:true},workout:{label:'运动时长（分钟）',unit:'min',required:true},sleep:{label:'睡眠时长（小时）',unit:'h',required:true},steps:{label:'步数',unit:'count',required:true},mood:{label:'心情评分（1–5）',unit:'score',required:true}};
  function update(){const kind=$('#event-kind').value,c=config[kind];$('#event-value-field').hidden=kind==='meal';$('#event-value-label').textContent=c.label;$('#event-value').required=c.required;$('#event-value').value='';$('#event-value').min=kind==='mood'?'1':'0';$('#event-value').max=kind==='mood'?'5':kind==='sleep'?'24':'100000';$('#event-value').placeholder=kind==='water'?'250':kind==='workout'?'30':kind==='sleep'?'7.5':'';$('#event-note').required=kind==='meal';}$('#event-kind').onchange=update;update();
  $('#event-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,fields=new FormData(form),kind=fields.get('kind'),start=new Date(fields.get('start_at'));try{if(Number.isNaN(start.getTime()))throw new Error('请选择有效的记录时间。');await saveModal(form,'/events',{kind,value:kind==='meal'?null:Number(fields.get('value')),unit:config[kind].unit,start_at:start.toISOString(),source:'manual',metadata:{note:fields.get('note')}},ticket,'POST','已记下。这一小步也算数。');}catch(error){if(sessionGuard.isCurrent(ticket))errorTarget(form,error);}};
}
function careSetText(element,value){if(element&&element.textContent!==value)element.textContent=value;}
function bindCareOpen(){document.querySelectorAll('[data-care-open]').forEach(button=>button.onclick=()=>careInbox.open(button.dataset.careOpen));}
function bindCareComposer(){
  const form=$('#care-reply-form'),input=$('#care-reply-input');if(!form||!input)return;
  input.oninput=()=>{careInbox.setDraft(input.value);const view=careInbox.snapshot(),send=$('#care-reply-send');if(send)send.disabled=view.sending||!!view.pendingRequest||!view.draft.trim();};
  form.onsubmit=async event=>{event.preventDefault();careInbox.setDraft(input.value);await careInbox.send();};
  const retry=$('#care-reply-retry');if(retry)retry.onclick=()=>careInbox.retry();
}
function updateCareSurface(){
  if(!state.dashboard||!state.token||careInbox.token!==state.token)return;
  const view=careInbox.snapshot(),count=$('.care-nav-count');if(count){careSetText(count,String(view.unread_count));count.hidden=view.unread_count===0;}
  careSetText($('#care-readiness'),careStatusText(view));
  careSetText($('#care-unread-count'),`${view.unread_count} 条未读`);
  const preferenceForm=$('#care-preferences-form');if(preferenceForm&&preferenceForm.dataset.dirty!=='true'){
    const enabled=$('[name="care_enabled"]',preferenceForm);if(enabled){enabled.checked=view.preferences?.enabled===true;enabled.disabled=!view.preferences;}
    const save=$('button[type="submit"]',preferenceForm);if(save)save.disabled=!view.preferences;
  }
  if(state.page!=='care'||!$('#care-root'))return;
  const list=$('#care-list'),stamp=JSON.stringify([view.selectedId,view.items]);
  if(list&&careRendered.get(list)!==stamp){const focused=document.activeElement?.dataset?.careOpen,scroll=list.scrollTop;list.innerHTML=renderCareList(view);careRendered.set(list,stamp);list.scrollTop=scroll;bindCareOpen();if(focused)Array.from(document.querySelectorAll('[data-care-open]')).find(b=>b.dataset.careOpen===focused)?.focus({preventScroll:true});}
  const column=$('#care-thread');if(column){
    const expected=view.detail?'ready':view.loading?'loading':'empty';
    if(column.dataset.careThread!==(view.selectedId||'')||column.dataset.careView!==expected||view.detail&&!$('#care-reply-form')){
      column.innerHTML=renderCareThread(view,state.threads);column.dataset.careThread=view.selectedId||'';column.dataset.careView=expected;bindCareComposer();bindCareSource();const messages=$('#care-messages');if(messages)messages.scrollTop=messages.scrollHeight;
    }else if(view.detail){
      careSetText($('#care-thread-title'),view.detail.thread.title||'小伴的关心');
      const messages=$('#care-messages'),messageStamp=JSON.stringify(view.detail.messages);
      if(messages&&careRendered.get(messages)!==messageStamp){const scroll=messages.scrollTop,nearBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<65;messages.innerHTML=renderCareMessages(view);careRendered.set(messages,messageStamp);messages.scrollTop=nearBottom?messages.scrollHeight:scroll;}
      const input=$('#care-reply-input');if(input&&input.value!==view.draft)input.value=view.draft;
      const send=$('#care-reply-send'),retry=$('#care-reply-retry');if(send)send.disabled=view.sending||!!view.pendingRequest||!view.draft.trim();if(retry){retry.hidden=!view.pendingRequest;retry.disabled=view.sending;}
      careSetText($('#care-reply-status'),view.sending?'小伴正在认真看你的回复…':view.pendingRequest?.saved?'你的回复已保存，小伴暂未完成回应。可以稍后重试。':view.pendingRequest?'上一条回复尚未确认。重试会使用同一个编号，不会重复发送。':'');
    }
  }
  careSetText($('#care-error'),view.error||'');
}
async function refreshCareInbox(){const ticket=sessionGuard.capture();await careInbox.load();if(sessionGuard.isCurrent(ticket)&&state.page==='care')await careInbox.refreshSelected();}
function bindCareUi(){
  bindCareOpen();bindCareComposer();bindCareSource();const refreshButton=$('#care-refresh');if(refreshButton)refreshButton.onclick=async()=>{refreshButton.disabled=true;try{await refreshCareInbox();}finally{if(refreshButton.isConnected)refreshButton.disabled=false;}};
  const form=$('#care-preferences-form');if(!form)return;form.onchange=()=>{form.dataset.dirty='true';};
  form.onsubmit=async event=>{event.preventDefault();const ticket=sessionGuard.capture(),fields=new FormData(form),button=$('button[type="submit"]',form),enabled=fields.has('care_enabled'),modelAllowed=state.preferences?.model_consent===true||careInbox.snapshot().status.model_allowed===true;button.disabled=true;$('#care-preferences-error').textContent='';
    try{if(await careInbox.setEnabled(enabled,{modelAllowed,consentGiven:fields.has('care_model_consent')})){
      if(!sessionGuard.isCurrent(ticket))return;if(enabled&&!modelAllowed)state.preferences={...state.preferences,model_consent:true};await careInbox.load({notify:false});if(!sessionGuard.isCurrent(ticket))return;render();toast(enabled?'已开启。小伴会先判断是否有必要，再来关心。':'主动关心已关闭，已有消息会保留。');
    }}catch(error){if(sessionGuard.isCurrent(ticket)&&form.isConnected)$('#care-preferences-error').textContent=friendlyError(error);}
    finally{if(sessionGuard.isCurrent(ticket)&&form.isConnected)button.disabled=false;}
  };
}
function bindCareSource(){const button=$('[data-care-source]');if(button)button.onclick=()=>{const item=state.threads.find(t=>t.id===button.dataset.careSource);if(!item)return;state.page='threads';render();openThread(item);};}
function bindCareAdmin(){
  const authorized=ticket=>state.dashboard?.user?.is_admin===true&&(!ticket||sessionGuard.isCurrent(ticket));
  if(!authorized())return;
  const careForm=$('#admin-care-form'),pushForm=$('#admin-native-push-form');
  if(careForm)careForm.onsubmit=async event=>{event.preventDefault();if(!authorized())return;const ticket=sessionGuard.capture(),button=$('button[type="submit"]',careForm);button.disabled=true;$('#admin-care-error').textContent='';$('#admin-care-result').textContent='';
    try{const fields=new FormData(careForm),payload=careConfigPayload({enabled:fields.has('enabled'),analysis_interval_minutes:fields.get('analysis_interval_minutes'),cooldown_minutes:fields.get('cooldown_minutes'),daily_limit:fields.get('daily_limit')});const result=await api('/admin/care-config',{method:'PATCH',body:JSON.stringify(payload),sessionToken:ticket.token});if(!authorized(ticket)||!careForm.isConnected)return;state.adminCareConfig=result;$('#admin-care-result').textContent='分析频率已保存。没有需要注意的事情时不会发消息。';}
    catch(error){if(authorized(ticket)&&careForm.isConnected)$('#admin-care-error').textContent=friendlyError(error);}finally{if(authorized(ticket)&&careForm.isConnected)button.disabled=false;}
  };
  const check=$('#admin-care-check');if(check)check.onclick=async()=>{if(!authorized())return;const ticket=sessionGuard.capture();check.disabled=true;$('#admin-care-error').textContent='';$('#admin-care-result').textContent='正在检查当前账号…';
    try{const result=await api('/admin/care-check',{method:'POST',body:'{}',timeoutMs:65000,sessionToken:ticket.token});if(!authorized(ticket)||!check.isConnected)return;await careInbox.load();if(!authorized(ticket)||!check.isConnected)return;$('#admin-care-result').textContent=result.created?'已新增一条关心，可在“关心消息”查看并回复。':'本次没有新增关心消息。个人授权、免打扰、发信上限或 AI 判断都可能让小伴保持安静。';}
    catch(error){if(authorized(ticket)&&check.isConnected){$('#admin-care-result').textContent='';$('#admin-care-error').textContent=friendlyError(error);}}finally{if(authorized(ticket)&&check.isConnected)check.disabled=false;}
  };
  if(pushForm)pushForm.onsubmit=async event=>{event.preventDefault();if(!authorized())return;const ticket=sessionGuard.capture(),button=$('button[type="submit"]',pushForm);button.disabled=true;$('#admin-native-push-error').textContent='';$('#admin-native-push-result').textContent='';
    try{const fields=new FormData(pushForm),options={};for(const [group,names] of Object.entries(nativeClientFields)){options[group]={};for(const name of Object.keys(names))options[group][name]=String(fields.get(`sdk_${group}_${name}`)||'');}const payload=pushConfigPayload({enabled:fields.has('enabled'),provider:fields.get('provider'),app_key:fields.get('app_key'),master_secret:fields.get('master_secret'),clear_master_secret:fields.has('clear_master_secret'),client_options:options});const result=await api('/admin/push-config',{method:'PATCH',body:JSON.stringify(payload),sessionToken:ticket.token});if(!authorized(ticket)||!pushForm.isConnected)return;state.adminPushConfig=result;$('[name="master_secret"]',pushForm).value='';$('[name="clear_master_secret"]',pushForm).checked=false;$('[name="enabled"]',pushForm).checked=result.enabled===true;$('[name="master_secret"]',pushForm).placeholder=result.master_secret_configured?'已保存，留空保留':'尚未配置';$('#admin-native-push-result').textContent='推送配置已保存。实际系统通知仍需手机授权及通道可用；应用内消息不受影响。';}
    catch(error){if(authorized(ticket)&&pushForm.isConnected)$('#admin-native-push-error').textContent=friendlyError(error);}finally{if(authorized(ticket)&&pushForm.isConnected)button.disabled=false;}
  };
  const pushTest=$('#admin-native-push-test');if(pushTest)pushTest.onclick=async()=>{if(!authorized())return;const ticket=sessionGuard.capture();pushTest.disabled=true;$('#admin-native-push-error').textContent='';$('#admin-native-push-result').textContent='正在向当前账号已注册的手机发送测试请求…';
    try{const result=await api('/admin/push-test',{method:'POST',body:'{}',sessionToken:ticket.token});if(!authorized(ticket)||!pushTest.isConnected)return;$('#admin-native-push-result').textContent=Number(result.sent)>0?`推送平台已接受 ${Number(result.sent)} 台设备的测试请求，请在手机上确认；这不代表已经送达。`:result.status==='not_configured'?'系统推送尚未配置或未启用，请先保存有效的 AppKey 和服务端密钥。':result.status==='no_devices'?'当前账号尚未绑定可推送的安卓设备，请先在手机登录并允许通知。':'平台暂未接受测试请求，请检查配置、手机通知权限与平台通道状态。';}
    catch(error){if(authorized(ticket)&&pushTest.isConnected){$('#admin-native-push-result').textContent='';$('#admin-native-push-error').textContent=friendlyError(error);}}finally{if(authorized(ticket)&&pushTest.isConnected)pushTest.disabled=false;}
  };
}

async function checkNewMessages(){
  if(!state.token||polling||document.visibilityState==='hidden')return;
  polling=true;const ticket=sessionGuard.capture();
  try{
    const ready=dueThreads(state.threads,state.preferences,state.reminders);
    if(ready.length){const dueTicket=dueGate.begin();if(dueTicket){try{await api('/evaluate',{method:'POST',body:'{}',sessionToken:ticket.token});}finally{dueGate.finish(dueTicket);}}}
    if(!sessionGuard.isCurrent(ticket))return;
    const rows=items(await api('/reminders',{sessionToken:ticket.token}));
    if(!sessionGuard.isCurrent(ticket))return;
    const additions=reminderTracker.update(rows);state.reminders=rows;
    await careInbox.load();if(sessionGuard.isCurrent(ticket)&&state.page==='care')await careInbox.refreshSelected();
    if(!sessionGuard.isCurrent(ticket))return;
    if(additions.length&&state.page==='today'&&!state.sending&&!$('#modal').open)render();
    for(const r of additions)if(!browserPushState.enabled&&'Notification'in window&&Notification.permission==='granted')new Notification('小伴有一条陪伴消息',{body:'打开小伴，看看你约定的事情。',tag:r.id,icon:'/favicon.svg'});
  }catch{/* Visible actions report connectivity errors; passive checks stay quiet. */}
  finally{polling=false;}
}
setInterval(checkNewMessages,60000);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')checkNewMessages();});
window.addEventListener('storage',event=>{if(event.key==='companion_token'&&event.newValue!==state.token)logout({revoke:false,clearStorage:false,cleanupPush:false});});
if(state.token){loadData().then(valid=>{if(valid){render();reminderTracker.reset(state.reminders);}}).catch(e=>{if(state.token){renderAuth();toast(friendlyError(e),true);}});}else renderAuth();
