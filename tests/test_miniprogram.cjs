const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {createClient,address,eligible} = require('../miniprogram/lib/api');
const A='11111111-1111-4111-8111-111111111111', B='22222222-2222-4222-8222-222222222222';
function platform() {
  const calls=[], storage={};
  return {calls,storage,request(options){calls.push(options);},getStorageSync:k=>storage[k],setStorageSync(k,v){storage[k]=v;}};
}
const client = wx => createClient(wx,'http://127.0.0.1:8876','test-credential',{devtools:true});
const answer=(call,data,statusCode=200)=>call.success({data,statusCode});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('transport enforces HTTPS except explicit simulator loopback; no URL credentials/path',()=>{
  for(const url of ['http://192.168.1.3:8765','http://127.0.0.1:8765','https://user:key@host','https://host/path','https://host?token=key','https://host:0']) assert.throws(()=>address(url,false));
  assert.equal(address(' https://relay.example:8765/ ',false),'https://relay.example:8765');
  assert.throws(()=>address('http://192.168.1.3',true));
});
test('reads use exact shared endpoints and credential header; never credential query/storage',async()=>{
  const wx=platform(), api=client(wx);
  const p=api.threads();assert.equal(wx.calls[0].url,'http://127.0.0.1:8876/api/threads');
  assert.equal(wx.calls[0].header['X-Resume-Token'],'test-credential');answer(wx.calls[0],[{id:A,title:'任务'}]);
  assert.deepEqual(await p,[{id:A,title:'任务'}]);assert.deepEqual(wx.storage,{});
  const q=api.check(A);answer(wx.calls[1],{threadId:B,canMonitor:true});await assert.rejects(q,/不匹配/);
});
test('invalid data, auth errors, and read failures do not silently look successful',async()=>{
  const wx=platform(), api=client(wx);
  const p=api.watches();answer(wx.calls[0],{});await assert.rejects(p,/格式/);
  const q=api.quota();answer(wx.calls[1],{error:'凭据失效'},401);await assert.rejects(q,e=>e.status===401);
  const r=api.threads();wx.calls[2].fail({errMsg:'secret internal message'});await assert.rejects(r,e=>!e.message.includes('secret'));
});
test('eligibility fails closed for completed, unknown, history-only resume claims',()=>{
  assert.equal(eligible({canMonitor:true,taskState:'running',decision:'wait'}),true);
  for(const row of [null,{}, {canMonitor:'true',taskState:'running',decision:'wait'}, {canMonitor:true,taskState:'idle',decision:'resume'},{canMonitor:true,taskState:'running',decision:'stop'}]) assert.equal(eligible(row),false);
});
test('start needs valid UUID, budget, explicit consent; duplicate writes blocked',async()=>{
  const wx=platform(), api=client(wx);
  assert.throws(()=>api.start('bad',1,true));
  for(const [n,c] of [[0,true],[1.2,true],[1,false],[101,true]]) await assert.rejects(api.start(A,n,c));
  assert.equal(wx.calls.length,0);
  const p=api.start(A,1,true);assert.deepEqual(wx.calls[0].data,{threadId:A,maxResumes:1,confirmed:true});
  assert.equal(api.pending().threadId,A);await assert.rejects(api.start(A,1,true));await assert.rejects(api.stop(A));
  answer(wx.calls[0],{threadId:A,status:'waiting_quota'});await p;assert.equal(api.pending(),null);
});
test('ambiguous POST is durable across clients, never retried automatically',async()=>{
  const wx=platform(), api=client(wx);
  const p=api.start(A,1,true);wx.calls[0].fail({errMsg:'timeout'});await assert.rejects(p,e=>e.uncertain);
  const reopened=client(wx);await assert.rejects(reopened.start(A,1,true));assert.equal(wx.calls.length,1);
  reopened.acknowledgePending();const stopped=reopened.stop(A);answer(wx.calls[1],{stopped:A});await stopped;
  assert.equal(reopened.pending(),null);assert.ok(!JSON.stringify(wx.storage).includes('test-credential'));
});
test('HTTP 409/5xx stay uncertain, explicit rejected requests do not block forever',async()=>{
  for(const status of [409,500,502,504,401,403,400]) {
    const wx=platform(),api=client(wx),p=api.start(A,1,true);answer(wx.calls[0],{error:'failure'},status);await assert.rejects(p);
    assert.equal(!!api.pending(),[409,500,502,504].includes(status));
  }
});
test('storage failure prevents dispatch and closed connection prevents further reads/writes',async()=>{
  const wx=platform();wx.setStorageSync=()=>{throw new Error('disk unavailable');};const api=client(wx);
  await assert.rejects(api.start(A,1,true));assert.equal(wx.calls.length,0);
  api.close();await assert.rejects(api.threads());await assert.rejects(api.stop(A));assert.equal(wx.calls.length,0);
});
function pageHarness() {
  let definition;const wx=platform();const app={};
  vm.runInNewContext(fs.readFileSync(require.resolve('../miniprogram/pages/home/home.js'),'utf8'),{
    Page:d=>{definition=d;}, require:p=>require('../miniprogram/'+p.replace('../../','')), wx, getApp:()=>app,
    clearTimeout,setTimeout,Date,Promise,Number
  });
  const p=definition;p.data=JSON.parse(JSON.stringify(p.data));p.setData=patch=>Object.assign(p.data,patch);p.onLoad();
  p.client=client(wx);p.data.connected=true;p.data.threads=[{id:A,title:'A'},{id:B,title:'B'}];
  return {p,wx};
}
const event=id=>({currentTarget:{dataset:{id}}});
const assessment=id=>({threadId:id,canMonitor:true,taskState:'running',decision:'wait',source:'live'});
test('A→B→A selection rejects stale success and stale error even with same UUID',async()=>{
  const {p,wx}=pageHarness();
  const a=p.select(event(A)),b=p.select(event(B)),a2=p.select(event(A));
  answer(wx.calls[2],assessment(A));await a2;
  answer(wx.calls[0],{...assessment(A),canMonitor:false});await a;
  wx.calls[1].fail();await b;
  assert.equal(p.data.canStart,true);assert.equal(p.data.error,'');assert.equal(p.data.selected.id,A);
});
test('back/hide/disconnect invalidate pending checks, never enable an unselected task',async()=>{
  for(const action of ['showList','onHide','disconnect']) {
    const {p,wx}=pageHarness(),check=p.select(event(A));p[action]();answer(wx.calls[0],assessment(A));await check;
    assert.equal(p.data.canStart,false);assert.equal(p.data.checking,false);
  }
});
test('start rechecks state; a completed task never produces a POST',async()=>{
  const {p,wx}=pageHarness();p.refresh=async()=>{};
  p.setData({selected:{id:A},canStart:true,consent:true,maximum:'1'});
  const start=p.start();answer(wx.calls[0],{...assessment(A),canMonitor:false,taskState:'idle',decision:'stop'});await start;
  assert.equal(wx.calls.length,1);assert.equal(wx.calls[0].method,'GET');assert.match(p.data.error,/状态已变化/);
});
test('start follows fresh check, exact POST, pending lock and success state',async()=>{
  const {p,wx}=pageHarness();p.refresh=async()=>{};
  p.setData({selected:{id:A},canStart:true,consent:true,maximum:'1'});
  const started=p.start();answer(wx.calls[0],assessment(A));await flush();
  assert.equal(wx.calls[1].method,'POST');assert.equal(p.data.writing,true);
  await p.start();assert.equal(wx.calls.length,2);
  answer(wx.calls[1],{threadId:A,status:'waiting_quota'});await started;
  assert.equal(p.data.view,'overview');assert.equal(p.data.writing,false);assert.equal(p.data.pending,null);
});
test('backgrounding during preflight prevents sending; errors survive successful refresh',async()=>{
  const {p,wx}=pageHarness();p.refresh=async()=>{};
  p.setData({selected:{id:A},canStart:true,consent:true,maximum:'1'});
  const started=p.start();p.onHide();answer(wx.calls[0],assessment(A));await started;assert.equal(wx.calls.length,1);
  const h=pageHarness();h.p.data.error='结果未确认';const fresh=h.p.refresh();
  answer(h.wx.calls[0],[]);answer(h.wx.calls[1],{ready:true});await fresh;assert.equal(h.p.data.error,'结果未确认');
});
test('archived row cannot be enrolled even if a malformed eligibility service says yes',async()=>{
  const {p,wx}=pageHarness();p.data.threads[0].archived=true;const q=p.select(event(A));answer(wx.calls[0],assessment(A));await q;assert.equal(p.data.canStart,false);
});
test('wrong task mutation acknowledgement remains pending; no false success',async()=>{
  const wx=platform(),api=client(wx),p=api.start(A,1,true);
  answer(wx.calls[0],{threadId:B,status:'watching'});await assert.rejects(p,e=>e.uncertain);
  assert.equal(api.pending().threadId,A);await assert.rejects(api.start(A,1,true));assert.equal(wx.calls.length,1);
});
test('stop cancel makes no POST; confirmed stop targets exactly the selected watch',async()=>{
  const {p,wx}=pageHarness();p.refresh=async()=>{};p.data.watches=[{thread_id:A,enabled:1}];
  wx.showModal=o=>o.success({confirm:false});await p.stop(event(A));assert.equal(wx.calls.length,0);
  wx.showModal=o=>o.success({confirm:true});const stopped=p.stop(event(A));await flush();
  assert.deepEqual(wx.calls[0].data,{threadId:A});answer(wx.calls[0],{stopped:A});await stopped;assert.equal(p.data.writing,false);
});
test('forced refresh after mutation waits for older poll and fetches a new snapshot',async()=>{
  const {p,wx}=pageHarness();const poll=p.refresh();const forced=p.refresh(true);
  answer(wx.calls[0],[]);answer(wx.calls[1],{ready:true});await poll;await flush();
  assert.equal(wx.calls.length,5);
  answer(wx.calls[2],[{id:A,title:'task'}]);answer(wx.calls[3],[{thread_id:A,enabled:0,status:'paused'}]);answer(wx.calls[4],{ready:true});await forced;
  assert.equal(p.data.watches[0].enabled,0);
});
test('reconnect or disconnect rejects late refresh and never restores old task data',async()=>{
  const {p,wx}=pageHarness();const poll=p.refresh();p.disconnect();
  answer(wx.calls[0],[{thread_id:A,enabled:1}]);answer(wx.calls[1],{ready:true});await poll;
  assert.equal(p.data.connected,false);assert.equal(p.data.watches.length,0);
});
