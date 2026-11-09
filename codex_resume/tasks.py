"""Conversation discovery and enrollment, separate from permission to send."""
import socket
import time
import uuid

from .app import ConnectionUnavailable, Desktop, open_selected_thread
from .policy import decide, interrupted_context_picker, quota_failure
from .rpc import ReadOnlyServer
from .runtime import codex_binary

SOURCES = ['cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
           'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown']
LIVE_REOPEN_ATTEMPTS = 9
LIVE_REOPEN_RETRY_INTERVAL = 0.25
LIVE_REOPEN_TIMEOUT = 5.0
LIVE_REOPEN_ATTEMPT_TIMEOUT = 0.75


def list_conversations(server, archive_filters=(False, True), timeout=55):
    """Consume all pages, including archives; don't silently return only page one.

    timeout: total seconds allowed for all pages across all archive filters.
    Callers with large accounts can raise this via --list-timeout.
    """
    rows, ids = [], set()
    deadline = time.monotonic() + timeout
    for archived in archive_filters:
        cursor, seen = None, set()
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError('对话列表尚未读取完整，请稍后重试')
            params = {'limit': 100, 'archived': archived, 'sourceKinds': SOURCES,
                      'sortKey': 'updated_at', 'modelProviders': []}
            if cursor is not None:
                params['cursor'] = cursor
            page = server.query('thread/list', params)
            if not isinstance(page, dict) or not isinstance(page.get('data'), list) or 'nextCursor' not in page:
                raise RuntimeError('对话列表不完整，无法确认后续分页')
            for row in page['data']:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                    raise RuntimeError('对话列表格式不受支持')
                if row['id'] not in ids:
                    ids.add(row['id'])
                    rows.append({key: row.get(key) for key in ('id', 'name', 'preview', 'cwd', 'updatedAt')})
                    rows[-1]['archived'] = archived
            cursor = page['nextCursor']
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise RuntimeError('对话分页游标异常，未返回不完整列表')
            seen.add(cursor)
    return rows


def stored_assessment(thread, thread_id):
    """Persisted turns may qualify for enrollment, NEVER for dispatch."""
    if not isinstance(thread, dict) or thread.get('id') != thread_id:
        raise RuntimeError('读取结果与所选对话不匹配')
    result = {'threadId': thread_id, 'title': thread.get('name') or thread.get('preview'),
              'source': 'history', 'connection': 'waiting', 'decision': 'stop',
              'canMonitor': False, 'taskState': 'unknown',
              'reason': '暂时无法确认任务状态，不会自动续跑', 'checkedAt': time.time()}
    if thread.get('ephemeral') is not False:
        return result
    turns = thread.get('turns')
    if not isinstance(turns, list):
        return result
    if not turns:
        return dict(result, taskState='empty', reason='这个对话还没有任务，无需加入托管')
    turn = turns[-1]
    if not isinstance(turn, dict) or not isinstance(turn.get('id'), str) or not turn['id']:
        return result
    status = turn.get('status')
    if status == 'completed':
        return dict(result, taskState='idle', reason='最近一轮已结束，未发现执行中的任务，无需加入托管')
    if status == 'interrupted':
        return dict(result, taskState='interrupted', reason='最近一轮被停止，不会自动重启')
    if status == 'inProgress' or (status == 'failed' and quota_failure(turn)):
        return dict(result, taskState='running' if status == 'inProgress' else 'quota_limited',
                    decision='wait', canMonitor=True,
                    reason='历史记录显示任务尚在执行' if status == 'inProgress' else '历史记录显示任务因额度耗尽暂停')
    if status == 'failed':
        return dict(result, taskState='other_failure', reason='任务因其他错误停止，不属于额度恢复续跑范围')
    return result


def live_assessment(home, app_path, thread_id, timeout=None):
    if timeout is None:
        desktop = Desktop(home, app_path)
    else:
        desktop = Desktop(home, app_path, timeout=timeout)
    with desktop as client:
        state = client.snapshot(thread_id)
    decision = decide(state)
    if decision.task_state == 'connecting':
        return None
    return {'threadId': thread_id, 'title': state.get('title'),
            'decision': decision.action, 'reason': decision.reason,
            'taskState': decision.task_state,
            'canMonitor': decision.task_state in ('running', 'quota_limited'),
            'source': 'live', 'connection': 'connected', 'checkedAt': time.time(),
            'runtime': state.get('threadRuntimeStatus'), 'model': state.get('latestModel'),
            'ignoredInterruptedPickerRequests': sum(interrupted_context_picker(state, r)
                for r in state.get('requests', [])) if isinstance(state.get('requests'), list) else 0}


def inspect_task(home, app_path, thread_id, list_timeout=55, allow_open=True):
    """Assess one exact conversation. Never authorizes a send.

    allow_open: only the enrollment path and the watcher's reconnect path may
    navigate to the original UUID to restore a live connection. A user-facing
    ``check`` is documented as leaving the App untouched, so it passes False and
    reports persisted history instead of opening the conversation.
    """
    thread_id = str(uuid.UUID(thread_id))
    # Archiving is a user stop signal, even if the last persisted turn was still
    # in progress. Never navigate to or enroll an archived conversation.
    with ReadOnlyServer(codex_binary(app_path), home) as server:
        archived = next((r for r in list_conversations(server, (True,), timeout=list_timeout) if r['id'] == thread_id), None)
    if archived:
        return {'threadId': thread_id, 'title': archived.get('name') or archived.get('preview'),
                'decision': 'stop', 'canMonitor': False, 'taskState': 'archived',
                'source': 'history', 'connection': 'not_checked', 'checkedAt': time.time(),
                'reason': '这个对话已归档，不会加入托管或自动打开'}
    try:
        result = live_assessment(home, app_path, thread_id)
        if result is not None:
            return result
    except (ConnectionUnavailable, ConnectionError, FileNotFoundError, TimeoutError, socket.timeout):
        pass
    # A selected thread can be active in the App while the read-only index
    # still reports it as notLoaded and the persisted turn tail is stale.
    # Opening the exact UUID is navigation only; fresh live state is still
    # required before enrollment. App loading is asynchronous, so poll briefly
    # rather than making one timing-sensitive snapshot attempt.
    if allow_open:
        try:
            open_selected_thread(app_path, thread_id)
        except (ConnectionUnavailable, ConnectionError, FileNotFoundError, TimeoutError, socket.timeout):
            pass
        else:
            transient = (ConnectionUnavailable, ConnectionError, FileNotFoundError,
                         TimeoutError, socket.timeout)
            deadline = time.monotonic() + LIVE_REOPEN_TIMEOUT
            for attempt in range(LIVE_REOPEN_ATTEMPTS):
                remaining = deadline - time.monotonic()
                if remaining <= 0.05:
                    break
                # Snapshot uses one App request and one stream wait; halve the
                # remaining budget so a slow attempt cannot overrun the whole poll.
                timeout = min(LIVE_REOPEN_ATTEMPT_TIMEOUT, remaining / 2)
                try:
                    result = live_assessment(home, app_path, thread_id, timeout=timeout)
                except transient:
                    result = None
                if result is not None:
                    return dict(result, openedSelectedThread=True)
                remaining = deadline - time.monotonic()
                if attempt + 1 < LIVE_REOPEN_ATTEMPTS and remaining > 0:
                    time.sleep(min(LIVE_REOPEN_RETRY_INTERVAL, remaining))
    with ReadOnlyServer(codex_binary(app_path), home) as server:
        response = server.query('thread/read', {'threadId': thread_id, 'includeTurns': True})
    return stored_assessment(response.get('thread'), thread_id)
