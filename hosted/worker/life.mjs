/* Confirmed personal context and explicit life-event follow-up policy. */
const DAY=86400000;
export class PersonalError extends Error {constructor(message){super(message);this.status=422;}}
const fail=message=>{throw new PersonalError(message);};
export const memoryCategories=['fact','preference','goal','pattern','context','relationship','boundary'];
const has=(data,key)=>Object.prototype.hasOwnProperty.call(data,key);
function text(value,label,max,min=0){if(typeof value!=='string'||value.trim().length<min||value.length>max)fail('请检查'+label);return value.trim();}
function instant(value){
  const parts=typeof value==='string'&&value.match(/^(\d{4}-\d{2}-\d{2})[Tt](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?(?:[Zz]|[+-]\d{2}:\d{2})$/);
  if(!parts||parts[1].startsWith('0000')||Number(parts[2])>23||Number(parts[3])>59||Number(parts[4]||0)>59||!Number.isFinite(Date.parse(value)))fail('时间须包含有效时区');
  const day=new Date(parts[1]+'T00:00:00Z');if(!Number.isFinite(+day)||day.toISOString().slice(0,10)!==parts[1])fail('日历日期无效');
  return new Date(value).toISOString();
}
export function activeMemory(memory,now){return !memory.expires_at||(Number.isFinite(Date.parse(memory.expires_at))&&Date.parse(memory.expires_at)>+now);}
export function memoryView(memory,now){return {...memory,expires_at:memory.expires_at??null,is_active:activeMemory(memory,now)};}
export function memoryInput(data,old,now){
  if(Object.keys(data).some(key=>!['text','category','expires_at'].includes(key)))fail('记忆字段无效');
  if(old&&!Object.keys(data).length)fail('请填写需要修改的内容');
  const result={...old};
  if(!old||has(data,'text'))result.text=text(data.text,'记忆内容',2000,1);
  if(!old||has(data,'category')){result.category=has(data,'category')?data.category:'preference';if(!memoryCategories.includes(result.category))fail('记忆类别无效');}
  if(has(data,'expires_at'))result.expires_at=data.expires_at===null?null:instant(data.expires_at);
  else if(!old)result.expires_at=result.category==='context'?new Date(+now+7*DAY).toISOString():null;
  return result;
}
export function threadInput(data,old,now){
  const keys=['title','details','status','event_at','follow_up_at','follow_up_enabled','outcome'];
  if(Object.keys(data).some(key=>!keys.includes(key))||!Object.keys(data).length)fail('重要事情字段无效');
  const item=old?{...old}:{title:'',details:'',status:'active',event_at:null,follow_up_at:null,follow_up_enabled:false,outcome:''};
  if(!old||has(data,'title'))item.title=text(data.title,'事情标题',120,1);
  for(const key of ['details','outcome'])if(has(data,key))item[key]=text(data[key],key==='details'?'事情说明':'后续情况',2000);
  if(has(data,'status')){if(!['active','paused','completed'].includes(data.status))fail('事情状态无效');item.status=data.status;}
  for(const key of ['event_at','follow_up_at'])if(has(data,key))item[key]=data[key]===null?null:instant(data[key]);
  if(has(data,'follow_up_enabled')){if(typeof data.follow_up_enabled!=='boolean')fail('跟进开关无效');item.follow_up_enabled=data.follow_up_enabled;}
  if(item.follow_up_enabled){
    if(!item.follow_up_at)fail('开启跟进需要明确选择时间');
    if(!old?.follow_up_enabled&&!has(data,'follow_up_at'))fail('开启跟进需要重新确认时间');
    if(!old?.follow_up_enabled||has(data,'follow_up_at')){
      const at=Date.parse(item.follow_up_at);if(at<+now-300000||at>+now+180*DAY)fail('跟进时间须在未来180天内');
    }
  }
  return item;
}
export function lifeChoices(threads,now,prefs){
  if(prefs.life_followups_enabled===false)return [];
  return threads.filter(t=>t.status==='active'&&t.follow_up_enabled&&t.follow_up_at&&Date.parse(t.follow_up_at)<=+now&&+now<Date.parse(t.follow_up_at)+DAY)
    .sort((a,b)=>Date.parse(a.follow_up_at)-Date.parse(b.follow_up_at))
    .map(t=>({category:'life',title:'你在意的事，小伴还记着',body:`你之前希望跟进“${t.title}”。现在愿意接着聊聊吗？也可以稍后再说。`,evidence_ids:[t.id],dedupe_key:`life:${t.id}:${t.revision}`,expires_at:new Date(Date.parse(t.follow_up_at)+DAY).toISOString()}));
}
export function sharingSuggestions(message,now){
  if(avoidsPersonalTopic(message))return [];
  if(message.length<12||message.length>3000||!/^(?:(?:其实|说实话|最近|这周|下周|今天|昨天|这几天|本周|这段时间|现在|目前)[，,\s]*)?我/.test(message)||!/(最近|这周|下周|面试|备考|项目|准备|担心)/.test(message))return [];
  if(/^(?:请)?(?:帮我)?记住/.test(message)||/(比如|例如|假如|假设|如果|要是|朋友说|朋友最近|我朋友|我的朋友|他最近|她最近)/.test(message))return [];
  if(/(?:不要|别|不想|不愿|无需).{0,16}(?:记|存|跟进)|(?:不|没)(?:再)?(?:打算|准备|计划)|(?:没有|不再有).{0,8}(?:计划|安排)|(?:取消|放弃).{0,8}(?:计划|面试|项目)/.test(message))return [];
  if(/步数|喝了多少|睡了多久|睡眠时长|运动了多久|多少毫升|(?:睡了|睡眠|喝水|运动).{0,8}(?:多少|几|多久)/.test(message))return [];
  const excerpt=message.slice(0,1000);
  return [{type:'memory',text:excerpt,category:'context',expires_at:new Date(+now+7*DAY).toISOString()},
    {type:'thread',title:excerpt.slice(0,120),details:excerpt,follow_up_enabled:false}];
}
export const avoidsPersonalTopic=message=>/(?:不想|不要|别|先不|不再).{0,5}(?:提|聊)|不提|不聊这|先不说|暂时不想说/.test(message);
export function relevantPersonalContext(message,memories,threads){
  const excluded=avoidsPersonalTopic(message);
  const stop=new Set(['我想','我在','我们','的事','这件','那件','事情','今天','之前','继续','聊聊','一起','最近','现在','这周']);
  const tokens=[...(message.match(/[\u4e00-\u9fff]+/g)||[]).flatMap(part=>Array.from({length:Math.max(0,part.length-1)},(_,i)=>part.slice(i,i+2))),...(message.toLowerCase().match(/[a-z0-9]{2,}/g)||[])].filter(t=>!stop.has(t));
  const match=text=>tokens.some(t=>text.toLowerCase().includes(t));
  const resume=/聊聊近况|最近的事|接着聊|继续聊(?:之前|上次|那件|这件)|上次的事|正在发生/.test(message);
  const related=excluded?[]:threads.filter(t=>t.status==='active'&&(match(t.title+' '+t.details)||resume)).slice(-3);
  const context=excluded?[]:memories.filter(m=>m.origin==='user'&&['context','boundary','relationship'].includes(m.category)&&match(m.text)).slice(-2);
  return {memories:context,threads:related};
}
export function personalReply(message,memories,threads,style){
  const {memories:context,threads:related}=relevantPersonalContext(message,memories,threads);
  const anchor=related.length?`你记下的“${related.at(-1).title}”还在进行中。`:context.length?`你之前确认过：“${context.at(-1).text.slice(0,160)}”。`:'';
  if(style==='listen')return anchor+'我在听，你可以慢慢说。你现在最想让我听见的是哪一部分？';
  if(style==='plan')return anchor+'我们先选一个你现在愿意做的小步骤：你希望解决哪件具体事情？你确认后，我们可以一起把它拆小。';
  return anchor+'我们可以一起理一理：发生了什么、你最在意什么、目前有哪些选择？先从你愿意说的部分开始。';
}
