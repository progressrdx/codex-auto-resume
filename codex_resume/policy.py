"""Pure decisions. Unknown state never authorizes a continuation."""
from dataclasses import dataclass
import hashlib
import json
import math


class UnsupportedState(ValueError):
    pass


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    turn_id: str = ''
    task_state: str = 'unknown'


def latest_turn(state):
    history = state.get('turnHistory')
    if isinstance(history, dict) and history.get('kind') == 'canonical':
        hist = history.get('history', {})
        islands = hist.get('islands', [])
        if not islands:
            raise UnsupportedState('没有可验证的最新会话历史')
        tail = islands[-1]
        if tail.get('newerBoundary', {}).get('status') != 'exhausted':
            raise UnsupportedState('历史尾部不完整；不能判断最新一轮')
        entries = tail.get('entries', [])
        if not entries:
            raise UnsupportedState('最新历史片段为空')
        turn = hist.get('entitiesByKey', {}).get(entries[-1].get('value'))
    else:
        turns = state.get('turns')
        if not isinstance(turns, list) or not turns:
            raise UnsupportedState('缺少会话轮次')
        turn = turns[-1]
    if not isinstance(turn, dict):
        raise UnsupportedState('不支持的会话历史结构')
    return turn


def quota_failure(turn):
    error = turn.get('error')
    if not isinstance(error, dict):
        return False
    info = error.get('codexErrorInfo')
    # These are protocol enum values, not a fuzzy match on user/assistant text.
    return info in ('usageLimitExceeded', 'UsageLimitExceeded') if isinstance(info, str) else False



def interrupted_context_picker(state, request):
    """Recognize ONLY a verified orphan of the App's canceled folder picker.

    No approval request, other tool, current turn, unknown turn, or merely old
    request qualifies. Evidence must be in the same contiguous canonical tail
    as the latest turn. This does not answer or remove the request in the App.
    """
    if not isinstance(request, dict) or request.get('method') != 'item/tool/call':
        return False
    params = request.get('params')
    if not isinstance(params, dict):
        return False
    if params.get('namespace') != 'codex_app' or params.get('tool') != 'setup_codex_context_picker':
        return False
    if not isinstance(state.get('id'), str) or params.get('threadId') != state['id']:
        return False
    tid = params.get('turnId')
    if not isinstance(tid, str) or not tid:
        return False
    history = state.get('turnHistory')
    if not isinstance(history, dict) or history.get('kind') != 'canonical':
        return False
    hist = history.get('history')
    if not isinstance(hist, dict):
        return False
    islands, entities = hist.get('islands'), hist.get('entitiesByKey')
    if not isinstance(islands, list) or not islands or not isinstance(entities, dict):
        return False
    tail = islands[-1]
    if not isinstance(tail, dict) or not isinstance(tail.get('newerBoundary'), dict):
        return False
    entries = tail.get('entries')
    if tail['newerBoundary'].get('status') != 'exhausted' or not isinstance(entries, list) or len(entries) < 2:
        return False
    turns = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('value'), str):
            return False
        turn = entities.get(entry['value'])
        if not isinstance(turn, dict) or not isinstance(turn.get('turnId'), str):
            return False
        turns.append(turn)
    if turns[-1]['turnId'] == tid:
        return False
    matches = [turn for turn in turns[:-1] if turn['turnId'] == tid]
    return len(matches) == 1 and matches[0].get('status') == 'interrupted'


def decide(state):
    if not isinstance(state, dict) or state.get('hostId') != 'local':
        return Decision('stop', '仅支持本机 App 会话', task_state='unsupported')
    if state.get('ephemeral') is not False:
        return Decision('stop', '临时或未知类型会话不受支持', task_state='unsupported')
    if state.get('resumeState') != 'resumed':
        return Decision('wait', '对话仍在加载，等待状态同步', task_state='connecting')
    if not isinstance(state.get('requests'), list):
        return Decision('stop', '缺少审批/用户输入状态')
    if any(not interrupted_context_picker(state, request) for request in state['requests']):
        return Decision('stop', '任务正在等待审批或用户输入', task_state='needs_attention')
    if state.get('threadGoalResumeConfirmation'):
        return Decision('stop', '任务恢复需要用户确认', task_state='needs_attention')
    goal = state.get('threadGoal')
    if goal is not None and (not isinstance(goal, dict) or goal.get('status') != 'active'):
        return Decision('stop', '任务目标已暂停、受阻或结束', task_state='needs_attention')
    if state.get('queuedFollowUps'):
        return Decision('stop', '已有用户排队消息', task_state='needs_attention')
    runtime = state.get('threadRuntimeStatus')
    if not isinstance(runtime, dict):
        return Decision('stop', '缺少运行状态')
    runtime_type = runtime.get('type')
    if runtime_type == 'active':
        if runtime.get('activeFlags'):
            return Decision('stop', '任务正在等待审批、输入或其他前置条件', task_state='needs_attention')
        return Decision('wait', '任务正在执行；加入托管后，因额度耗尽中断时会等待恢复', task_state='running')
    # A real usage-limit failure is exposed by the pinned App as systemError,
    # not idle.  Only these two known terminal containers may be interpreted;
    # other/transitional runtime types retain enrollment but never authorize a send.
    if runtime_type not in ('idle', 'systemError'):
        return Decision('wait', '任务运行状态正在同步；保留托管并稍后重查', task_state='connecting')
    try:
        turn = latest_turn(state)
    except UnsupportedState as exc:
        return Decision('stop', str(exc))
    tid = turn.get('turnId')
    if not isinstance(tid, str) or not tid:
        return Decision('stop', '最新一轮没有稳定标识')
    status = turn.get('status')
    if status == 'inProgress':
        return Decision('wait', '本轮执行状态正在同步', task_state='running')
    if status == 'completed':
        if isinstance(goal, dict) and goal.get('status') == 'active':
            return Decision('wait', '长任务目标仍在进行，等待 App 的下一步；不会因本轮结束额外发送消息', tid, 'running')
        return Decision('stop', '本轮已结束，当前没有执行中的任务，无需加入托管', tid, 'idle')
    if status == 'interrupted':
        return Decision('stop', '本轮被停止；不会自动重启', tid, 'interrupted')
    if status != 'failed' or not quota_failure(turn):
        return Decision('stop', '不是有结构化证据的额度耗尽错误', tid, 'other_failure')
    return Decision('resume', '任务因额度耗尽暂停；确认额度恢复后在原对话继续', tid, 'quota_limited')


def fingerprint(state):
    """Ignore token counters and titles; bind the actual work and execution settings."""
    keys = ('id', 'hostId', 'cwd', 'latestModel', 'latestReasoningEffort',
            'latestCollaborationMode', 'currentPermissions', 'latestThreadSettings',
            'threadGoal', 'threadGoalResumeConfirmation', 'requests', 'queuedFollowUps',
            'threadRuntimeStatus', 'resumeState')
    data = {k: state.get(k) for k in keys}
    data['turn'] = latest_turn(state)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quota_status(response, now, limit_id=None):
    """Return (ready, next_check_epoch, explanation). Always re-read after reset."""
    buckets = response.get('rateLimitsByLimitId')
    if isinstance(buckets, dict) and buckets:
        if limit_id is None and set(buckets) != {'codex'}:
            return False, None, '存在多个额度桶；请显式指定 --limit-id'
        bucket = buckets.get(limit_id or 'codex')
    else:
        bucket = response.get('rateLimits')
        if isinstance(bucket, dict) and limit_id and bucket.get('limitId') != limit_id:
            bucket = None
    if not isinstance(bucket, dict):
        return False, None, '缺少所选额度桶'
    if bucket.get('spendControlReached') or bucket.get('individualLimit') is not None:
        return False, None, '账户还有独立使用限制，需要人工检查'
    reached = bucket.get('rateLimitReachedType')
    if reached not in (None, 'rate_limit_reached'):
        return False, None, '账户存在非窗口额度限制'
    exhausted = []
    windows = [bucket.get('primary'), bucket.get('secondary')]
    if windows[0] is None:
        return False, None, '缺少主窗口额度数据'
    for window in windows:
        if window is None:
            continue
        if not isinstance(window, dict):
            return False, None, '不支持的额度窗口结构'
        used, reset, duration = (window.get(k) for k in ('usedPercent', 'resetsAt', 'windowDurationMins'))
        if not numeric(used) or not 0 <= used <= 100 or not numeric(reset) or reset <= 0 or not numeric(duration) or duration <= 0:
            return False, None, '额度数据不完整，不能推测可用时间'
        if used >= 100:
            exhausted.append(reset)
        elif reset <= now:
            return False, now + 60, '额度数据已过期，稍后重新读取'
    if exhausted:
        return False, max(now + 60, max(exhausted) + 15), '额度仍不足，等待实际重置后再检查'
    if reached:
        return False, now + 60, '服务仍标记额度耗尽，稍后再检查'
    return True, now, '所选额度桶的所有窗口均有余额'
