const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PENDING_KEY = 'relay-unconfirmed-actions-v1';
const WATCH_STATES = ['starting','watching','waiting_quota','waiting_connection','needs_attention',
  'resumed','stopped','paused','uncertain','blocked','budget','changed','retrying'];
let operationSequence = 0;
function address(value, devtools) {
  const s = String(value || '').trim().replace(/\/$/, '');
  const match = /^(https?):\/\/([a-z0-9.-]+)(?::([0-9]{1,5}))?$/i.exec(s);
  if (!match || !match[2] || match[2].includes('..') || (match[3] && (+match[3] < 1 || +match[3] > 65535))) throw new Error('请输入服务地址，不含路径、凭据或查询参数。');
  if (match[1] !== 'https' && !(devtools && match[2] === '127.0.0.1')) throw new Error('手机连接必须使用 HTTPS；127.0.0.1 仅供开发者工具本机测试。');
  return s.toLowerCase();
}
function eligible(row) {
  return !!row && row.canMonitor === true && ['running','quota_limited'].includes(row.taskState) && ['wait','resume'].includes(row.decision);
}
function id(value) { if (!UUID.test(value || '')) throw new Error('任务编号无效，请重新选择对话。'); return value.toLowerCase(); }
function validate(path, data) {
  if (path === '/api/threads' && (!Array.isArray(data) || data.some(r => !r || !UUID.test(r.id) || typeof r.title !== 'string'))) throw new Error('对话数据格式不正确。');
  if (path === '/api/watches' && (!Array.isArray(data) || data.some(r => !r || !UUID.test(r.thread_id) || ![0,1,false,true].includes(r.enabled)))) throw new Error('监控数据格式不正确。');
  if (path === '/api/quota' && (!data || typeof data.ready !== 'boolean')) throw new Error('额度数据格式不正确。');
  if (path.startsWith('/api/check?') && (!data || data.threadId !== path.split('=')[1] || typeof data.canMonitor !== 'boolean')) throw new Error('任务检查响应不匹配，请重新选择。');
  if (!data || typeof data !== 'object') throw new Error('服务返回了无效数据。');
  return data;
}
function createClient(wxApi, base, token, options) {
  const origin = address(base, options && options.devtools);
  if (typeof token !== 'string' || !token.trim() || /[\r\n]/.test(token)) throw new Error('请填写本机服务的连接凭据。');
  const credential = token.trim();
  let writing = false;
  let closed = false;
  function allPending() { return wxApi.getStorageSync(PENDING_KEY) || {}; }
  function pending() { const item = allPending()[origin]; return item || null; }
  function record(value, expected) {
    const rows = allPending();
    // An old connection may finish after its intent was acknowledged and replaced.
    // Its response can settle only its own intent, never a newer request's lock.
    if (expected && (!rows[origin] || rows[origin].operationId !== expected.operationId)) return;
    if (value) rows[origin] = value; else delete rows[origin];
    // Fail closed BEFORE dispatch if durable intent cannot be saved.
    wxApi.setStorageSync(PENDING_KEY, rows);
  }
  function request(path, method, data) {
    if (closed) return Promise.reject(new Error('连接已断开，请重新连接。'));
    return new Promise((resolve, reject) => {
      wxApi.request({url: origin + path, method, data, timeout: 70000,
        header: {'X-Resume-Token': credential, 'Content-Type': 'application/json'},
        success(response) {
          const status = response.statusCode;
          if (status !== 200) {
            const error = new Error(response.data && typeof response.data.error === 'string' ? response.data.error.slice(0,300) : '服务返回错误，请检查连接。');
            error.status = status;
            // A 409 may be a CLI timeout/start-up failure after a watcher was created.
            error.uncertain = method === 'POST' && ![400,401,403,404,413,415].includes(status);
            reject(error); return;
          }
          try {
            const result = validate(path, response.data);
            if (method === 'POST' && ((path === '/api/start' && (result.threadId !== data.threadId || !WATCH_STATES.includes(result.status))) || (path === '/api/stop' && result.stopped !== data.threadId))) throw new Error('操作响应不匹配，请核对监控记录。');
            resolve(result);
          } catch (error) { error.uncertain = method === 'POST'; reject(error); }
        },
        fail() { const error = new Error(method === 'POST' ? '操作结果未确认，请刷新监控记录；不要重复提交。' : '连接失败，请检查服务地址、网络、合法域名与 HTTPS 证书。'); error.uncertain = method === 'POST'; reject(error); }
      });
    });
  }
  async function mutate(action, data) {
    if (closed) throw new Error('连接已断开，请重新连接。');
    if (writing || pending()) throw new Error('上一次操作尚未确认，请先核对监控记录。');
    writing = true;
    const intent = {action, threadId:data.threadId, createdAt:Date.now(), operationId:Date.now() + '-' + (++operationSequence)};
    try {
      record(intent);
      const result = await request('/api/' + action, 'POST', data);
      record(null, intent);
      return result;
    } catch (error) {
      if (!error.uncertain) record(null, intent);
      throw error;
    } finally { writing = false; }
  }
  return {
    origin, pending, busy: () => writing,
    close() { closed = true; },
    acknowledgePending() { if (writing) throw new Error('操作仍在发送，请等待。'); record(null); },
    threads: () => request('/api/threads','GET'),
    watches: () => request('/api/watches','GET'),
    quota: () => request('/api/quota','GET'),
    check: value => request('/api/check?threadId=' + id(value),'GET'),
    start(value, maximum, confirmed) {
      const target = id(value);
      if (!Number.isInteger(maximum) || maximum < 1 || maximum > 100 || confirmed !== true) return Promise.reject(new Error('请确认托管边界，并填写 1–100 的累计续跑上限。'));
      return mutate('start', {threadId:target, maxResumes:maximum, confirmed:true});
    },
    stop: value => mutate('stop', {threadId:id(value)})
  };
}
module.exports = {address, eligible, createClient, validate};
