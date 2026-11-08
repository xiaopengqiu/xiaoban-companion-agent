export function memoryGroups(rows){
  const groups=[{id:'stable',title:'相对稳定的你',description:'你的偏好、目标、关系与生活经历。',items:[]},{id:'context',title:'最近的处境',description:'阶段性的事情会到期，避免一直用过去定义现在。',items:[]},{id:'boundary',title:'陪伴的边界',description:'怎样交流、哪些事情不想被主动提起。',items:[]},{id:'pattern',title:'记录中发现的模式',description:'带依据的摘要，可以更正。',items:[]},{id:'expired',title:'已到期，等你重新确认',description:'保留给你查看与修改，不再作为当前理解。',items:[]}];
  for(const row of rows){const key=row.is_active===false?'expired':row.category==='context'?'context':row.category==='boundary'?'boundary':row.category==='pattern'?'pattern':'stable';groups.find(g=>g.id===key).items.push(row);}
  return groups;
}
export function localInput(value){
  if(!value)return '';
  const date=new Date(value);if(!Number.isFinite(+date))return '';
  return new Date(+date-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
}
function instant(value){if(!value)return null;const date=new Date(value);if(!Number.isFinite(+date))throw new Error('请选择有效的时间。');return date.toISOString();}
export function threadPayload(values,original=null){
  const payload={title:String(values.title||'').trim(),details:String(values.details||'').trim(),status:values.status||'active',outcome:String(values.outcome||'').trim(),event_at:instant(values.event_at),follow_up_enabled:!!values.follow_up_enabled};
  if(!payload.title||payload.title.length>120)throw new Error('请填写 1–120 字的事情名称。');
  if(payload.follow_up_enabled&&!values.follow_up_at)throw new Error('请选择希望小伴联系你的跟进时间。');
  if(!original||(payload.follow_up_enabled&&!original.follow_up_enabled)||values.follow_up_at!==localInput(original.follow_up_at))payload.follow_up_at=instant(values.follow_up_at);
  return payload;
}
export function dueThreads(rows,prefs,reminders,now=Date.now()){
  if(!prefs?.enabled||prefs.life_followups_enabled===false)return [];
  const known=new Set(reminders.map(r=>r.dedupe_key));
  return rows.filter(t=>{const due=Date.parse(t.follow_up_at);return t.status==='active'&&t.follow_up_enabled&&Number.isFinite(due)&&due<=now&&now-due<86400000&&!known.has(`life:${t.id}:${t.revision}`);});
}
export class DueCheckGate {
  constructor(){this.reset();}
  reset(){this.generation=(this.generation||0)+1;this.busy=null;this.lastAttempt=-Infinity;}
  begin(now=Date.now()){if(this.busy||now-this.lastAttempt<300000)return null;const ticket={generation:this.generation};this.busy=ticket;this.lastAttempt=now;return ticket;}
  finish(ticket){if(this.busy===ticket)this.busy=null;}
}
export function modelConfigPayload(values){
  const result={mode:values.mode||'disabled',api_url:String(values.api_url||'').trim(),model:String(values.model||'').trim(),local_provider:values.local_provider||'codex',local_model:String(values.local_model||'').trim(),gateway_url:String(values.gateway_url||'').trim()};
  for(const name of ['api_key','gateway_key'])if(String(values[name]||'').trim())result[name]=String(values[name]).trim();
  return result;
}
