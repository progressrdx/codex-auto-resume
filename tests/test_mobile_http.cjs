// Real TCP/HTTP boundary; synthetic account. Uses the actual mobile request client.
const test=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {createClient}=require('../miniprogram/lib/api');
const ID='11111111-1111-4111-8111-111111111111';
const DONE='22222222-2222-4222-8222-222222222222';
function wxHTTP() {
  const store={}, calls=[];
  return {calls,getStorageSync:k=>store[k],setStorageSync:(k,v)=>{store[k]=v;},
    request(o) {
      calls.push({path:new URL(o.url).pathname,method:o.method});
      const body=o.data ? JSON.stringify(o.data) : undefined;
      const headers={...o.header,Referer:'https://servicewechat.com/test/devtools/page-frame.html'};
      if(body) headers['Content-Length']=Buffer.byteLength(body);
      const req=http.request(o.url,{method:o.method,headers,timeout:o.timeout},res=>{
        let text='';res.on('data',c=>{text+=c;});res.on('end',()=>{try{o.success({statusCode:res.statusCode,data:JSON.parse(text)});}catch(e){o.fail(e);}});
      });
      req.on('error',o.fail);req.on('timeout',()=>req.destroy(new Error('timeout')));req.end(body);
      return {abort:()=>req.destroy()};
    }};
}
module.exports={wxHTTP};
if(require.main===module || process.env.NODE_TEST_CONTEXT) {
  test('mobile client HTTP round-trip matches Web GET data; start/stop change shared backend state',async()=>{
    const server=spawn('python3',['-u','-c',
      'import sys;sys.path.insert(0,"tests");from web_fixture import Fixture;from codex_resume.web import CompanionServer\ns=CompanionServer(("127.0.0.1",0),Fixture(),token="mobile-http-test");print(s.server_address[1],flush=True);s.serve_forever()'],{cwd:require('node:path').resolve(__dirname,'..'),stdio:['ignore','pipe','pipe']});
    try {
      let errors='';server.stderr.on('data',x=>{errors+=x;});
      const port=await Promise.race([once(server.stdout,'data').then(([b])=>Number(String(b).trim())),once(server,'exit').then(()=>{throw new Error(errors);})]);
      const base=`http://127.0.0.1:${port}`,wx=wxHTTP();
      const api=createClient(wx,base,'mobile-http-test',{devtools:true});
      const web=async path=>{const res=await fetch(base+path,{headers:{'X-Resume-Token':'mobile-http-test'}});assert.equal(res.status,200);return res.json();};
      assert.deepEqual(await api.threads(),await web('/api/threads'));
      assert.deepEqual(await api.watches(),await web('/api/watches'));
      assert.deepEqual(await api.quota(),await web('/api/quota'));
      assert.equal((await api.check(DONE)).canMonitor,false);
      const row=await api.check(ID);assert.equal(row.canMonitor,true);
      await api.start(ID,1,true);
      const active=await api.watches();assert.deepEqual(active,await web('/api/watches'));
      assert.equal(active[0].enabled,1);assert.equal(active[0].max_resumes,1);
      await api.stop(ID);
      const stopped=await api.watches();assert.deepEqual(stopped,await web('/api/watches'));assert.equal(stopped[0].enabled,0);
      const bad=createClient(wx,base,'wrong',{devtools:true});await assert.rejects(bad.stop(ID),e=>e.status===401);
      assert.deepEqual(await web('/api/watches'),stopped);
      assert.equal(wx.calls.filter(c=>c.method==='POST').length,3);
    } finally {server.kill('SIGTERM');await once(server,'exit');}
  });
}
