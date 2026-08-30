"""A single explicit watch, never a blanket scan-and-resume of all tasks."""
import time
import socket

from .app import ConnectionUnavailable
from .policy import decide, fingerprint, quota_status


class Controller:
    def __init__(self, thread_id, store, desktop_factory, quota_reader, limit_id=None, clock=time.time,
                 open_thread=None, notify=None):
        self.thread_id, self.store = thread_id, store
        self.desktop_factory, self.quota_reader = desktop_factory, quota_reader
        self.limit_id, self.clock = limit_id, clock
        self.next_quota_check = 0
        self.waiting_baseline = None
        self.failures = 0
        self.open_thread = open_thread
        self.next_open = 0
        self.notify = notify or (lambda _status, _reason: None)

    def step(self):
        watch = self.store.get(self.thread_id)
        if not watch or not watch['enabled']:
            return False
        if self.store.uncertain(self.thread_id):
            return self.end('uncertain', '存在未确认的发送；请在 App 检查，禁止自动重发')
        stage = 'app'
        try:
            with self.desktop_factory() as desktop:
                state = desktop.snapshot(self.thread_id)
                decision = decide(state)
                if decision.action == 'stop':
                    status = 'needs_attention' if decision.task_state == 'needs_attention' else 'stopped'
                    return self.end(status, decision.reason)
                if decision.action == 'wait':
                    self.failures = 0
                    self.store.update(self.thread_id, 'watching' if decision.task_state == 'running' else 'waiting_connection', decision.reason, only_enabled=True)
                    return True
                baseline = fingerprint(state)
                previous = self.store.attempt(self.thread_id, decision.turn_id)
                if previous:
                    if previous['state'] == 'sent' and self.clock() - previous['created'] < 120:
                        self.store.update(self.thread_id, 'watching', '续跑已发送，等待 App 更新状态', only_enabled=True)
                        return True
                    return self.end('stopped', '本轮已经尝试过续跑；不会重复发送')
                if self.waiting_baseline is not None and self.waiting_baseline != baseline:
                    return self.end('changed', '等待期间任务或执行设置改变；请重新选择是否接管')
                self.waiting_baseline = baseline
                if self.store.count(self.thread_id) >= watch['max_resumes']:
                    return self.end('budget', '已达到该任务累计自动续跑次数上限')
                if self.clock() < self.next_quota_check:
                    self.store.update(self.thread_id, 'waiting_quota', '等待额度重置；期间仍检查任务是否被停止', only_enabled=True)
                    return True
                stage = 'quota'
                ready, next_check, reason = quota_status(self.quota_reader(), self.clock(), self.limit_id)
                self.failures = 0
                if not ready:
                    if next_check is None:
                        return self.end('blocked', reason)
                    self.next_quota_check = next_check
                    self.store.update(self.thread_id, 'waiting_quota', reason, only_enabled=True)
                    return True
                # Persist intent BEFORE any network write. Includes a cancellation/budget check.
                message_id = self.store.claim(self.thread_id, decision.turn_id, baseline)
                if message_id is None:
                    return self.end('stopped', '监控已暂停、预算用尽或该轮次已有发送记录')
                try:
                    # The adapter obtains another fresh snapshot and rechecks the fingerprint.
                    # It inherits App settings and sends no approval decisions.
                    if not self.store.get(self.thread_id)['enabled']:
                        return self.end('uncertain', '发送已取消；保留防重记录')
                    resumed = desktop.resume(self.thread_id, baseline, message_id,
                        dispatch_guard=lambda: self.store.can_dispatch(
                            self.thread_id, decision.turn_id, message_id))
                    self.store.acknowledged(self.thread_id, decision.turn_id, resumed)
                except Exception:
                    return self.end('uncertain', '续跑没有得到可验证的确认；请在 App 检查，不会重发')
                self.waiting_baseline = None
                self.next_quota_check = 0
                self.store.update(self.thread_id, 'resumed', 'App 已确认启动下一轮；继续观察结果', only_enabled=True)
                return True
        except (ConnectionUnavailable, ConnectionError, FileNotFoundError, TimeoutError, socket.timeout) as exc:
            # Keep the explicit enrollment through temporary disconnections and
            # sleeps. Never use persisted history to authorize a send.
            # A selected task may lose its owner because the App was closed, not
            # only because its page was unloaded.  The callback performs a fresh
            # read-only eligibility check before opening the exact original UUID.
            if stage == 'app' and self.open_thread and self.clock() >= self.next_open:
                self.next_open = self.clock() + 300
                if self.store.get(self.thread_id)['enabled']:
                    try:
                        if self.open_thread(self.thread_id) is False:
                            return self.end('stopped', '所选对话已结束、归档或不再符合托管条件；未自动打开或发送消息')
                    except Exception:
                        pass  # Navigation failure is not delivery uncertainty.
            status = 'waiting_connection' if stage == 'app' else 'waiting_quota'
            reason = ('托管已保留，等待 App 重新连接；连接恢复并重新核验前不会发送消息'
                      if stage == 'app' else '托管已保留，等待重新读取额度；不会按时间直接续跑')
            self.store.update(self.thread_id, status, reason, only_enabled=True)
            return True
        except Exception:
            self.failures += 1
            if self.failures >= 3:
                return self.end('blocked', '连续三次无法安全读取 App 或额度；请检查 App、登录、网络与版本')
            self.store.update(self.thread_id, 'retrying', '暂时无法读取状态；不续跑，稍后重查', only_enabled=True)
            return True

    def end(self, status, reason):
        self.store.update(self.thread_id, status, reason, True, only_enabled=True)
        if status not in ('stopped', 'budget'):
            self.notify(status, reason)
        return False
