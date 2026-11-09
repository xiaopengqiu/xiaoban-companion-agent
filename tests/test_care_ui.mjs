import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import * as careUI from '../web/care-ui.mjs';
import {SessionGuard,ReminderTracker} from '../web/lifecycle.mjs';
import {DueCheckGate} from '../web/companion-ui.mjs';
import {CareInbox,careConfigPayload,pushConfigPayload,renderCareInbox,renderCareAdmin,careStatusText} from '../web/care-ui.mjs';

const thread=(id='care-a')=>({id,title:'面试之后',preview:'今天感觉怎样？',unread:true,message_count:1,created_at:'2026-11-08T10:00:00Z',updated_at:'2026-11-08T10:00:00Z',source:'ai'});
const detail=(id='care-a',extra={})=>({thread:thread(id),messages:[{id:'m1',role:'assistant',content:'今天感觉怎样？',created_at:'2026-11-08T10:00:00Z'}],reply_status:'complete',...extra});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture(serve){const calls=[];let nonce=0;const inbox=new CareInbox({request:async(path,options={})=>{calls.push({path,...options});return serve(path,options,calls);},requestId:()=>`request-${++nonce}`});inbox.reset('account-a');return {inbox,calls};}
const standard=async(path)=>path==='/care'?{items:[thread()],unread_count:1,status:{enabled:true,model_allowed:true,available:true,reason_code:'not_due'}}:path==='/care/preferences'?{enabled:true}:path.endsWith('/read')?{read:true}:detail(path.split('/')[2]);

test('no-contact analysis produces an empty inbox without a made-up companion message',async()=>{
  const {inbox,calls}=fixture(async path=>path==='/care'?{items:[],unread_count:0,status:{enabled:true,model_allowed:true,available:true,reason_code:'not_due'}}:{enabled:true});
  await inbox.load();const view=inbox.snapshot();assert.equal(view.items.length,0);assert.equal(view.unread_count,0);
  const html=renderCareInbox(view,{model_consent:true});assert.match(html,/暂时没有新的关心/);assert.doesNotMatch(html,/今天感觉怎样/);
  assert.deepEqual(calls.map(c=>c.path),['/care','/care/preferences']);
});
test('historical thread provenance is escaped and navigation requires an exact owned id',()=>{
 const source={id:'thread-owned',title:'<img src=x onerror=alert(1)>',revision:1};
 const view={selectedId:'care-a',detail:detail('care-a',{thread:{...thread(),source_thread:source}}),draft:''};
 const linked=careUI.renderCareThread(view,[{id:'thread-owned',title:'renamed',revision:9}]);
 assert.match(linked,/data-care-source="thread-owned"/);assert.doesNotMatch(linked,/<img/);assert.match(linked,/&lt;img/);
 const missing=careUI.renderCareThread(view,[{id:'different',title:source.title,revision:1}]);
 assert.doesNotMatch(missing,/data-care-source/);assert.match(missing,/&lt;img/);
});

test('switching account discards an in-flight private inbox and every draft',async()=>{
  const gate=deferred();const {inbox}=fixture(path=>path==='/care'?gate.promise:{enabled:true});
  const loading=inbox.load();inbox.setDraft('private unsent','care-a');inbox.reset('account-b');
  gate.resolve({items:[thread()],unread_count:1,status:{enabled:true}});await loading;
  assert.equal(inbox.snapshot().items.length,0);assert.equal(inbox.snapshot().selectedId,null);assert.equal(inbox.snapshot().draft,'');
});

test('late selection response cannot replace the thread opened afterward or mark it read',async()=>{
  const a=deferred();const {inbox,calls}=fixture((path,options)=>path==='/care/care-a'?a.promise:standard(path,options));
  const first=inbox.open('care-a');await inbox.open('care-b');a.resolve(detail('care-a'));await first;
  assert.equal(inbox.snapshot().selectedId,'care-b');assert.equal(inbox.snapshot().detail.thread.id,'care-b');
  assert.equal(calls.filter(c=>c.path==='/care/care-a/read').length,0);assert.equal(calls.filter(c=>c.path==='/care/care-b/read').length,1);
});

test('drafts survive polling and switching between care threads',async()=>{
  const {inbox}=fixture(standard);await inbox.load();await inbox.open('care-a');inbox.setDraft('想慢慢聊');
  await inbox.load();assert.equal(inbox.snapshot().draft,'想慢慢聊');await inbox.open('care-b');inbox.setDraft('另一个草稿');
  await inbox.open('care-a');assert.equal(inbox.snapshot().draft,'想慢慢聊');
});

test('mark-read updates list and unread count only after the authenticated request succeeds',async()=>{
  const read=deferred();const {inbox}=fixture(path=>path.endsWith('/read')?read.promise:standard(path));
  await inbox.load();const opening=inbox.open('care-a');await Promise.resolve();await Promise.resolve();
  assert.equal(inbox.snapshot().unread_count,1);read.resolve({read:true});await opening;
  assert.equal(inbox.snapshot().items[0].unread,false);assert.equal(inbox.snapshot().unread_count,0);
});

test('unknown network outcome reuses the same request ID and content on retry',async()=>{
  let attempts=0;const {inbox,calls}=fixture((path,options)=>{
    if(path.endsWith('/reply')){if(++attempts===1)throw Error('连接暂时不可用');return detail('care-a');}return standard(path,options);
  });await inbox.open('care-a');inbox.setDraft('后来有点紧张');assert.equal(await inbox.send(),false);
  assert.equal(inbox.snapshot().draft,'后来有点紧张');assert.ok(inbox.snapshot().pendingRequest);
  assert.equal(await inbox.retry(),true);const posts=calls.filter(c=>c.path.endsWith('/reply')).map(c=>JSON.parse(c.body));
  assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);assert.equal(inbox.snapshot().draft,'');
});

test('an invalid reply response does not claim success or erase the retryable draft',async()=>{
  const {inbox}=fixture(path=>path.endsWith('/reply')?{}:standard(path));await inbox.open('care-a');
  inbox.setDraft('需要确认的回复');assert.equal(await inbox.send(),false);
  assert.equal(inbox.snapshot().draft,'需要确认的回复');assert.equal(inbox.snapshot().pendingRequest.id,'request-1');
});

test('pending model response keeps the saved user message and retries without a duplicate turn',async()=>{
  let attempts=0;const saved={id:'u1',role:'user',content:'其实还好',request_id:'request-1',reply_status:'pending'};
  const {inbox,calls}=fixture(path=>path.endsWith('/reply')?(++attempts===1?detail('care-a',{messages:[saved],reply_status:'pending'}):detail('care-a',{messages:[{...saved,reply_status:'complete'},{id:'a2',role:'assistant',content:'愿意说说后来吗？'}]})):standard(path));
  await inbox.open('care-a');inbox.setDraft('其实还好');await inbox.send();
  assert.equal(inbox.snapshot().draft,'');assert.equal(inbox.snapshot().pendingRequest.saved,true);assert.equal(inbox.snapshot().detail.messages[0].content,'其实还好');
  inbox.setDraft('新的想法先留在草稿');assert.equal(await inbox.send(),false);await inbox.retry();
  assert.equal(inbox.snapshot().draft,'新的想法先留在草稿');assert.equal(inbox.snapshot().pendingRequest,null);
  assert.equal(calls.filter(c=>c.path.endsWith('/reply')).length,2);
});

test('pending reply can be recovered from a server snapshot after refresh',async()=>{
  const {inbox,calls}=fixture(path=>path.endsWith('/read')?{read:true}:path.endsWith('/reply')?detail():detail('care-a',{reply_status:'pending',messages:[{id:'u1',role:'user',content:'今天很累',request_id:'saved-request',reply_status:'pending'}]}));
  await inbox.open('care-a');assert.equal(inbox.snapshot().pendingRequest.id,'saved-request');await inbox.retry();
  assert.equal(JSON.parse(calls.find(c=>c.path.endsWith('/reply')).body).request_id,'saved-request');
});

test('a reply arriving after the user types more does not erase the new draft',async()=>{
  const response=deferred();const {inbox}=fixture(path=>path.endsWith('/reply')?response.promise:standard(path));await inbox.open('care-a');
  inbox.setDraft('第一句');const sending=inbox.send();inbox.setDraft('第二句先记在这里');response.resolve(detail());await sending;
  assert.equal(inbox.snapshot().draft,'第二句先记在这里');
});

test('a stale reply cannot repopulate another account after logout',async()=>{
  const response=deferred();const {inbox}=fixture(path=>path.endsWith('/reply')?response.promise:standard(path));await inbox.open('care-a');
  inbox.setDraft('private');const sending=inbox.send();inbox.reset(null);response.resolve(detail());await sending;
  assert.equal(inbox.snapshot().detail,null);assert.equal(inbox.snapshot().pendingRequest,null);assert.equal(inbox.snapshot().draft,'');
});

test('polling resolves an unknown completed request without leaving a duplicate-send draft',async()=>{
  let completed=false;const {inbox}=fixture(path=>path.endsWith('/reply')?Promise.reject(Error('network')):path.endsWith('/read')?{read:true}:completed?detail('care-a',{messages:[{id:'u1',role:'user',request_id:'request-1',content:'原来的回复',reply_status:'complete'},{id:'a1',role:'assistant',content:'记下了。'}]}):detail());
  await inbox.open('care-a');inbox.setDraft('原来的回复');await inbox.send();assert.ok(inbox.snapshot().pendingRequest);
  completed=true;await inbox.refreshSelected();assert.equal(inbox.snapshot().pendingRequest,null);assert.equal(inbox.snapshot().draft,'');
});

test('a read snapshot without a receipt does not discard an unknown request ID',async()=>{
  const {inbox}=fixture(path=>path.endsWith('/reply')?Promise.reject(Error('network')):path.endsWith('/read')?{read:true}:detail());
  await inbox.open('care-a');inbox.setDraft('还未确认');await inbox.send();await inbox.refreshSelected();
  assert.equal(inbox.snapshot().pendingRequest.id,'request-1');assert.equal(inbox.snapshot().draft,'还未确认');
});

test('care opt-in needs explicit model consent, and disabling care leaves chat consent unchanged',async()=>{
  const {inbox,calls}=fixture(path=>path==='/care/preferences'?{enabled:false}:{});
  await assert.rejects(()=>inbox.setEnabled(true,{modelAllowed:false,consentGiven:false}),/授权/);assert.equal(calls.length,0);
  await inbox.setEnabled(true,{modelAllowed:false,consentGiven:true});
  assert.equal(calls[0].path,'/preferences');assert.deepEqual(JSON.parse(calls[0].body),{model_consent:true});
  assert.equal(calls[1].path,'/care/preferences');calls.length=0;await inbox.setEnabled(false,{modelAllowed:true});
  assert.deepEqual(calls.map(c=>c.path),['/care/preferences']);
});

test('care frequency is validated independently from send cooldown and maximum messages',()=>{
  assert.deepEqual(careConfigPayload({enabled:true,analysis_interval_minutes:'30',cooldown_minutes:'240',daily_limit:'2'}),{enabled:true,analysis_interval_minutes:30,cooldown_minutes:240,daily_limit:2});
  for(const values of [{analysis_interval_minutes:14},{cooldown_minutes:0},{daily_limit:6},{analysis_interval_minutes:2.3}])assert.throws(()=>careConfigPayload({enabled:true,analysis_interval_minutes:120,cooldown_minutes:240,daily_limit:2,...values}));
});

test('push payload keeps secrets write-only and limits public client parameters',()=>{
  const payload=pushConfigPayload({enabled:true,provider:'jpush',app_key:'1234',master_secret:'   ',client_options:{firebase:{project_id:'demo'},manufacturers:{huawei_app_id:'123'}}});
  assert.equal(Object.hasOwn(payload,'master_secret'),false);assert.equal(payload.client_options.firebase.project_id,'demo');
  assert.equal(pushConfigPayload({...payload,master_secret:'new-secret'}).master_secret,'new-secret');
  assert.equal(pushConfigPayload({...payload,clear_master_secret:true}).clear_master_secret,true);
  assert.throws(()=>pushConfigPayload({...payload,master_secret:'x',clear_master_secret:true}),/清除/);
  assert.throws(()=>pushConfigPayload({...payload,client_options:{firebase:{master_secret:'no'}}}),/参数/);
});

test('care content and notification configuration are escaped and ordinary users have no admin surface',()=>{
  const view={items:[{...thread(),title:'<img onerror=evil()>',preview:'<script>bad</script>'}],status:{},preferences:{enabled:false},selectedId:'care-a',detail:detail('care-a',{messages:[{role:'assistant',content:'<script>bad</script>'}]}),draft:'</textarea><script>bad</script>',unread_count:1};
  const html=renderCareInbox(view,{model_consent:false});assert.doesNotMatch(html,/<script>/);assert.match(html,/&lt;script&gt;/);assert.match(html,/授权/);
  assert.equal(renderCareAdmin({is_admin:false},{enabled:true},{master_secret:'never-display'}),'');
  const admin=renderCareAdmin({is_admin:true},{enabled:true,analysis_interval_minutes:120,cooldown_minutes:240,daily_limit:2},{provider:'jpush',enabled:true,app_key:'public-key',master_secret:'never-display',master_secret_configured:true,client_options:{}});
  assert.doesNotMatch(admin,/never-display/);assert.match(admin,/每次分析不等于每次发信/);assert.match(admin,/已保存/);
});

test('readiness copy does not claim a scheduler or configured model when unavailable',()=>{
  assert.match(careStatusText({status:{reason_code:'model_unconfigured'}}),/暂未就绪/);
  assert.match(careStatusText({status:{reason_code:'quiet_hours'}}),/免打扰/);
  const html=renderCareAdmin({is_admin:true},{analysis_interval_minutes:120,cooldown_minutes:240,daily_limit:2,model_ready:false,scheduler_configured:false},{});
  assert.match(html,/后台定时尚未启用/);assert.match(html,/模型尚未就绪/);
});

function applicationFixture(handler=()=>undefined){
  const calls=[],elements=new Map();let admin=false;
  const context={...careUI,SessionGuard,ReminderTracker,DueCheckGate,AbortController,setTimeout,clearTimeout,
    document:{visibilityState:'visible',querySelector(selector){if(selector.startsWith('meta'))return null;if(!elements.has(selector))elements.set(selector,{});return elements.get(selector);}},localStorage:{getItem:()=> 'test-account'},
    fetch:async(path)=>{calls.push(path);const data=(await handler(path))??(path==='/api/dashboard'?{user:{is_admin:admin}}:path==='/api/care'?{items:[],unread_count:0,status:{enabled:false}}:path==='/api/care/preferences'?{enabled:false}:path==='/api/preferences'||path==='/api/integrations/status'?{}:path.startsWith('/api/admin/')?{enabled:false}:{items:[]});return {ok:true,status:200,json:async()=>data};}};
  vm.createContext(context);const source=readFileSync(new URL('../web/app.js',import.meta.url),'utf8').replace(/^import .*;$/gm,'').split('setInterval(checkNewMessages')[0];
  vm.runInContext(source+';globalThis.load=loadData;globalThis.adminCare=()=>state.adminCareConfig;globalThis.adminPush=()=>state.adminPushConfig;globalThis.careView=()=>careInbox.snapshot();globalThis.careCtrl=careInbox;globalThis.updateCare=updateCareSurface;globalThis.setCarePage=()=>{state.page="care";};globalThis.bindCareAdminUi=bindCareAdmin;',context);
  return {context,calls,elements,setAdmin:value=>{admin=value;}};
}
test('the actual application loads care without asking ordinary users for admin resources',async()=>{
  const {context,calls}=applicationFixture();await context.load();
  assert.ok(calls.includes('/api/care'));assert.ok(calls.includes('/api/care/preferences'));
  assert.equal(calls.some(p=>p.startsWith('/api/admin/')),false);assert.equal(context.careView().loaded,true);
});
test('the actual application reads administrator care/push configuration and clears it on revocation',async()=>{
  const {context,calls,setAdmin}=applicationFixture();setAdmin(true);await context.load();
  assert.ok(calls.includes('/api/admin/care-config'));assert.ok(calls.includes('/api/admin/push-config'));assert.ok(context.adminCare());assert.ok(context.adminPush());
  setAdmin(false);await context.load();assert.equal(context.adminCare(),null);assert.equal(context.adminPush(),null);
});

test('actual passive care rendering leaves the focused composer and scroll position in place',async()=>{
  let data=detail();const {context,elements}=applicationFixture(path=>path==='/api/care/care-a'?data:path.endsWith('/read')?{read:true}:undefined);
  await context.load();context.document.querySelector=selector=>elements.get(selector)||null;await context.careCtrl.open('care-a');context.careCtrl.setDraft('正在输入的草稿');context.setCarePage();
  const input={value:'正在输入的草稿',selectionStart:3,dataset:{}},column={dataset:{careThread:'care-a',careView:'ready'},writes:0,set innerHTML(v){this.writes++;}},messages={scrollTop:80,scrollHeight:500,clientHeight:120,writes:0,set innerHTML(v){this.writes++;}},list={scrollTop:0,writes:0,set innerHTML(v){this.writes++;}};
  context.document.querySelector=selector=>elements.get(selector)||null;context.document.querySelectorAll=()=>[];context.document.activeElement=input;
  for(const [name,node] of Object.entries({'#care-root':{},'#care-unread-count':{textContent:'1 条未读'},'#care-thread':column,'#care-messages':messages,'#care-list':list,'#care-reply-form':{},'#care-reply-input':input,'#care-reply-send':{},'#care-reply-retry':{},'#care-reply-status':{},'#care-thread-title':{},'#care-error':{}}))elements.set(name,node);
  context.updateCare();assert.equal(elements.get('#care-unread-count').textContent,'0 条未读');const before=messages.writes;context.updateCare();assert.equal(messages.writes,before);assert.equal(column.writes,0);assert.equal(input.selectionStart,3);assert.equal(context.document.activeElement,input);
  data=detail('care-a',{messages:[...data.messages,{id:'new',role:'assistant',content:'后来还好吗？'}]});await context.careCtrl.refreshSelected();
  assert.equal(messages.writes,before+1);assert.equal(messages.scrollTop,80);assert.equal(column.writes,0);assert.equal(input.value,'正在输入的草稿');assert.equal(input.selectionStart,3);
});


test('a stale administrator button cannot request privileged resources after role revocation',async()=>{
  const {context,calls,elements,setAdmin}=applicationFixture();setAdmin(true);await context.load();
  const button={isConnected:true};elements.set('#admin-native-push-test',button);context.bindCareAdminUi();
  setAdmin(false);await context.load();const before=calls.length;await button.onclick();
  assert.equal(calls.length,before);
});

test('a late privileged response cannot reappear after administrator access was revoked',async()=>{
  const response=deferred();const {context,elements,setAdmin}=applicationFixture(path=>path==='/api/admin/push-test'?response.promise:undefined);setAdmin(true);await context.load();
  const button={isConnected:true},result={textContent:''};elements.set('#admin-native-push-test',button);elements.set('#admin-native-push-result',result);context.bindCareAdminUi();
  const sending=button.onclick();setAdmin(false);await context.load();response.resolve({status:'queued',sent:1});await sending;
  assert.doesNotMatch(result.textContent,/平台已接受/);
});

 test('admin analysis interval reflects the actual production scheduler cadence',()=>{
 const html=renderCareAdmin({is_admin:true},{enabled:true,analysis_interval_minutes:120,scheduler_interval_minutes:60,min_analysis_interval_minutes:60},null);
 assert.match(html,/name="analysis_interval_minutes" min="60" max="1440" step="60"/);assert.match(html,/每 60 分钟运行一轮/);
 });
