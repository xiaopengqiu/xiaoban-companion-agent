'use strict';

self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));

self.addEventListener('push',event=>{
  let payload={};
  try{payload=event.data?.json()||{};}catch{}
  const reminderId=typeof payload.reminder_id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(payload.reminder_id)?payload.reminder_id:'';
  event.waitUntil(self.registration.showNotification('小伴有一条新提醒',{
    body:'打开小伴，查看今天的陪伴消息。',
    icon:'/favicon.svg',
    tag:reminderId?'xiaoban-'+reminderId:'xiaoban-reminder',
    data:{reminder_id:reminderId,path:'/'},
  }));
});

self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil((async()=>{
    const appUrl=new URL('/',self.location.origin).href;
    const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    for(const client of windows){
      try{
        const url=new URL(client.url);
        if(url.origin===self.location.origin&&['/','/index.html'].includes(url.pathname)&&typeof client.focus==='function')return await client.focus();
      }catch{}
    }
    // Push payloads and notification data never choose the navigation destination.
    return self.clients.openWindow(appUrl);
  })());
});
