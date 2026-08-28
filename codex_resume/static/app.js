'use strict';
const $ = (id) => document.getElementById(id);
let token = sessionStorage.getItem('relay-token') || '';
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('token')) {
  token = fragment.get('token');
  sessionStorage.setItem('relay-token', token);
  history.replaceState(null, '', location.pathname);
}
let connected = false;
let authGeneration = 0;
let threads = [];
let checked = null;
let selectedStop = null;
let reading = false;
let quota = null;
let lastQuotaFetch = 0;
let starting = false;
let checkGeneration = 0;
let pickerGeneration = 0;
const assessments = new Map();
const taskStates = {running:'任务执行中', quota_limited:'因额度暂停', needs_attention:'需要你处理',
  idle:'本轮已结束', empty:'空对话', interrupted:'已手动停止', other_failure:'其他错误',
  unsupported:'暂不支持', unknown:'状态未知', connecting:'等待连接', archived:'已归档'};
const states = {
  starting: ['启动中', 'muted'], watching: ['托管中 · 正在执行', ''], waiting_quota: ['托管中 · 等待额度', 'warning'],
  waiting_connection: ['托管中 · 等待连接', 'warning'], needs_attention: ['需要你处理', 'warning'],
  resumed: ['已发送续跑', ''], stopped: ['已停止', 'muted'], paused: ['手动停止', 'muted'],
  uncertain: ['结果未确认', 'danger'], blocked: ['需要检查', 'warning'],
  budget: ['达到次数上限', 'muted'], changed: ['任务已变化', 'warning'], retrying: ['连接重试中', 'warning']
};
function node(tag, cls, text) { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = text; return el; }
function notice(text, warning = false) { $('notice').textContent = text; $('notice').className = warning ? 'notice warning' : 'notice'; $('notice').hidden = !text; }
function busy(button, value, label) { button.disabled = value; button.setAttribute('aria-busy', String(value)); button.textContent = label; }
function timeLabel(value) { return new Date(value * 1000).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}); }
function connectedUI(value) {
  connected = value;
  $('connection-state').textContent = value ? '控制台已连接' : '尚未连接';
  $('connection-state').className = `connection-state${value ? ' connected' : ''}`;
  $('login-panel').hidden = value;
  $('refresh').disabled = !value;
  $('choose-task').disabled = !value;
  $('disconnect').hidden = !value;
}
function forget() {
  authGeneration += 1;
  token = ''; sessionStorage.removeItem('relay-token'); connectedUI(false);
  quota = null; threads = []; checked = null; lastQuotaFetch = 0; assessments.clear();
  if ($('task-dialog').open) $('task-dialog').close();
  if ($('stop-dialog').open) $('stop-dialog').close();
  $('watch-list').replaceChildren(node('p', 'empty-state', '已断开页面。后台监控不会因此停止；请重新连接后查看或停止。'));
  $('watch-updated').textContent = '监控状态未知';
  $('nav-count').textContent = '—'; $('task-count').textContent = '—';
  for (const key of ['primary','secondary']) { $(key+'-percent').textContent = '—'; $(key+'-bar').value = 0; $(key+'-reset').textContent = '重置时间待读取'; $(key+'-clock').textContent = '—'; }
  $('quota-updated').textContent = '尚未连接'; $('quota-reason').textContent = '连接后读取真实额度';
}
async function api(path, data) {
  const generation = authGeneration;
  const response = await fetch(`/api/${path}`, {
    method: data === undefined ? 'GET' : 'POST', cache: 'no-store',
    headers: {'X-Resume-Token': token, ...(data === undefined ? {} : {'Content-Type':'application/json'})},
    ...(data === undefined ? {} : {body: JSON.stringify(data)})
  });
  const result = await response.json();
  if (generation !== authGeneration) throw new Error('连接已变化，请重新读取。');
  if (response.status === 401) { forget(); $('login-error').textContent = result.error; }
  if (!response.ok) throw new Error(result.error || '服务暂时不可用，请稍后刷新。');
  return result;
}
function renderQuota(value) {
  quota = value;
  $('version').textContent = `App ${value.app.version} · ${value.app.build}`;
  $('quota-updated').textContent = `${timeLabel(value.checkedAt)} 读取`;
  $('quota-reason').textContent = value.reason;
  for (const key of ['primary','secondary']) {
    const window = value[key];
    if (!window || !Number.isFinite(window.usedPercent) || window.usedPercent < 0 || window.usedPercent > 100) {
      $(key+'-percent').textContent = '—'; $(key+'-bar').value = 0;
      $(key+'-reset').textContent = '窗口数据不可用'; continue;
    }
    const remaining = Math.max(0, Math.min(100, 100-window.usedPercent));
    $(key+'-percent').textContent = `${Math.round(remaining)}%`;
    $(key+'-bar').value = remaining;
    $(key+'-reset').textContent = Number.isFinite(window.resetsAt) ? `${timeLabel(window.resetsAt)} 重置` : '重置时间未知';
  }
  clocks();
}
function clocks() {
  for (const key of ['primary','secondary']) {
    const reset = quota?.[key]?.resetsAt;
    if (!Number.isFinite(reset)) { $(key+'-clock').textContent = '—'; continue; }
    const seconds = Math.ceil(reset - Date.now()/1000);
    if (seconds <= 0) { $(key+'-clock').textContent = '已到时间，等待重查'; continue; }
    const days = Math.floor(seconds/86400), hours = Math.floor((seconds%86400)/3600), minutes = Math.floor((seconds%3600)/60);
    $(key+'-clock').textContent = days > 0 ? `约 ${days}天 ${hours}时` : `${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
  }
}
function titleFor(id) { return threads.find(t=>t.id===id)?.title || '对话标题暂不可用'; }
function renderWatches(rows) {
  $('watch-error').textContent = '';
  $('task-count').textContent = rows.length;
  $('nav-count').textContent = rows.filter(r=>r.enabled).length;
  $('watch-updated').textContent = `监控记录更新于 ${new Date().toLocaleTimeString('zh-CN')} · 页面打开时每 15 秒刷新`;
  if (!rows.length) {
    const empty = node('div','empty-state');
    const symbol = node('div','relay-symbol'); symbol.setAttribute('aria-hidden','true'); symbol.append(node('span'),node('i'),node('span'));
    empty.append(symbol,node('h3','','还没有监控任务'),node('p','','选择一个正在运行的长任务。若它因额度耗尽停止，续航会等待恢复。'),node('span','empty-note','不会接管其他任务 · 不会自动批准操作'));
    $('watch-list').replaceChildren(empty); return;
  }
  const cards = rows.map(row => {
    const card=node('article','watch-card'), main=node('div'), side=node('div','watch-side');
    main.append(node('h3','',titleFor(row.thread_id)),node('p','',row.reason));
    const meta=node('div','watch-meta'); meta.append(node('span','',`累计尝试 ${row.attempts} / ${row.max_resumes}`),node('span','',`状态写入 ${timeLabel(row.updated)}`));
    if (row.process === 'not_running') meta.append(node('span','error','监控进程已退出，请检查后重新开启'));
    main.append(meta);
    const state=states[row.status] || ['未知状态','warning']; side.append(node('span',`pill ${state[1]}`,state[0]));
    const button=node('button','button secondary',row.enabled ? '停止监控' : '检查任务');
    button.addEventListener('click',()=>row.enabled ? openStop(row) : openChooser(row.thread_id)); side.append(button); card.append(main,side); return card;
  });
  $('watch-list').replaceChildren(...cards);
}
async function refresh(forceQuota=false) {
  if (!connected || reading || document.hidden) return;
  reading=true; busy($('refresh'),true,'读取中…');
  const jobs = [api('watches').then(renderWatches).catch(e=>{
    $('watch-error').textContent=`读取失败：${e.message}`;
    $('watch-updated').textContent='以下是上次读取的记录，不能代表当前状态。';
    for(const button of $('watch-list').querySelectorAll('button')) button.disabled=true;
  })];
  if(forceQuota || Date.now()-lastQuotaFetch>60000) {
    lastQuotaFetch=Date.now();
    jobs.push(api('quota').then(renderQuota).catch(e=>{ $('quota-reason').textContent=`读取失败：${e.message}`; $('quota-updated').textContent='数据可能过期，请刷新'; }));
  }
  await Promise.allSettled(jobs); reading=false; busy($('refresh'),!connected,'刷新状态');
}
async function login(event) {
  event?.preventDefault();
  if(event) { token=$('token').value.trim(); sessionStorage.setItem('relay-token',token); }
  if(!token) return;
  const button=$('login-form').querySelector('button'); busy(button,true,'连接中…'); $('login-error').textContent='';
  try {
    const rows=await api('watches'); connectedUI(true); $('token').value=''; renderWatches(rows); notice('');
    await Promise.allSettled([refresh(true), api('threads').then(data=>{threads=data;}).then(()=>api('watches')).then(renderWatches)]);
  } catch(e) { $('login-error').textContent=e.message; connectedUI(false); }
  finally { busy(button,false,'连接'); }
}
function selectedId() { return $('thread-id').value.trim().toLowerCase(); }
function updateSelection() {
  const id=selectedId(), task=threads.find(t=>t.id===id);
  $('selected-task').hidden=!id;
  $('selected-title').textContent=task?.title || '手动输入的任务';
  $('check-button').hidden=!id;
  for(const button of $('thread-options').querySelectorAll('.thread-option')) {
    const selected=button.dataset.threadId===id;
    button.setAttribute('aria-pressed',String(selected));
    button.querySelector('.selection-marker').hidden=!selected;
    const assessment=assessments.get(button.dataset.threadId);
    const label=button.querySelector('.task-state');
    if(label) label.textContent=assessment ? `上次识别：${taskStates[assessment.taskState]||'状态未知'}` : '选中后识别状态';
  }
}
function renderThreads() {
  const term=$('search').value.trim().toLocaleLowerCase();
  const matches=threads.filter(t=>($('show-archived').checked || !t.archived) && (t.title+' '+t.id).toLocaleLowerCase().includes(term));
  $('thread-count').textContent=`已读取 ${threads.length} 个本地对话 · 当前显示 ${matches.length} 个`;
  const buttons=matches.map(t=>{
    const button=node('button','thread-option'); button.type='button'; button.dataset.threadId=t.id;
    const marker=node('span','selection-marker','已选中'); marker.hidden=true;
    button.append(node('strong','',t.title),node('span','task-state meta','选中后识别状态'),marker);
    if(t.archived) button.append(node('span','pill muted','已归档'));
    button.addEventListener('click',()=>{ if(starting) return; $('thread-id').value=t.id; updateSelection(); checkTask(); }); return button;
  });
  $('thread-options').replaceChildren(...(buttons.length ? buttons : [node('p','meta',term?'没有匹配的对话，请换个标题搜索。':'暂无本地对话，请先在 Codex 中开始一个任务。')]));
  updateSelection();
}
function invalidateCheck() {
  checkGeneration+=1;
  checked=null; $('start-form').hidden=true; $('check-result').hidden=true; $('confirm-start').checked=false; $('action-error').textContent='';
  busy($('check-button'),false,'检查任务');
}
async function openChooser(id='') {
  if(starting) { notice('开启请求仍在处理，请先等待结果并查看监控记录。',true); return; }
  const generation=++pickerGeneration;
  pickerView(false);
  $('manual-selection').open=false;
  $('thread-id').value=id; invalidateCheck(); updateSelection(); $('search').value=''; $('thread-error').textContent='';
  $('thread-options').replaceChildren(node('p','meta','正在读取全部本地对话…'));
  $('thread-count').textContent='正在读取完整列表（包含后续分页）';
  if(!$('task-dialog').open) $('task-dialog').showModal();
  try {
    const rows=await api('threads');
    if(generation!==pickerGeneration || !$('task-dialog').open) return;
    threads=rows; renderThreads();
  } catch(e) {
    if(generation!==pickerGeneration || !$('task-dialog').open) return;
    $('thread-error').textContent=e.message; $('thread-options').replaceChildren();
  }
}
function pickerView(details) {
  $('picker-browser').hidden=details;
  $('task-details').hidden=!details;
}
function backToList() {
  const button=node('button','button secondary back-to-list','返回对话列表');
  button.type='button';
  button.addEventListener('click',()=>{ if(starting) return; invalidateCheck(); pickerView(false); $('search').focus({preventScroll:true}); });
  return button;
}
async function checkTask(event) {
  event?.preventDefault();
  if(starting) return;
  const id=selectedId(); invalidateCheck(); updateSelection();
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) {
    pickerView(false);
    $('action-error').textContent='请选择列表中的对话；使用高级选项时，请填写完整的会话编号。'; return;
  }
  const generation=checkGeneration;
  const stillSelected=()=>generation===checkGeneration && selectedId()===id && $('task-dialog').open;
  busy($('check-button'),true,'检查中…');
  pickerView(true);
  $('task-details').scrollTop=0;
  $('check-result').className='check-result';
  $('check-result').replaceChildren(backToList(),node('p','','正在识别任务状态…'));
  $('check-result').hidden=false;
  try {
    const result=await api(`check?threadId=${encodeURIComponent(id)}`);
    if(!stillSelected()) return;
    if(result.threadId!==id) throw new Error('检查结果与所选任务不一致，已取消。请重新检查。');
    checked=result; assessments.set(id,result); updateSelection();
    const canStart=canEnroll(result);
    $('check-result').className=`check-result${canStart?'':' blocked'}`;
    $('check-result').replaceChildren(backToList(),node('span','pill',taskStates[result.taskState]||'状态未知'),node('strong','',result.title||titleFor(id)),node('p','',result.reason||'无法确认任务状态，不能加入托管。'),node('p','meta',`${result.source==='live'?'App 实时状态':'所选对话历史'} · ${canStart?'可以加入托管':'无需或暂不能托管'}`));
    if(result.source==='history' && canStart) $('check-result').append(node('p','meta','实时连接尚未恢复。加入后会尝试在 App 中加载这个原对话；历史记录只用于筛选，重新核验实时状态与额度前不会发送消息。'));
    if(result.model) $('check-result').append(node('p','meta',`沿用模型：${result.model}`));
    if(result.ignoredInterruptedPickerRequests) $('check-result').append(node('p','meta',`已核实 ${result.ignoredInterruptedPickerRequests} 个旧轮次的文件夹选择请求属于已中断轮次；当前审批仍会阻止续跑。`));
    $('check-result').hidden=false; $('start-form').hidden=!canStart;
    $('check-result').scrollIntoView({block:'start'});
  } catch(e) {
    if(!stillSelected()) return;
    checked=null; $('start-form').hidden=true;
    $('check-result').className='check-result blocked';
    $('check-result').replaceChildren(backToList(),node('strong','','状态暂不可用：'+titleFor(id)),node('p','',e.message),node('p','meta','实时状态和历史记录均未能确认任务资格，或接口不兼容。此次没有开启监控或发送消息；已有托管不受本次检查影响。'));
    $('check-result').hidden=false;
    $('check-result').scrollIntoView({block:'start'});
  } finally { if(stillSelected()) busy($('check-button'),false,'重新检查'); }
}
function canEnroll(result) {
  return result?.canMonitor===true && ['running','quota_limited'].includes(result.taskState) && ['wait','resume'].includes(result.decision);
}
async function startTask(event) {
  event.preventDefault();
  if(!checked || checked.threadId!==selectedId() || !canEnroll(checked) || starting) return;
  starting=true; busy($('start-button'),true,'正在开启…'); $('action-error').textContent='';
  const data={threadId:checked.threadId,maxResumes:Number($('max-resumes').value),confirmed:$('confirm-start').checked};
  if($('limit-id').value.trim()) data.limitId=$('limit-id').value.trim();
  try {
    const result=await api('start',data); $('task-dialog').close();
    notice(`任务已提交监控，当前状态：${(states[result.status]||['待确认'])[0]}。请以下方监控记录为准。`); await refresh();
  } catch(e) {
    $('action-error').textContent=e.message;
    // An uncertain HTTP outcome must never encourage a blind resubmit.
    checked=null; $('start-form').hidden=true; $('check-result').hidden=true;
    notice('开启结果需检查。请先刷新监控记录，再决定是否重新检查任务。',true); await refresh();
  } finally { starting=false; busy($('start-button'),false,'加入托管'); }
}
function openStop(row) { selectedStop=row.thread_id; $('stop-task').textContent=titleFor(row.thread_id); $('stop-error').textContent=''; $('stop-dialog').showModal(); }
async function stopTask() {
  if(!selectedStop) return;
  busy($('confirm-stop'),true,'正在停止…');
  try { await api('stop',{threadId:selectedStop}); $('stop-dialog').close(); notice('已停止后续自动续跑。正在执行的 Codex 任务不受影响。'); await refresh(); }
  catch(e) { $('stop-error').textContent=e.message; }
  finally { busy($('confirm-stop'),false,'停止监控'); }
}
for (const dialog of [$('task-dialog'), $('stop-dialog')]) {
  dialog.addEventListener('keydown', event=>{
    if(event.key==='Escape') { event.preventDefault(); dialog.close(); }
  });
}
$('login-form').addEventListener('submit',login);
$('refresh').addEventListener('click',()=>refresh(true));
$('choose-task').addEventListener('click',()=>openChooser());
$('close-dialog').addEventListener('click',()=>$('task-dialog').close());
$('search').addEventListener('input',renderThreads);
$('show-archived').addEventListener('change',renderThreads);
$('thread-id').addEventListener('input',()=>{ invalidateCheck(); updateSelection(); });
$('task-dialog').addEventListener('close',()=>{ pickerGeneration+=1; invalidateCheck(); });
$('check-form').addEventListener('submit',checkTask);
$('start-form').addEventListener('submit',startTask);
$('cancel-stop').addEventListener('click',()=>$('stop-dialog').close());
$('confirm-stop').addEventListener('click',stopTask);
$('disconnect').addEventListener('click',()=>{forget();notice('页面已断开，后台监控不受影响。');});
$('access-mode').textContent=location.protocol==='https:' ? `HTTPS 加密连接 · ${location.host}` : `仅限本机访问 · ${location.host}`;
setInterval(()=>refresh(),15000); setInterval(clocks,1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden) refresh(true);});
if(token) login();
