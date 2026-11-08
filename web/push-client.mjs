// Device subscriptions only. Authentication stays in the caller's captured API function.
const unsupported='当前环境不支持后台通知，请用支持推送的独立浏览器打开在线小伴。';
const cancelled='通知开启已取消，请在当前账号下重新操作。';

export function createBackgroundPushClient(environment=globalThis){
  let revision=0,operations=Promise.resolve();
  const hasSharedLock=()=>typeof environment.navigator?.locks?.request==='function';
  const enqueue=task=>{
    const run=()=>hasSharedLock()?environment.navigator.locks.request('xiaoban-background-push',{mode:'exclusive'},task):task();
    const result=operations.then(run,run);operations=result.catch(()=>{});return result;
  };
  function capability(){
    if(environment.location?.protocol!=='https:'||environment.isSecureContext!==true)return {supported:false,reason:'后台通知需要 HTTPS 在线服务，请用独立浏览器打开在线小伴。'};
    if(!environment.Notification||!environment.PushManager||!environment.navigator?.serviceWorker?.register||!environment.navigator.serviceWorker.getRegistration||!hasSharedLock())return {supported:false,reason:unsupported};
    return {supported:true};
  }
  async function subscription(){
    const registration=await environment.navigator.serviceWorker.getRegistration('/');
    return registration?.pushManager?await registration.pushManager.getSubscription():null;
  }
  const ownership=(api,device)=>api('/push/subscriptions/status?endpoint='+encodeURIComponent(device.endpoint));
  async function state(api){
    const support=capability();if(!support.supported)return {...support,enabled:false};
    const result={supported:true,permission:environment.Notification.permission,enabled:false};
    let device;
    try{device=await subscription();}catch{return {supported:false,enabled:false,reason:unsupported};}
    if(result.permission!=='granted'||!device)return result;
    if(!api)return {...result,enabled:true};
    try{
      const remote=await ownership(api,device);
      return {...result,enabled:remote?.subscribed===true};
    }catch{return {...result,reason:'暂时无法确认当前账号的通知订阅，请联网后刷新。'};}
  }
  function applicationKey(value){
    try{
      if(typeof value!=='string'||value.length>100||!/^[-_A-Za-z0-9]+={0,2}$/.test(value))throw Error();
      const normalized=value.replace(/-/g,'+').replace(/_/g,'/');
      const decoded=environment.atob(normalized+'='.repeat((4-normalized.length%4)%4));
      const key=Uint8Array.from(decoded,character=>character.charCodeAt(0));
      if(key.length!==65||key[0]!==4)throw Error();
      return key;
    }catch{throw Error('服务端推送公钥无效，请联系管理员。');}
  }
  async function activeRegistration(registration){
    if(registration.active)return registration;
    const worker=registration.installing||registration.waiting;
    if(!worker)throw Error('后台通知服务尚未准备好，请稍后重试。');
    await new Promise((resolve,reject)=>{
      const finish=error=>{environment.clearTimeout(timer);worker.removeEventListener('statechange',changed);error?reject(error):resolve();};
      const changed=()=>{if(worker.state==='activated')finish();else if(worker.state==='redundant')finish(Error('后台通知服务安装失败，请稍后重试。'));};
      const timer=environment.setTimeout(()=>finish(Error('后台通知服务准备超时，请稍后重试。')),20000);
      worker.addEventListener('statechange',changed);changed();
    });
    return registration;
  }
  async function enable(api,{isCurrent=()=>true}={}){
    const support=capability();if(!support.supported)throw Error(support.reason);
    const ticket=++revision;
    const assertCurrent=()=>{if(ticket!==revision||!isCurrent())throw Error(cancelled);};
    assertCurrent();
    // Invoke immediately from the explicit click, before an API await loses user activation.
    const permission=environment.Notification.permission==='default'?await environment.Notification.requestPermission():environment.Notification.permission;
    assertCurrent();
    if(permission!=='granted')throw Error('通知权限未开启，请在浏览器的网站设置中允许通知。');
    return enqueue(async()=>{
      assertCurrent();
      const config=await api('/push/config');assertCurrent();
      if(config?.configured!==true)throw Error('服务端尚未配置后台通知，请联系管理员。');
      const key=applicationKey(config.public_key);
      const registration=await activeRegistration(await environment.navigator.serviceWorker.register('/sw.js',{scope:'/'}));
      assertCurrent();
      let device=null,created=false;
      try{
        device=await registration.pushManager.getSubscription();assertCurrent();
        if(!device){device=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});created=true;}
        assertCurrent();
        await api('/push/subscriptions',{method:'POST',body:JSON.stringify(device.toJSON())});assertCurrent();
        return {supported:true,permission:'granted',enabled:true};
      }catch(error){
        if(device){
          // A channel is shared by all tabs. Never revoke another account's existing channel.
          let owned=false;try{owned=(await ownership(api,device))?.subscribed===true;}catch{}
          // A newly-created channel cannot have changed owner while this origin lock is held.
          if(owned||created){
            try{await api('/push/subscriptions',{method:'DELETE',body:JSON.stringify({endpoint:device.endpoint})});}catch{}
            try{await device.unsubscribe();}catch{}
          }
        }
        throw error;
      }
    });
  }
  function disable(api){
    revision+=1;
    return enqueue(async()=>{
      if(!environment.navigator?.serviceWorker?.getRegistration)return {enabled:false};
      const device=await subscription();if(!device)return {enabled:false};
      try{
        if((await ownership(api,device))?.subscribed!==true)return {enabled:false};
      }catch{throw Error('通知解绑暂未确认，请联网后重试关闭。');}
      let remoteFailure=null,localFailure=null;
      try{await api('/push/subscriptions',{method:'DELETE',body:JSON.stringify({endpoint:device.endpoint})});}catch(error){remoteFailure=error;}
      // Without a cross-tab lock only remove the authenticated server record.
      if(hasSharedLock())try{await device.unsubscribe();}catch(error){localFailure=error;}
      if(remoteFailure||localFailure)throw Error('通知解绑暂未完全确认，请联网后重试关闭。');
      return {enabled:false};
    });
  }
  async function test(api){
    const support=capability();if(!support.supported)throw Error(support.reason);
    const device=await subscription();if(!device||environment.Notification.permission!=='granted')throw Error('请先在当前浏览器开启后台通知。');
    return api('/push/test',{method:'POST',body:JSON.stringify({endpoint:device.endpoint})});
  }
  return {state,enable,disable,test};
}

const client=createBackgroundPushClient();
export const getBackgroundPushState=api=>client.state(api);
export const enableBackgroundPush=(api,options)=>client.enable(api,options);
export const stopBackgroundPush=api=>client.disable(api);
export const testBackgroundPush=api=>client.test(api);
