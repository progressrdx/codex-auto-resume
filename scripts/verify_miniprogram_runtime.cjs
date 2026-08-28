// Official WeChat automation SDK. Real wx.request; account/task data is synthetic.
// Requires an open, compiled miniprogram project with loopback development requests permitted.
// This script NEVER uses the production port or credential, and does not publish/preview.
const automator=require('miniprogram-automator');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const path=require('node:path');
const fs=require('node:fs');
const ID='11111111-1111-4111-8111-111111111111';
const root=path.resolve(__dirname,'..');
async function run(){
  let mini,server,modalMocked=false;
  const deadline=setTimeout(()=>{console.error('Runtime verification timed out; not passed.');if(server)server.kill();process.exit(1);},90000);
  try{
    mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:9420'});
    const page=await mini.currentPage();
    assert(page && page.path === 'pages/home/home','Compile the home page before running verification');
    await page.callMethod('disconnect');
    console.log('Runtime page ready');
    const appid=JSON.parse(fs.readFileSync(path.join(root,'miniprogram/project.config.json'),'utf8')).appid;
    assert.match(appid,/^wx[0-9a-f]{16}$/);
    server=spawn('python3',['-u','-c','import sys;sys.path.insert(0,"tests");from web_fixture import Fixture;from codex_resume.web import CompanionServer\ns=CompanionServer(("127.0.0.1",0),Fixture(),token="mobile-runtime-test",mini_app_id=sys.argv[1]);print(s.server_address[1],flush=True);s.serve_forever()',appid],{cwd:root,stdio:['ignore','pipe','pipe']});
    const [chunk]=await once(server.stdout,'data');
    const base='http://127.0.0.1:'+Number(String(chunk).trim());
    console.log('Isolated backend ready');
    const get=async endpoint=>{const r=await fetch(base+endpoint,{headers:{'X-Resume-Token':'mobile-runtime-test'}});assert.equal(r.status,200);return r.json();};
    await (await page.$('#server')).input(base);
    await (await page.$('#credential')).input('mobile-runtime-test');
    await (await page.$('#connect-action')).tap();
    await page.waitFor(async()=>await page.data('connected'));
    console.log('wx.request connection passed');
    assert.deepEqual(await page.data('threads'),await get('/api/threads'));
    assert.equal(await page.data('credential'),'');
    await (await page.$('#showList-action')).tap();
    await (await page.$('.thread')).tap();
    await page.waitFor(async()=>await page.data('canStart'));
    console.log('Eligibility passed');
    await (await page.$('#maximum')).input('1');
    // Native checkbox change event; request APIs themselves are never mocked.
    await (await page.$('checkbox-group')).trigger('change',{value:['yes']});
    await (await page.$('#start-action')).tap();
    await page.waitFor(async()=>!(await page.data('writing')) && (await page.data('view'))==='overview');
    let rows=await get('/api/watches');assert.equal(rows[0].thread_id,ID);assert.equal(rows[0].enabled,1);
    const displayed=await page.data('watches');assert.equal(displayed[0].enabled,rows[0].enabled);assert.equal(displayed[0].max_resumes,1);
    // Auto-confirm only the synthetic stop dialog, not a real account operation.
    await mini.mockWxMethod('showModal',{confirm:true,cancel:false});
    modalMocked=true;
    await (await page.$('#stop-action')).tap();
    await page.waitFor(async()=>!(await page.data('writing')) && (await page.data('watches'))[0].enabled===0);
    await mini.restoreWxMethod('showModal');
    modalMocked=false;
    rows=await get('/api/watches');assert.equal(rows[0].enabled,0);
    const evidence={at:new Date().toISOString(),scope:'real WeChat simulator wx.request → real HTTP backend with synthetic task data',tests:['connect','threads parity','fresh eligibility','start POST','shared active state','stop POST','shared stopped state'],mocked:['stop confirmation dialog only'],realTaskWrites:0};
    fs.mkdirSync(path.join(root,'tests/evidence'),{recursive:true});
    await mini.screenshot({path:path.join(root,'tests/evidence/miniprogram-synthetic.png')});
    fs.writeFileSync(path.join(root,'tests/evidence/miniprogram-runtime.json'),JSON.stringify(evidence,null,2)+'\n');
    console.log(JSON.stringify(evidence,null,2));
  }finally{
    // Keep the overall deadline active during cleanup: a failed IDE bridge must
    // not turn a failed test into an indefinitely hanging process.
    try {
      if(mini){
        try { if(modalMocked) await Promise.race([mini.restoreWxMethod('showModal'),new Promise(resolve=>setTimeout(resolve,2000))]); }
        finally { mini.disconnect(); }
      }
    } finally {
      if(server && server.exitCode===null){const exited=once(server,'exit');server.kill();await exited;}
      clearTimeout(deadline);
    }
  }
}
run().catch(e=>{console.error('NOT PASSED: '+e.message);process.exit(1);});
