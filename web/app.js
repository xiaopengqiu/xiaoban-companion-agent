'use strict';
import {SessionGuard, ReminderTracker} from './lifecycle.mjs';
const $ = (s, root = document) => root.querySelector(s);
const app = $('#app');
const cloudRuntime = $('meta[name="companion-runtime"]')?.content === 'cloud';
const state = {token: localStorage.getItem('companion_token'), page: 'today', dashboard: null, memories: [], events: [], reminders: [], messages: [], preferences: null, integrations: null, sending: false, syncWarning:false};
const sessionGuard = new SessionGuard(state.token);
const reminderTracker = new ReminderTracker();
const activeRequests = new Set();
let authRevision = 0;
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
const labels = {steps:'步数', sleep:'睡眠', workout:'运动', meal:'饮食', water:'饮水', mood:'心情', preference:'偏好', goal:'目标', fact:'生活记录', pattern:'行为模式', activity:'活动'};
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
      throw new Error(typeof detail==='string'?detail:`请求未完成（${response.status}）`);
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
async function signIn(data){state.token=data.token;state.syncWarning=false;sessionGuard.replace(state.token);localStorage.setItem('companion_token',state.token);state.page='today';if(await loadData()){reminderTracker.reset(state.reminders);render();}}
async function loadData(){
  const ticket=sessionGuard.capture();
  const read=path=>api(path,{sessionToken:ticket.token});
  let results;
  try{results=await Promise.all([read('/dashboard'),read('/memories'),read('/events'),read('/reminders'),read('/chat'),read('/preferences'),read('/integrations/status')]);}
  catch(error){if(!sessionGuard.isCurrent(ticket))return false;throw error;}
  if(!sessionGuard.isCurrent(ticket))return false;
  [state.dashboard,state.memories,state.events,state.reminders,state.messages,state.preferences,state.integrations]=[results[0],items(results[1]),items(results[2]),items(results[3]),items(results[4]),results[5],results[6]];
  return true;
}
async function refresh(){if(await loadData())render();}
const pageNames={today:'今日陪伴',timeline:'行为时间线',memories:'用户记忆',settings:'提醒与设置'};
function render(){
  if(!state.dashboard)return;
  const user=state.dashboard.user;
  app.innerHTML=`<div class="shell"><aside class="sidebar">${brand()}<nav class="nav" aria-label="主要导航">${Object.entries(pageNames).map(([id,label])=>`<button data-page="${id}" class="${state.page===id?'active':''}" ${state.page===id?'aria-current="page"':''}>${icon({today:'home',timeline:'clock',memories:'memory',settings:'settings'}[id])}<span>${label}</span></button>`).join('')}</nav><div class="side-bottom"><div class="side-note"><strong>小小的改变，也值得记住。</strong>按自己的节奏来，<br>小伴会一直陪你记录。</div><div class="account"><span class="avatar">${esc((user.name||'你').slice(0,1))}</span><div class="account-name">${esc(user.name)}<small>${user.is_demo?'模拟体验账号':cloudRuntime?'云端个人空间':'我的个人空间'}</small></div><button id="logout" aria-label="退出登录" title="退出登录">${icon('logout')}</button></div></div></aside><main class="workspace"><div class="topbar"><div class="breadcrumbs">日常 / <strong>${pageNames[state.page]}</strong></div><div class="top-actions">${user.is_demo?'<span class="pill amber"><i class="dot"></i>模拟数据</span>':''}<span class="pill neutral">${state.dashboard.mode==='model'?'模型已连接':ruleModeLabel}</span><button class="btn secondary small" id="refresh" aria-label="刷新数据">${icon('refresh')}刷新</button></div></div><div class="page-title"><div><h1>${{today:'今天，也照顾好自己。',timeline:'日常的点滴，都在这里。',memories:'越了解，越懂得陪伴。',settings:'用你舒服的方式陪伴。'}[state.page]}</h1><p>${state.page==='today'?`${esc(user.name)}，${localDate(new Date(),{month:'long',day:'numeric',weekday:'long'})} · 这是属于你的日常。`:{timeline:'运动、睡眠和每一顿饭，慢慢拼成你的生活。',memories:'这些是小伴记住的你。你可以随时修改、补充或删除。',settings:'提醒时间、关注的事情，都由你来决定。'}[state.page]}</p></div><button class="btn primary" id="primary-action">${icon('plus')}${state.page==='memories'?'补充记忆':'记录日常'}</button></div><div class="content-grid"><div class="main-column">${{today:renderToday,timeline:renderTimeline,memories:renderMemories,settings:renderSettings}[state.page]()}</div>${renderChat()}</div><footer class="page-footer"><span>小伴 · 认真记住，也轻轻提醒。</span><span>${user.is_demo?'当前为合成演示数据。真实设备需在手机端授权。':'数据来自你的记录和已授权设备。'} · ${esc(user.timezone)}${cloudRuntime?' · 云端账号，记录保存在在线服务':' · 当前本机服务'}</span></footer></main></div>`;
  bindShell();
}
function renderToday(){
  const d=state.dashboard,m=d.metrics||{},insight=d.insights?.[0];
  return `<section class="hero"><div class="hero-copy"><span class="eyebrow">A LITTLE CARE, EVERY DAY</span><h2>${esc(insight?.title||'从今天开始，慢慢认识你。')}</h2><p>${esc(insight?.body||'记录一件小事，或聊聊你想养成的习惯。每一次记录，都会让陪伴更贴近你。')}</p></div><div class="hero-art" aria-hidden="true"><svg viewBox="0 0 150 150"><circle cx="92" cy="63" r="47" fill="#d9e4bf"/><path d="M78 140V65" stroke="#79956c" stroke-width="3"/><path d="M79 103C42 108 30 73 39 66c29-2 42 16 40 37Z" fill="#a8bd8c"/><path d="M79 82C76 48 111 40 119 51c2 22-15 32-40 31Z" fill="#809f72"/><path d="M51 80q21 10 28 25m0-24q12-14 27-20" stroke="#eef2dd" fill="none" stroke-width="1.5"/><path d="M50 140h56" stroke="#abbc90" stroke-width="2"/><circle cx="31" cy="42" r="3" fill="#c2d19e"/><circle cx="124" cy="113" r="4" fill="#c2d19e"/></svg></div></section><section class="metrics" aria-label="今日数据">${[{key:'steps',title:'今日步数',icon:'steps',unit:'步'},{key:'sleep_hours',title:'最近睡眠',icon:'moon',unit:'小时',digits:1},{key:'workout_minutes',title:'今日运动',icon:'activity',unit:'分钟'},{key:'water_ml',title:'今日饮水',icon:'water',unit:'ml'}].map(x=>`<article class="metric panel"><div class="metric-label">${icon(x.icon)}${x.title}</div><div class="metric-value">${number(m[x.key],x.digits||0)}<span>${x.unit}</span></div><div class="metric-foot">${m[x.key]==null?'等待记录或设备同步':d.user.is_demo?'合成演示记录':'已记录数据'}</div></article>`).join('')}</section><section class="panel chart-card"><div class="panel-head"><h2>看见日常的节奏</h2><span class="chart-legend"><i></i>近 7 天步数</span></div>${renderChart(d.series||[])}<div class="chart-foot">${icon('clock')} ${d.last_sync_at?`最近记录：${localDate(d.last_sync_at,{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}`:'还没有记录，连接设备或手动记录后开始积累。'}</div></section><section class="panel reminders-card"><div class="panel-head"><h2>小伴的轻提醒</h2><button id="evaluate" class="text-link">看看有什么新发现 ${icon('arrow')}</button></div>${renderReminders()}<p class="subtle-note">提醒会考虑数据时间、免打扰时段和你的反馈。暂未配置手机推送时，消息会保存在这里。</p></section>`;
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
function renderMemories(){return `<section class="intro-strip">${icon('memory')} 小伴会区分你明确告诉它的事与数据中发现的模式。你修改的内容会优先采用。</section><section class="memory-grid">${state.memories.length?state.memories.map(m=>`<article class="panel memory-card"><span class="pill ${m.category==='pattern'?'amber':''}">${labels[m.category]||esc(m.category)}</span><p>${esc(m.text)}</p><div class="memory-meta"><span>${m.origin==='user'?'你亲口告诉小伴':m.category==='pattern'?'来自行为记录 · 可更正':'日常中记住的事'}${m.evidence_ids?.length?` · ${m.evidence_ids.length} 条依据`:''}</span><div><button data-edit-memory="${esc(m.id)}" aria-label="编辑记忆">${icon('edit')}</button><button data-delete-memory="${esc(m.id)}" aria-label="删除记忆">${icon('trash')}</button></div></div></article>`).join(''):'<div class="panel empty">告诉小伴你的偏好或目标，<br>让每一次陪伴更贴近你。</div>'}</section>`;}
function renderSettings(){const p=state.preferences||{},i=state.integrations||{};return `<section class="panel settings-card"><h2>轻提醒，按你的节奏</h2><form id="preferences-form"><div class="settings-row"><div><h3>开启主动陪伴</h3><p>小伴会依据新记录和你的目标，选择值得提醒的事情。</p></div><label class="switch"><input name="enabled" type="checkbox" ${p.enabled?'checked':''} aria-label="开启主动陪伴"><span></span></label></div><div class="settings-form"><div class="form-row"><div class="form-field"><label for="quiet-start">免打扰开始</label><input id="quiet-start" type="time" name="quiet_start" value="${esc(p.quiet_start||'22:00')}" required></div><div class="form-field"><label for="quiet-end">免打扰结束</label><input id="quiet-end" type="time" name="quiet_end" value="${esc(p.quiet_end||'08:00')}" required></div></div><div class="form-field"><label for="daily-limit">每天最多主动提醒</label><select id="daily-limit" name="daily_limit">${[1,2,3,4,5].map(n=>`<option value="${n}" ${Number(p.daily_limit)===n?'selected':''}>${n} 条</option>`).join('')}</select></div><div class="form-field"><label>你想关注的事情</label><div class="inline-actions">${['sleep','activity','meal'].map(c=>`<label class="check"><input type="checkbox" name="category" value="${c}" ${(p.reminder_categories||[]).includes(c)?'checked':''}>${labels[c]}</label>`).join('')}</div></div><p class="subtle-note">时区：${esc(p.timezone||state.dashboard.user.timezone)}。${cloudRuntime?'云端定时评估尚未开启；关闭页面后的定时提醒暂不可用，可手动检查消息箱。':'提醒由服务端持续检查，需保持服务运行。'}</p><p class="error" role="alert"></p><button type="submit" class="btn primary">保存陪伴偏好</button></div></form></section><section class="panel settings-card"><h2>连接你的日常</h2><p>手机端授权健康数据后，运动和睡眠会同步到个人时间线。支持范围取决于设备和健康平台。</p><div class="integration-state"><span class="pill ${i.open_wearables?.connected?'':'neutral'}">${icon('device')}${i.open_wearables?.connected?'设备账号已绑定':i.open_wearables?.configured?'数据服务已配置':'数据服务未配置'}</span><span class="pill neutral">${i.model?.configured?'语言模型已配置':ruleModeLabel}</span><span class="pill neutral">${i.push?.configured?'手机推送已配置':'消息箱已启用'}</span></div><div class="integration-actions"><button id="connect-wearables" class="btn secondary small">连接 Open Wearables</button><button id="sync-wearables" class="btn soft small">${icon('refresh')}同步已连接数据</button><button id="browser-notifications" class="btn secondary small">开启浏览器提醒</button></div><p class="subtle-note">健康读取与授权需要在 Flutter 手机端完成。浏览器提醒仅在此页面打开时接收新消息。</p></section><section class="panel settings-card"><h2>你的记忆，由你掌握</h2><p>${cloudRuntime?'记录、记忆和提醒保存在在线服务中，使用同一云端账号访问。':'记录、记忆和提醒保存在当前本机服务的数据目录。'}你可以逐条删除，也可以删除整个账号。</p><div class="danger-zone"><button id="delete-account" class="btn danger small">删除账号与全部数据</button><button id="logout-settings" class="btn secondary small">退出登录</button></div></section>`;}
function renderChat(){const messages=state.messages;return `<aside class="panel chat-panel" aria-label="和小伴聊天"><div class="chat-heading"><div class="companion-face" aria-hidden="true">✳</div><div><h2>小伴在这里</h2><p>记得你的日常，也听你说说话。</p></div><span class="dot" title="服务已连接"></span></div><div id="chat-body" class="chat-body" aria-live="polite"><p class="chat-intro">慢慢来，今天想聊些什么？</p>${messages.length?messages.map(m=>`<div class="message ${m.role==='user'?'user':'assistant'}">${esc(m.content)}</div>`).join(''):'<div class="message assistant">你好呀，我是小伴。你可以告诉我一个想养成的习惯，也可以问问最近的运动和睡眠。<br>我会依据已记录的信息陪你一起看看。</div>'}</div><div class="suggestions"><button data-prompt="我最近的睡眠怎么样？">看看最近的睡眠 ${icon('arrow')}</button><button data-prompt="你记得我的哪些偏好和目标？">聊聊你记住的我 ${icon('arrow')}</button></div><form class="chat-form" id="chat-form"><textarea id="chat-input" aria-label="给小伴发消息" placeholder="跟小伴说点什么…" rows="1" maxlength="2000" required></textarea><button type="submit" aria-label="发送消息">${icon('send')}</button></form><div class="chat-foot">${state.dashboard.mode==='model'?'模型会结合你的记录回答':ruleReplyLabel}</div></aside>`;}
function bindShell(){
  if(state.syncWarning)$('.page-title').insertAdjacentHTML('afterend','<p class="intro-strip" role="alert">本地记录已保存；外部语义索引同步尚未完成。恢复服务后重试，当前回答只采用本地有效记忆。</p>');
  const preferenceForm=$('#preferences-form');
  if(preferenceForm){
    $('.settings-form',preferenceForm).insertAdjacentHTML('beforebegin',`<div class="settings-row"><div><h3>允许模型辅助理解</h3><p>启用后，已配置的语言模型和语义索引可处理必要的个人摘要。关闭时使用已保存事实与规则。</p></div><label class="switch"><input name="model_consent" type="checkbox" ${state.preferences?.model_consent?'checked':''} aria-label="允许模型处理个人摘要"><span></span></label></div>`);
    $('.settings-form',preferenceForm).insertAdjacentHTML('afterbegin',`<div class="form-field"><label for="user-timezone">我的时区</label><input id="user-timezone" name="timezone" value="${esc(state.preferences?.timezone||'Asia/Shanghai')}" placeholder="例如 Asia/Shanghai" required maxlength="80"></div>`);
  }
  document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{state.page=b.dataset.page;render();window.scrollTo(0,0);});
  $('#logout').onclick=logout;if($('#logout-settings'))$('#logout-settings').onclick=logout;
  $('#refresh').onclick=async function(){this.disabled=true;try{await refresh();toast('日常已更新');}catch(e){toast(friendlyError(e),true);this.disabled=false;}};
  $('#primary-action').onclick=()=>state.page==='memories'?openMemory():openEvent();
  bindChat();
  if($('#evaluate'))$('#evaluate').onclick=async function(){this.disabled=true;try{const r=await post('/evaluate');await refresh();toast(r.created?`小伴整理了 ${r.created} 条新提醒`:'已检查：暂时没有需要新增的提醒。');}catch(e){toast(friendlyError(e),true);this.disabled=false;}};
  document.querySelectorAll('[data-feedback]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await post(`/reminders/${encodeURIComponent(b.dataset.id)}/feedback`,{action:b.dataset.feedback,minutes:30});await refresh();toast({done:'记下啦，这一小步也值得肯定。',snooze:cloudRuntime?'已设置稍后提醒。云端定时尚未开启，可稍后回到消息箱检查。':'好的，30 分钟后再看看。',dismiss:'今天先跳过，按你的节奏来。',disable:'已关闭这一类提醒。'}[b.dataset.feedback]);}catch(e){toast(friendlyError(e),true);b.disabled=false;}});
  document.querySelectorAll('[data-edit-memory]').forEach(b=>b.onclick=()=>openMemory(state.memories.find(m=>m.id===b.dataset.editMemory)));
  document.querySelectorAll('[data-delete-memory]').forEach(b=>b.onclick=()=>confirmAction('忘记这条记忆？','小伴会删除它，并更新相关的待发提醒。',async()=>{await api(`/memories/${encodeURIComponent(b.dataset.deleteMemory)}`,{method:'DELETE'});await refresh();toast('这条记忆已经删除。');}));
  document.querySelectorAll('[data-delete-event]').forEach(b=>b.onclick=()=>confirmAction('删除这次记录？','依赖这次记录的行为模式和待发提醒也会重新处理。',async()=>{await api(`/events/${encodeURIComponent(b.dataset.deleteEvent)}`,{method:'DELETE'});await refresh();toast('记录已删除。');}));
  if($('#preferences-form'))$('#preferences-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,fields=new FormData(form),button=$('button[type=submit]',form);button.disabled=true;try{await patch('/preferences',{enabled:fields.has('enabled'),model_consent:fields.has('model_consent'),timezone:fields.get('timezone'),quiet_start:fields.get('quiet_start'),quiet_end:fields.get('quiet_end'),daily_limit:Number(fields.get('daily_limit')),reminder_categories:fields.getAll('category')});await refresh();toast('已记住你喜欢的陪伴方式。');}catch(error){errorTarget(form,error);button.disabled=false;}};
  if($('#connect-wearables'))$('#connect-wearables').onclick=async function(){this.disabled=true;try{const r=await post('/integrations/open-wearables/connect');await refresh();toast(r.configured?'设备账号已准备好，请在手机端授权同步。':r.detail||'请先配置服务端的 Open Wearables 连接。',!r.configured);}catch(e){toast(friendlyError(e),true);this.disabled=false;}};
  if($('#sync-wearables'))$('#sync-wearables').onclick=async function(){this.disabled=true;try{const r=await post('/integrations/open-wearables/sync');await refresh();toast(`同步完成，新增 ${r.imported} 条记录。`);}catch(e){toast(friendlyError(e),true);this.disabled=false;}};
  if($('#browser-notifications'))$('#browser-notifications').onclick=async()=>{if(!('Notification'in window)){toast('当前浏览器不支持系统通知。',true);return;}const permission=await Notification.requestPermission();toast(permission==='granted'?'已开启。页面打开时会通知新的陪伴消息。':'浏览器通知未开启，仍可在消息箱查看。');};
  if($('#delete-account'))$('#delete-account').onclick=()=>confirmAction('删除账号与全部数据？','记录、记忆、对话、提醒和登录凭证将被删除。此操作无法撤销。',async()=>{await api('/me',{method:'DELETE'});logout();toast('账号与个人数据已删除。');});
}
function logout({revoke=true,clearStorage=true}={}){const oldToken=state.token;for(const controller of activeRequests)controller.abort();activeRequests.clear();if(clearStorage)localStorage.removeItem('companion_token');state.token=null;sessionGuard.replace(null);state.dashboard=null;state.messages=[];state.memories=[];state.events=[];state.reminders=[];state.preferences=null;state.integrations=null;state.sending=false;state.syncWarning=false;reminderTracker.reset([]);renderAuth();if(revoke&&oldToken){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),5000);fetch('/api/auth/logout',{method:'POST',headers:{Authorization:`Bearer ${oldToken}`},credentials:'same-origin',cache:'no-store',keepalive:true,signal:controller.signal}).catch(()=>{}).finally(()=>clearTimeout(timer));}}
function bindChat(){
  const chat=$('#chat-body');if(!chat)return;chat.scrollTop=chat.scrollHeight;
  const button=$('#chat-form button');button.disabled=state.sending;
  if(state.sending)$('.chat-foot').textContent='小伴正在查看记录…';
  $('#chat-form').onsubmit=async e=>{e.preventDefault();await sendChat($('#chat-input').value);};
  $('#chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chat-form').requestSubmit();}};
  document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>sendChat(b.dataset.prompt));
}
function updateChatPanel(){const panel=$('.chat-panel');if(!panel||!state.dashboard)return;panel.outerHTML=renderChat();bindChat();}
async function sendChat(text){
  const message=text.trim();if(!message||state.sending||!state.token)return;
  const ticket=sessionGuard.capture();state.sending=true;state.messages.push({role:'user',content:message});updateChatPanel();
  try{
    const response=await api('/chat',{method:'POST',body:JSON.stringify({message}),sessionToken:ticket.token});
    if(!sessionGuard.isCurrent(ticket))return;
    state.messages.push({role:'assistant',content:response.reply});
    const history=await api('/chat',{sessionToken:ticket.token}).catch(()=>null);
    if(!sessionGuard.isCurrent(ticket))return;
    if(history)state.messages=items(history);
    const memories=await api('/memories',{sessionToken:ticket.token}).catch(()=>null);
    if(!sessionGuard.isCurrent(ticket))return;
    if(memories)state.memories=items(memories);
    state.sending=false;updateChatPanel();
    const foot=$('.chat-foot');if(foot)foot.textContent=response.mode==='model'?'语言模型 · 结合已授权记录回答':ruleReplyLabel;
    if(response.evidence?.length){const evidence=document.createElement('div');evidence.className='chat-evidence';evidence.textContent='依据：'+[...new Set(response.evidence.map(e=>e.label||e.id))].slice(0,6).join(' · ');$('#chat-body')?.append(evidence);}
  }catch(e){if(sessionGuard.isCurrent(ticket)){state.messages.push({role:'assistant',content:friendlyError(e)});state.sending=false;updateChatPanel();}}
  finally{if(sessionGuard.isCurrent(ticket)){state.sending=false;const button=$('#chat-form button');if(button)button.disabled=false;const container=$('#chat-body');if(container)container.scrollTop=container.scrollHeight;}}
}
function openModal(title,html){$('#modal-content').innerHTML=`<div class="modal-heading"><h2>${esc(title)}</h2><button type="button" id="close-modal" aria-label="关闭">×</button></div>${html}`;$('#close-modal').onclick=()=>$('#modal').close();$('#modal').showModal();}
function confirmAction(title,body,action){openModal(title,`<p class="modal-note">${esc(body)}</p><p class="error" role="alert"></p><div class="modal-actions"><button class="btn secondary" id="cancel-modal">取消</button><button class="btn danger" id="confirm-modal">确认删除</button></div>`);$('#cancel-modal').onclick=()=>$('#modal').close();$('#confirm-modal').onclick=async function(){this.disabled=true;try{await action();$('#modal').close();}catch(e){$('.error',$('#modal')).textContent=friendlyError(e);this.disabled=false;}};}
function openMemory(memory){openModal(memory?'修改这条记忆':'告诉小伴一件事',`<form id="memory-form">${memory?'':`<div class="form-field"><label for="memory-category">属于哪一类</label><select id="memory-category" name="category"><option value="preference">我的偏好</option><option value="goal">我的目标</option><option value="fact">生活记录</option></select></div>`}<div class="form-field"><label for="memory-text">希望小伴记住什么</label><textarea id="memory-text" name="text" required maxlength="1000" placeholder="比如：工作日上午不要提醒我运动">${esc(memory?.text||'')}</textarea></div><p class="modal-note">你明确告诉小伴的内容会优先采用，也可以随时更正。</p><p class="error" role="alert"></p><div class="modal-actions"><button class="btn primary" type="submit">${memory?'保存修改':'记住这件事'}</button></div></form>`);$('#memory-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,fields=new FormData(form),button=$('button',form);button.disabled=true;try{if(memory)await patch(`/memories/${encodeURIComponent(memory.id)}`,{text:fields.get('text')});else await post('/memories',{text:fields.get('text'),category:fields.get('category')});await refresh();$('#modal').close();toast('小伴记住啦。');}catch(error){errorTarget(form,error);button.disabled=false;}};}
function openEvent(){const now=new Date();const localInput=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,16);openModal('记下日常的一小步',`<form id="event-form"><div class="form-row"><div class="form-field"><label for="event-kind">记录什么</label><select id="event-kind" name="kind">${['meal','water','workout','sleep','steps','mood'].map(k=>`<option value="${k}">${labels[k]}</option>`).join('')}</select></div><div class="form-field" id="event-value-field"><label for="event-value" id="event-value-label">数值</label><input id="event-value" name="value" type="number" step="any" min="0" max="100000" placeholder="可选"></div></div><div class="form-field"><label for="event-time">记录时间（本机时区）</label><input type="datetime-local" id="event-time" name="start_at" value="${localInput}" required></div><div class="form-field"><label for="event-note">想补充什么</label><textarea id="event-note" name="note" maxlength="1000" placeholder="午餐吃了什么？今天运动感觉如何？"></textarea></div><p class="error" role="alert"></p><div class="modal-actions"><button class="btn primary" type="submit">保存这次记录</button></div></form>`);
  const config={meal:{label:'餐次',unit:'entry',required:false},water:{label:'饮水量（ml）',unit:'ml',required:true},workout:{label:'运动时长（分钟）',unit:'min',required:true},sleep:{label:'睡眠时长（小时）',unit:'h',required:true},steps:{label:'步数',unit:'count',required:true},mood:{label:'心情评分（1–5）',unit:'score',required:true}};
  function update(){const kind=$('#event-kind').value,c=config[kind];$('#event-value-field').hidden=kind==='meal';$('#event-value-label').textContent=c.label;$('#event-value').required=c.required;$('#event-value').value='';$('#event-value').min=kind==='mood'?'1':'0';$('#event-value').max=kind==='mood'?'5':kind==='sleep'?'24':'100000';$('#event-value').placeholder=kind==='water'?'250':kind==='workout'?'30':kind==='sleep'?'7.5':'';$('#event-note').required=kind==='meal';}$('#event-kind').onchange=update;update();
  $('#event-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,fields=new FormData(form),kind=fields.get('kind'),button=$('button',form);button.disabled=true;const start=new Date(fields.get('start_at'));try{if(Number.isNaN(start.getTime()))throw new Error('请选择有效的记录时间。');await post('/events',{kind,value:kind==='meal'?null:Number(fields.get('value')),unit:config[kind].unit,start_at:start.toISOString(),source:'manual',metadata:{note:fields.get('note')}});await refresh();$('#modal').close();toast('已记下。这一小步也算数。');}catch(error){errorTarget(form,error);button.disabled=false;}};
}
async function checkNewMessages(){if(!state.token)return;const ticket=sessionGuard.capture();try{const rows=items(await api('/reminders',{sessionToken:ticket.token}));if(!sessionGuard.isCurrent(ticket))return;for(const r of reminderTracker.update(rows)){if('Notification'in window&&Notification.permission==='granted')new Notification('小伴有一条新提醒',{body:'打开小伴，看看今天的陪伴消息。',tag:r.id,icon:'/favicon.svg'});}}catch{/* The visible actions report connectivity errors. Polling stays quiet. */}}
setInterval(checkNewMessages,60000);
window.addEventListener('storage',event=>{if(event.key==='companion_token'&&event.newValue!==state.token)logout({revoke:false,clearStorage:false});});
if(state.token){loadData().then(valid=>{if(valid){render();reminderTracker.reset(state.reminders);}}).catch(e=>{if(state.token){renderAuth();toast(friendlyError(e),true);}});}else renderAuth();
