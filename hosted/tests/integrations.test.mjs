import test from 'node:test';
import assert from 'node:assert/strict';
import {OWClient, verifyOWWebhook, sendFCM} from '../worker/integrations.mjs';

const UUID='11111111-1111-4111-8111-111111111111';
const NOW=new Date('2026-11-08T06:00:00Z');
const env={OPEN_WEARABLES_URL:'https://wearables.example',OPEN_WEARABLES_API_KEY:'test-only-api-key',OPEN_WEARABLES_APP_ID:'test-app',OPEN_WEARABLES_APP_SECRET:'test-only-app-secret'};
const source={provider:'apple',source:'HealthKit',device:'Apple Watch'};
const reply=data=>new Response(JSON.stringify(data),{headers:{'content-type':'application/json'}});

test('upstream profile and SDK credentials use pinned 0.11.0 request contracts',async()=>{
  const calls=[];
  const client=new OWClient(env,async(url,init)=>{calls.push({url,init});return reply(url.endsWith('/token')?{access_token:'scoped-jwt',refresh_token:'scoped-refresh'}:{id:UUID});});
  assert.equal((await client.createUser({id:'local-user',name:'小林',email:'test@example.com'})).id,UUID);
  assert.equal((await client.issueSdkTokens(UUID)).access_token,'scoped-jwt');
  assert.equal(calls[0].init.headers['X-Open-Wearables-API-Key'],env.OPEN_WEARABLES_API_KEY);
  assert.deepEqual(JSON.parse(calls[0].init.body),{external_user_id:'local-user',first_name:'小林',email:'test@example.com'});
  assert.deepEqual(JSON.parse(calls[1].init.body),{app_id:env.OPEN_WEARABLES_APP_ID,app_secret:env.OPEN_WEARABLES_APP_SECRET});
  assert.equal(calls[1].url,`https://wearables.example/api/v1/users/${UUID}/token`);
});

test('normalization preserves partial daily buckets and true observed session times',async()=>{
  const queries=[];
  const fetcher=async(url)=>{const u=new URL(url);queries.push(u);let data=[];
    if(u.pathname.endsWith('/activity'))data=[{date:'2026-11-08',steps:1400,source},{date:'2026-11-07',steps:null,source}];
    if(u.pathname.endsWith('/sleep'))data=[{id:'sleep-1',start_time:'2026-11-07T23:00:00+08:00',end_time:'2026-11-08T07:00:00+08:00',sleep_duration_seconds:25200,source}];
    if(u.pathname.endsWith('/workouts'))data=[{id:'workout-1',start_time:'2026-11-08T01:00:00Z',end_time:'2026-11-08T01:30:00Z',source,type:'walking'}];
    if(u.pathname.endsWith('/meals'))data=[{id:'meal-1',timestamp:'2026-11-08T04:00:00Z',calories_kcal:500,water_ml:250,name:'午餐',source}];
    return reply({data,pagination:{has_more:false}});
  };
  const events=await new OWClient(env,fetcher).fetchEvents(UUID,'2026-10-08T00:00:00Z',NOW,NOW,'Asia/Shanghai');
  assert.deepEqual(events.map(e=>e.kind),['steps','sleep','workout','meal','water']);
  assert.equal(events[0].metadata.summary_date,'2026-11-08');
  assert.equal(events[0].metadata.is_complete_day,false);
  assert.equal(events[0].end_at,undefined);
  assert.equal(events[1].value,7);assert.equal(events[1].metadata.duration_measure,'time_asleep');
  assert.equal(events[2].value,30);assert.equal(events[4].value,250);
  assert.equal(queries[0].searchParams.get('end_date'),'2026-11-09T00:00:00.000Z');
  assert.equal(queries[1].searchParams.get('end_date'),NOW.toISOString());
});

test('invalid session windows are rejected instead of becoming health facts',async()=>{
  const fetcher=async url=>reply({data:url.includes('/sleep')?[{id:'bad',start_time:'2026-11-08T02:00:00Z',end_time:'2026-11-08T01:00:00Z',sleep_duration_seconds:1,source}]:[],pagination:{has_more:false}});
  await assert.rejects(new OWClient(env,fetcher).fetchEvents(UUID,'2026-11-01T00:00:00Z',NOW),/时长|时间/);
});

test('unchanged historical daily summaries remain identical across sync times',async()=>{
  const fetcher=async url=>reply({data:url.includes('/activity')?[{date:'2026-11-07',steps:6300,source}]:[],pagination:{has_more:false}});
  const client=new OWClient(env,fetcher);
  const first=await client.fetchEvents(UUID,'2026-11-01T00:00:00Z',NOW,NOW,'Asia/Shanghai');
  const later=new Date(+NOW+3600000);
  const second=await client.fetchEvents(UUID,'2026-11-01T00:00:00Z',later,later,'Asia/Shanghai');
  assert.deepEqual(second,first);
  assert.equal(first[0].metadata.is_complete_day,true);
});

test('unreachable services do not expose secrets and deletion 404 is retry-safe',async()=>{
  const client=new OWClient(env,async()=>new Response('',{status:404}));
  assert.deepEqual(await client.deleteUser(UUID),{deleted:true});
  const broken=new OWClient(env,async()=>{throw new Error('test-only-api-key should never leak');});
  await assert.rejects(broken.createUser({id:'u',name:'林',email:'u@example.com'}),error=>!error.message.includes('api-key')&&error.message.includes('连接'));
  await assert.rejects(new OWClient({...env,OPEN_WEARABLES_APP_SECRET:''},async()=>{assert.fail('No network call without app secret');}).issueSdkTokens(UUID),/配置/);
});

test('cursor pagination is bounded and repeated cursors fail closed',async()=>{
  let count=0;
  const client=new OWClient(env,async()=>{count++;return reply({data:[],pagination:{has_more:true,next_cursor:'same'}});});
  await assert.rejects(client.fetchEvents(UUID,'2026-11-01T00:00:00Z',NOW),/游标|分页/);
  assert.equal(count,2);
});

test('Svix verifies original bytes, timestamp and tampering before routing a user',async()=>{
  const bytes=crypto.getRandomValues(new Uint8Array(32));
  const secret='whsec_'+btoa(String.fromCharCode(...bytes));
  const payload=new TextEncoder().encode(JSON.stringify({type:'sync.completed',data:{user_id:UUID}}));
  const stamp=Math.floor(+NOW/1000).toString(),messageId='test-message';
  const key=await crypto.subtle.importKey('raw',bytes,{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const prefix=new TextEncoder().encode(messageId+'.'+stamp+'.');
  const combined=new Uint8Array(prefix.length+payload.length);combined.set(prefix);combined.set(payload,prefix.length);
  const signature=btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.sign('HMAC',key,combined))));
  const headers=new Headers({'svix-id':messageId,'svix-timestamp':stamp,'svix-signature':'v1,'+signature});
  assert.equal((await verifyOWWebhook(payload,headers,secret,NOW)).data.user_id,UUID);
  await assert.rejects(verifyOWWebhook(new TextEncoder().encode(' {}'),headers,secret,NOW),/签名/);
  await assert.rejects(verifyOWWebhook(payload,headers,secret,new Date(+NOW+301000)),/过期/);
  await assert.rejects(verifyOWWebhook(payload,headers,'',NOW),/配置/);
});

test('FCM is explicitly unconfigured without credentials',async()=>{
  assert.equal((await sendFCM({},['device-token'],'reminder-id',()=>assert.fail('no request expected'))).status,'not_configured');
});

test('FCM signs a real service JWT and sends generic lockscreen text only',async()=>{
  const keys=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
  const der=new Uint8Array(await crypto.subtle.exportKey('pkcs8',keys.privateKey));
  const account={client_email:'test@example.iam.gserviceaccount.com',project_id:'test-project',private_key:'-----BEGIN PRIVATE KEY-----\n'+btoa(String.fromCharCode(...der))+'\n-----END PRIVATE KEY-----'};
  let fcmCalls=0;
  const fetcher=async(url,init)=>{
    if(url==='https://oauth2.googleapis.com/token'){
      const jwt=new URLSearchParams(init.body).get('assertion');const parts=jwt.split('.');
      const sig=Uint8Array.from(atob(parts[2].replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
      assert.equal(await crypto.subtle.verify('RSASSA-PKCS1-v1_5',keys.publicKey,sig,new TextEncoder().encode(parts[0]+'.'+parts[1])),true);
      return reply({access_token:'test-oauth-token'});
    }
    assert.equal(url,'https://fcm.googleapis.com/v1/projects/test-project/messages:send');
    const payload=JSON.parse(init.body);assert.equal(payload.message.data.reminder_id,'reminder-id');
    assert.equal(payload.message.notification.body.includes('睡眠'),false);assert.equal(payload.message.token,'device-token');
    fcmCalls++;return reply({name:'projects/test-project/messages/test-message'});
  };
  const result=await sendFCM({FCM_SERVICE_ACCOUNT_JSON:JSON.stringify(account)},['device-token'],'reminder-id',fetcher);
  assert.equal(result.status,'queued');assert.equal(result.sent,1);assert.equal(fcmCalls,1);
});
