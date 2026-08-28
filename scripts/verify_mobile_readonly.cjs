// Only GETs; never enrolls, stops, or sends to real tasks. Credentials stay in memory.
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {createClient}=require('../miniprogram/lib/api');
const {wxHTTP}=require('../tests/test_mobile_http.cjs');
async function main(){
  const log=process.env.RELAY_PRIVATE_LOG;
  if(!log) throw new Error('Set RELAY_PRIVATE_LOG to the private startup log; do not pass the token on the command line.');
  const tokens=[...fs.readFileSync(log,'utf8').matchAll(/连接凭据：(\S+)/g)];
  if(!tokens.length) throw new Error('No startup credential found');
  const token=tokens.at(-1)[1],base=process.env.RELAY_BASE_URL || 'http://127.0.0.1:8765';
  const transport=wxHTTP(),mobile=createClient(transport,base,token,{devtools:true});
  const web=async path=>{const r=await fetch(base+path,{headers:{'X-Resume-Token':token}});assert.equal(r.status,200);return r.json();};
  const checks={};
  for(const [name,method] of [['threads','threads'],['watches','watches'],['quota','quota']]){
    const [m,w]=await Promise.all([mobile[method](),web('/api/'+name)]);
    // Match user-visible shared fields, excluding timestamps that can advance during polling.
    const stable=x=>name==='threads'?x.map(r=>({id:r.id,title:r.title,archived:r.archived,cwd:r.cwd})).sort((a,b)=>a.id.localeCompare(b.id)):
      name==='watches'?x.map(r=>({id:r.thread_id,enabled:r.enabled,status:r.status,attempts:r.attempts,max:r.max_resumes})).sort((a,b)=>a.id.localeCompare(b.id)):
      {ready:x.ready,primary:x.primary,secondary:x.secondary,limitId:x.limitId};
    assert.deepEqual(stable(m),stable(w));
    checks[name]={equal:true,count:Array.isArray(m)?m.length:undefined};
  }
  const task=process.env.RELAY_CHECK_THREAD;
  if(task){const result=await mobile.check(task);checks.selectedTask={matchingId:result.threadId===task,taskState:result.taskState,source:result.source};}
  assert.equal(transport.calls.filter(c=>c.method!=='GET').length,0);
  console.log(JSON.stringify({at:new Date().toISOString(),scope:'real backend; actual mobile client with Node HTTP adapter, NOT WeChat runtime',checks,mutations:0},null,2));
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
