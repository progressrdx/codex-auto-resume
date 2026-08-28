// Real TCP/HTTP boundary; synthetic account. Uses the actual mobile request client.
const test=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const https=require('node:https');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {createClient}=require('../miniprogram/lib/api');
const ID='11111111-1111-4111-8111-111111111111';
const DONE='22222222-2222-4222-8222-222222222222';
function wxHTTP(options={}) {
  const store={}, calls=[];
  return {calls,getStorageSync:k=>store[k],setStorageSync:(k,v)=>{store[k]=v;},
    request(o) {
      calls.push({path:new URL(o.url).pathname,method:o.method});
      const body=o.data ? JSON.stringify(o.data) : undefined;
      const headers={...o.header,Referer:'https://servicewechat.com/test/devtools/page-frame.html'};
      if(body) headers['Content-Length']=Buffer.byteLength(body);
      const transport=new URL(o.url).protocol==='https:'?https:http;
      const req=transport.request(o.url,{method:o.method,headers,timeout:o.timeout,ca:options.ca},res=>{
        let text='';res.on('data',c=>{text+=c;});res.on('end',()=>{try{o.success({statusCode:res.statusCode,data:JSON.parse(text)});}catch(e){o.fail(e);}});
      });
      req.on('error',o.fail);req.on('timeout',()=>req.destroy(new Error('timeout')));req.end(body);
      return {abort:()=>req.destroy()};
    }};
}
module.exports={wxHTTP};
if(require.main===module) {
  test('read-only verification failure does not print private titles, paths, or credentials',async()=>{
    const marker='SYNTHETIC_PRIVATE_CONTENT', token='SYNTHETIC_PRIVATE_CREDENTIAL';let count=0,mode='mismatch';
    const directory=fs.mkdtempSync(path.join(os.tmpdir(),'relay-readonly-audit-'));
    const log=path.join(directory,'fixture.log');fs.writeFileSync(log,'连接凭据：'+token+'\n');
    const server=http.createServer((req,res)=>{
      assert.equal(req.headers['x-resume-token'],token);res.setHeader('Content-Type','application/json');
      if(mode==='error') {res.statusCode=500;res.end(JSON.stringify({error:marker}));return;}
      if(mode==='invalidJSON') {res.end(marker);return;}
      res.end(JSON.stringify([{id:ID,title:marker+(++count),cwd:'/private/'+marker,archived:false}]));
    });
    server.listen(0,'127.0.0.1');await once(server,'listening');
    let child;
    try {
      const env={...process.env,RELAY_PRIVATE_LOG:log,RELAY_BASE_URL:'http://127.0.0.1:'+server.address().port};
      delete env.NODE_TEST_CONTEXT;
      for(const scenario of ['mismatch','error','invalidJSON']) {
        mode=scenario;
        child=spawn(process.execPath,[path.resolve(__dirname,'../scripts/verify_mobile_readonly.cjs')],{
          env,stdio:['ignore','pipe','pipe']});
        let output='';child.stdout.on('data',c=>{output+=c;});child.stderr.on('data',c=>{output+=c;});
        const [code]=await once(child,'exit');assert.equal(code,1);
        assert.match(output,mode==='mismatch'?/threads/:/Private payload omitted/);
        assert.ok(!output.includes(marker));assert.ok(!output.includes(token));
      }
    } finally {
      if(child && child.exitCode === null && child.signalCode === null) child.kill();
      server.close();fs.rmSync(directory,{recursive:true,force:true});
    }
  });
  test('HTTP adapter uses verified HTTPS with per-test trust for loopback only',async(t)=>{
    try { execFileSync('openssl',['version'],{stdio:'ignore'}); } catch (_) { t.skip('openssl is unavailable');return; }
    const directory=fs.mkdtempSync(path.join(os.tmpdir(),'relay-mobile-tls-'));
    const cert=path.join(directory,'cert.pem'),key=path.join(directory,'key.pem'),config=path.join(directory,'openssl.cnf');
    fs.writeFileSync(config,'[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n');
    let server;
    try {
      execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',key,'-out',cert,'-config',config],{stdio:'ignore'});
      const certificate=fs.readFileSync(cert);
      server=https.createServer({key:fs.readFileSync(key),cert:certificate},(req,res)=>{
        assert.equal(req.headers['x-resume-token'],'tls-fixture-only');res.setHeader('Content-Type','application/json');
        res.end(JSON.stringify([{id:ID,title:'Synthetic TLS task'}]));
      });
      server.listen(0,'127.0.0.1');await once(server,'listening');
      const base='https://127.0.0.1:'+server.address().port;
      const trusted=createClient(wxHTTP({ca:certificate}),base,'tls-fixture-only');
      assert.deepEqual(await trusted.threads(),[{id:ID,title:'Synthetic TLS task'}]);
      const untrusted=createClient(wxHTTP(),base,'tls-fixture-only');await assert.rejects(untrusted.threads());
    } finally { if(server) server.close();fs.rmSync(directory,{recursive:true,force:true}); }
  });
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
