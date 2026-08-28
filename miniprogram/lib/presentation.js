const names = {running:'任务执行中',quota_limited:'因额度暂停',idle:'本轮已结束',empty:'暂无任务',interrupted:'已中断',needs_attention:'需要处理',waiting_quota:'等待额度恢复',waiting_connection:'等待 App 连接',starting:'启动中',paused:'已停止托管',stopped:'已停止托管',blocked:'需要处理',uncertain:'发送结果未确认',resumed:'已发送续跑',watching:'观察中'};
function stateName(value) { return names[value] || '状态待确认'; }
function quotaView(value) {
  if (!value || !Number.isFinite(value.usedPercent)) return {remaining:'—', reset:'暂不可用'};
  const date = Number.isFinite(value.resetsAt) ? new Date(value.resetsAt * 1000) : null;
  return {remaining:Math.max(0,Math.min(100,100-value.usedPercent)), reset:date ? date.toLocaleString() : '重置时间未知'};
}
function watchesView(rows, threads) {
  const titles = {}; threads.forEach(r => { titles[r.id] = r.title; });
  return rows.map(r => Object.assign({},r,{title:titles[r.thread_id] || '对话标题暂不可用',stateLabel:stateName(r.status)}));
}
module.exports = {stateName, quotaView, watchesView};
