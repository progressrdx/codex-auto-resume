import copy
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from codex_resume.app import (AppError, Desktop, MAX_FRAME, ThreadUnavailable,
                              WindowsPipe, check_version, open_selected_thread)
from codex_resume.__main__ import watch_process_spec
from codex_resume.controller import Controller
from codex_resume.policy import decide, fingerprint, quota_status, latest_turn
from codex_resume.rpc import ReadOnlyServer
from codex_resume.store import Store
from codex_resume.tasks import list_conversations, stored_assessment, inspect_task
from codex_resume.runtime import WINDOWS_PIPE, codex_binary, default_app_path

THREAD = '11111111-1111-4111-8111-111111111111'


class SourceLaunchTests(unittest.TestCase):
    def args(self):
        return SimpleNamespace(home=Path('/users/test/.codex'), app=Path('/Applications/ChatGPT.app'),
                               state_dir=Path('/users/test/.codex-auto-resume'), thread=THREAD,
                               max_resumes=3, limit_id=None)

    @patch('codex_resume.__main__.sys.executable', '/usr/bin/python3')
    def test_source_watcher_keeps_module_launch(self):
        args = self.args(); args.limit_id = 'codex'
        command, cwd, env = watch_process_spec(args, 7)
        self.assertEqual(command[:3], ['/usr/bin/python3', '-m', 'codex_resume'])
        self.assertEqual(command[-2:], ['--limit-id', 'codex'])
        self.assertIsNone(env)
        self.assertEqual(cwd.name, 'codex-auto-resume')

    @patch.dict(os.environ, {'LOCALAPPDATA': r'C:\Users\test\AppData\Local'})
    def test_windows_defaults_to_relocated_bundled_cli(self):
        path = default_app_path('windows')
        self.assertEqual(str(path), r'C:\Users\test\AppData\Local/OpenAI/Codex/bin/codex.exe')
        self.assertEqual(codex_binary(path, 'windows'), path)

    def test_windows_watcher_does_not_receive_posix_descriptor(self):
        args = self.args()
        command, _, _ = watch_process_spec(args, None)
        self.assertNotIn('--lock-fd', command)


def state(status='failed', error='usageLimitExceeded', turn_id='t1'):
    turn = {'turnId': turn_id, 'status': status, 'error': {'codexErrorInfo': error}, 'items': []}
    return {'id': THREAD, 'hostId': 'local', 'ephemeral': False, 'resumeState': 'resumed',
            'requests': [], 'threadGoal': None, 'threadGoalResumeConfirmation': None,
            'threadRuntimeStatus': {'type': 'idle'}, 'latestModel': 'same-model',
            'currentPermissions': {'profile': 'existing'}, 'cwd': '/work',
            'turns': [], 'turnHistory': {'kind': 'canonical', 'history': {
                'entitiesByKey': {'entity': turn}, 'islands': [{'entries': [{'key': 'key', 'value': 'entity'}],
                'newerBoundary': {'status': 'exhausted'}}], 'isComplete': True}}}


def quota(primary=10, secondary=10, reset=2000, weekly_reset=8000):
    return {'rateLimitsByLimitId': {'codex': {
        'primary': {'usedPercent': primary, 'windowDurationMins': 300, 'resetsAt': reset},
        'secondary': {'usedPercent': secondary, 'windowDurationMins': 10080, 'resetsAt': weekly_reset},
        'spendControlReached': False, 'individualLimit': None, 'rateLimitReachedType': None}}}


def with_interrupted_picker(data):
    old = {'turnId': 'old-picker-turn', 'status': 'interrupted', 'items': []}
    hist = data['turnHistory']['history']
    hist['entitiesByKey']['old-picker'] = old
    hist['islands'][-1]['entries'].insert(0, {'key': 'old-key', 'value': 'old-picker'})
    hist['isComplete'] = False  # Older history may be unloaded; this tail is contiguous.
    data['requests'] = [{'method': 'item/tool/call', 'params': {
        'threadId': THREAD, 'turnId': old['turnId'], 'namespace': 'codex_app',
        'tool': 'setup_codex_context_picker'}}]
    return data


class PolicyTests(unittest.TestCase):
    def test_task_classification_is_separate_from_dispatch(self):
        for status, label in [('failed','quota_limited'), ('completed','idle'), ('interrupted','interrupted')]:
            self.assertEqual(decide(state(status)).task_state, label)
        active = state(); active['threadRuntimeStatus'] = {'type':'active','activeFlags':[]}
        self.assertEqual(decide(active).task_state, 'running')
    def test_structured_quota_failure(self):
        self.assertEqual(decide(state()).action, 'resume')

    def test_real_app_system_error_quota_failure_is_resumable(self):
        s = with_interrupted_picker(state())
        s['threadRuntimeStatus'] = {'type': 'systemError'}
        turn = latest_turn(s)
        turn['error'].update({
            'message': "You've hit your usage limit. Try again later.",
            'additionalDetails': None,
        })
        decision = decide(s)
        self.assertEqual((decision.action, decision.task_state, decision.turn_id),
                         ('resume', 'quota_limited', 't1'))

    def test_system_error_only_resumes_structured_quota_failure(self):
        for error in ('Other', None, {'usageLimitExceeded': {}}, 'Unauthorized'):
            s = state(error=error)
            s['threadRuntimeStatus'] = {'type': 'systemError'}
            with self.subTest(error=error):
                self.assertEqual(decide(s).action, 'stop')

    def test_transitional_runtime_keeps_enrollment_without_authorizing_send(self):
        for runtime in ({'type':'initializing'}, {'type':'unknown'}, {}):
            s = state(); s['threadRuntimeStatus'] = runtime
            with self.subTest(runtime=runtime):
                decision = decide(s)
                self.assertEqual((decision.action, decision.task_state), ('wait', 'connecting'))

    def test_text_is_not_quota_evidence(self):
        s = state(error='Other')
        latest_turn(s)['error']['message'] = 'Usage limit reached, continue now'
        self.assertEqual(decide(s).action, 'stop')

    def test_normal_stops_are_not_resumed(self):
        for status in ('completed', 'interrupted', 'unknown'):
            with self.subTest(status=status):
                self.assertEqual(decide(state(status)).action, 'stop')

    def test_network_and_auth_failures_are_not_quota_failures(self):
        for error in ('Unauthorized', 'HttpConnectionFailed', None, {'usageLimitExceeded': {}}):
            self.assertEqual(decide(state(error=error)).action, 'stop')

    def test_approvals_and_paused_goals_block(self):
        for field, value in [('requests', [{'type': 'approval'}]),
                             ('threadGoal', {'status': 'paused'}),
                             ('threadGoal', {'status': 'complete'}),
                             ('threadGoalResumeConfirmation', {'required': True}),
                             ('ephemeral', True), ('hostId', 'remote'), ('queuedFollowUps', ['message'])]:
            s = state(); s[field] = value
            self.assertEqual(decide(s).action, 'stop', field)

    def test_active_task_waits_but_active_approval_stops(self):
        s = state(); s['threadRuntimeStatus'] = {'type': 'active', 'activeFlags': []}
        self.assertEqual(decide(s).action, 'wait')
        s['threadRuntimeStatus']['activeFlags'] = ['waitingOnApproval']
        self.assertEqual(decide(s).action, 'stop')

    def test_verified_interrupted_picker_does_not_block_new_turn(self):
        s = with_interrupted_picker(state())
        original = copy.deepcopy(s)
        self.assertEqual(decide(s).action, 'resume')
        s['threadRuntimeStatus'] = {'type': 'active', 'activeFlags': []}
        self.assertEqual(decide(s).action, 'wait')
        self.assertEqual(s['requests'], original['requests'])  # Never remove or answer App requests.

    def test_picker_exception_requires_exact_tool_thread_and_interruption(self):
        for field, value in [('namespace', 'other'), ('tool', 'approve_request'),
                             ('threadId', 'other-thread'), ('turnId', 'missing'), ('turnId', 't1')]:
            s = with_interrupted_picker(state())
            s['requests'][0]['params'][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(decide(s).action, 'stop')
        for status in ('inProgress', 'completed', 'failed', None):
            s = with_interrupted_picker(state())
            s['turnHistory']['history']['entitiesByKey']['old-picker']['status'] = status
            self.assertEqual(decide(s).action, 'stop')

    def test_picker_exception_never_exempts_approvals_or_additional_requests(self):
        for method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval',
                       'item/permissions/requestApproval', 'item/tool/requestUserInput', 'unknown'):
            s = with_interrupted_picker(state())
            s['requests'][0]['method'] = method
            self.assertEqual(decide(s).action, 'stop')
        s = with_interrupted_picker(state())
        s['requests'].append({'type': 'approval'})
        self.assertEqual(decide(s).action, 'stop')
        s = with_interrupted_picker(state())
        s['threadRuntimeStatus'] = {'type': 'active', 'activeFlags': ['waitingOnApproval']}
        self.assertEqual(decide(s).action, 'stop')

    def test_picker_exception_needs_unambiguous_contiguous_history(self):
        for alteration in ('boundary', 'missing-entity', 'split-island', 'duplicate', 'legacy', 'malformed'):
            s = with_interrupted_picker(state())
            hist = s['turnHistory']['history']; tail = hist['islands'][-1]
            if alteration == 'boundary': tail['newerBoundary']['status'] = 'unknown'
            elif alteration == 'missing-entity': del hist['entitiesByKey']['old-picker']
            elif alteration == 'split-island': hist['islands'].insert(0, {'entries': [tail['entries'].pop(0)]})
            elif alteration == 'duplicate': tail['entries'].insert(0, tail['entries'][0])
            elif alteration == 'legacy': del s['turnHistory']
            elif alteration == 'malformed': tail['entries'][0]['value'] = []
            with self.subTest(alteration=alteration): self.assertEqual(decide(s).action, 'stop')

    def test_incomplete_history_is_not_latest(self):
        s = state(); s['turnHistory']['history']['islands'][-1]['newerBoundary']['status'] = 'unknown'
        self.assertEqual(decide(s).action, 'stop')

    def test_entity_uses_value_not_key(self):
        self.assertEqual(latest_turn(state())['turnId'], 't1')

    def test_missing_state_is_not_safe(self):
        for key in ('requests', 'threadRuntimeStatus', 'ephemeral'):
            s = state(); del s[key]
            self.assertEqual(decide(s).action, 'stop')

    def test_legacy_history(self):
        s = state(); turn = latest_turn(s); del s['turnHistory']; s['turns'] = [turn]
        self.assertEqual(decide(s).action, 'resume')

    def test_fingerprint_tracks_settings_and_content(self):
        s = state(); original = fingerprint(s)
        s['title'] = 'new title'; s['latestTokenUsageInfo'] = {'total': 123}
        self.assertEqual(fingerprint(s), original)
        s['currentPermissions'] = {'profile': 'changed'}
        self.assertNotEqual(fingerprint(s), original)

    def test_available_quota(self):
        self.assertTrue(quota_status(quota(), 1000)[0])

    def test_weekly_limit_overrides_short_reset(self):
        ready, when, _ = quota_status(quota(100, 100), 1000)
        self.assertFalse(ready); self.assertEqual(when, 8015)

    def test_expired_quota_must_refresh(self):
        ready, when, _ = quota_status(quota(reset=900), 1000)
        self.assertFalse(ready); self.assertEqual(when, 1060)

    def test_bad_quota_fails_closed(self):
        for value in (None, True, float('nan'), -1, 101, '10'):
            q = quota(); q['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = value
            self.assertFalse(quota_status(q, 1000)[0])

    def test_multiple_buckets_require_selection(self):
        q = quota(); q['rateLimitsByLimitId']['other'] = copy.deepcopy(q['rateLimitsByLimitId']['codex'])
        self.assertIsNone(quota_status(q, 1000)[1])
        self.assertTrue(quota_status(q, 1000, 'codex')[0])
        self.assertFalse(quota_status(q, 1000, 'missing')[0])

    def test_spend_controls_are_not_waitable_windows(self):
        q = quota(); q['rateLimitsByLimitId']['codex']['spendControlReached'] = True
        self.assertEqual(quota_status(q, 1000)[:2], (False, None))


class TaskDiscoveryTests(unittest.TestCase):
    def stored(self, status='completed', error=None):
        return {'id': THREAD, 'ephemeral': False, 'turns': [
            {'id':'turn-1', 'status':status, 'error':{'codexErrorInfo':error}}]}

    def test_history_never_authorizes_dispatch(self):
        for status, error in [('inProgress',None), ('failed','usageLimitExceeded')]:
            result = stored_assessment(self.stored(status,error), THREAD)
            self.assertTrue(result['canMonitor'])
            self.assertEqual(result['decision'], 'wait')
            self.assertEqual(result['connection'], 'waiting')
            self.assertEqual(result['source'], 'history')

    def test_empty_idle_interrupted_and_unknown_cannot_enroll(self):
        for status in ('completed','interrupted','failed','unknown'):
            self.assertFalse(stored_assessment(self.stored(status), THREAD)['canMonitor'])
        for turns in ([], None, [{'id':'t', 'status':'unknown'}], [{'status':'inProgress'}]):
            thread = self.stored(); thread['turns'] = turns
            self.assertFalse(stored_assessment(thread, THREAD)['canMonitor'])
        thread = self.stored(); thread['turns'] = []
        self.assertEqual(stored_assessment(thread, THREAD)['taskState'], 'empty')
        thread['id'] = 'different'
        with self.assertRaises(RuntimeError): stored_assessment(thread, THREAD)

    @patch('codex_resume.tasks.ReadOnlyServer')
    @patch('codex_resume.tasks.Desktop')
    def test_unloaded_selection_falls_back_to_read_only_history(self, desktop, server):
        desktop.return_value.__enter__.return_value.snapshot.side_effect = ThreadUnavailable()
        reader = server.return_value.__enter__.return_value
        reader.query.side_effect = [{'data':[], 'nextCursor':None}, {'thread': self.stored('inProgress')}]
        result = inspect_task(Path('/home'), Path('/app'), THREAD)
        self.assertTrue(result['canMonitor'])
        reader.query.assert_called_with('thread/read', {'threadId':THREAD, 'includeTurns':True})

    @patch('codex_resume.tasks.ReadOnlyServer')
    @patch('codex_resume.tasks.Desktop')
    def test_socket_timeout_selection_falls_back_to_read_only_history(self, desktop, server):
        import socket
        desktop.return_value.__enter__.return_value.snapshot.side_effect = socket.timeout
        reader = server.return_value.__enter__.return_value
        reader.query.side_effect = [{'data':[], 'nextCursor':None}, {'thread': self.stored('inProgress')}]
        result = inspect_task(Path('/home'), Path('/app'), THREAD)
        self.assertTrue(result['canMonitor'])
        self.assertEqual(result['source'], 'history')
        self.assertEqual(result['decision'], 'wait')

    @patch('codex_resume.tasks.ReadOnlyServer')
    @patch('codex_resume.tasks.Desktop')
    def test_incompatible_live_state_never_falls_back(self, desktop, server):
        reader = server.return_value.__enter__.return_value
        reader.query.return_value = {'data':[], 'nextCursor':None}
        desktop.return_value.__enter__.return_value.snapshot.side_effect = AppError('unsafe protocol')
        with self.assertRaises(AppError): inspect_task(Path('/home'), Path('/app'), THREAD)
        self.assertTrue(all(c.args[0]=='thread/list' for c in reader.query.call_args_list))

    @patch('codex_resume.tasks.ReadOnlyServer')
    @patch('codex_resume.tasks.Desktop')
    def test_archived_thread_cannot_enroll_even_if_last_turn_was_running(self, desktop, server):
        server.return_value.__enter__.return_value.query.return_value = {'data':[{'id':THREAD}], 'nextCursor':None}
        result = inspect_task(Path('/home'), Path('/app'), THREAD)
        self.assertFalse(result['canMonitor'])
        self.assertEqual(result['taskState'], 'archived')
        desktop.assert_not_called()

    def test_all_pages_sources_and_archives_are_read_without_task_execution(self):
        from unittest.mock import Mock
        server = Mock()
        server.query.side_effect = [
            {'data':[{'id':'a'}], 'nextCursor':'page2'},
            {'data':[{'id':'a'}, {'id':'b'}], 'nextCursor':None},
            {'data':[{'id':'c'}], 'nextCursor':None}]
        rows = list_conversations(server)
        self.assertEqual([r['id'] for r in rows], ['a','b','c'])
        calls = server.query.call_args_list
        self.assertEqual(calls[1].args[1]['cursor'], 'page2')
        self.assertTrue(calls[2].args[1]['archived'])
        self.assertTrue(rows[-1]['archived'])
        self.assertIn('appServer', calls[0].args[1]['sourceKinds'])
        self.assertTrue(all(c.args[0]=='thread/list' for c in calls))

    def test_incomplete_or_looping_pages_fail_instead_of_truncating(self):
        from unittest.mock import Mock
        for page in [{'data':[]}, {'data':[], 'nextCursor':'repeated'}]:
            server = Mock(); server.query.return_value = page
            with self.assertRaises(RuntimeError): list_conversations(server)

    @patch('codex_resume.app.subprocess.run')
    @patch('codex_resume.app.check_version')
    def test_navigation_only_uses_validated_original_uuid_without_prompt(self, version, run):
        open_selected_thread('/Applications/Test.app', THREAD)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/open','-g','-a',
            '/Applications/Test.app','codex://threads/'+THREAD])
        run.reset_mock()
        with self.assertRaises(ValueError): open_selected_thread('/app','invalid?prompt=execute')
        run.assert_not_called()


class FakeDesktop:
    def __init__(self, data):
        self.data, self.calls, self.fail = data, [], False
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def snapshot(self, thread): return self.data
    def resume(self, thread, baseline, message, dispatch_guard=None):
        if dispatch_guard is not None and not dispatch_guard():
            raise AppError('synthetic cancelled dispatch')
        self.calls.append((thread, baseline, message))
        if self.fail: raise TimeoutError()
        return 'new-turn'


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.arm(THREAD, 3)
        self.desktop = FakeDesktop(state())
        self.reads = 0
        self.quota = quota()
        def read():
            self.reads += 1
            return self.quota
        self.controller = Controller(THREAD, self.store, lambda: self.desktop, read, clock=lambda: 1000)
    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_resume_records_ack(self):
        self.assertTrue(self.controller.step())
        self.assertEqual(len(self.desktop.calls), 1)
        self.assertEqual(self.store.attempt(THREAD, 't1')['resumed_turn'], 'new-turn')

    def test_wait_does_not_send_or_requery_early(self):
        self.quota = quota(100)
        self.assertTrue(self.controller.step()); self.assertTrue(self.controller.step())
        self.assertEqual(self.reads, 1); self.assertFalse(self.desktop.calls)

    def test_system_error_waits_for_reset_then_resumes_once(self):
        self.desktop.data['threadRuntimeStatus'] = {'type': 'systemError'}
        self.quota = quota(100)
        self.assertTrue(self.controller.step())
        self.assertEqual(self.store.get(THREAD)['status'], 'waiting_quota')
        self.assertEqual(self.store.get(THREAD)['enabled'], 1)
        self.assertFalse(self.desktop.calls)
        self.controller.clock = lambda: 2100
        self.quota = quota(reset=3000)
        self.assertTrue(self.controller.step())
        self.assertEqual(len(self.desktop.calls), 1)
        self.assertEqual(self.store.attempt(THREAD, 't1')['state'], 'sent')
        self.assertTrue(self.controller.step())
        self.assertEqual(len(self.desktop.calls), 1)

    def test_reset_does_not_imply_headroom(self):
        self.quota = quota(100)
        self.controller.step()
        self.controller.clock = lambda: 2100
        self.assertTrue(self.controller.step())
        self.assertEqual(self.reads, 2); self.assertFalse(self.desktop.calls)

    def test_pause_during_wait(self):
        self.quota = quota(100); self.controller.step(); self.store.stop(THREAD)
        self.assertFalse(self.controller.step()); self.assertFalse(self.desktop.calls)

    def test_waiting_state_change_stops(self):
        self.quota = quota(100); self.controller.step()
        self.desktop.data['latestModel'] = 'other'
        self.assertFalse(self.controller.step()); self.assertFalse(self.desktop.calls)

    def test_unknown_delivery_not_retried_even_after_restart(self):
        self.desktop.fail = True
        self.assertFalse(self.controller.step())
        self.store.arm(THREAD, 3); self.desktop.fail = False
        self.assertFalse(self.controller.step()); self.assertEqual(len(self.desktop.calls), 1)
        self.assertEqual(self.store.get(THREAD)['status'], 'uncertain')

    def test_existing_ack_never_duplicated(self):
        self.controller.step(); self.controller.step()
        self.assertEqual(len(self.desktop.calls), 1)

    def test_stale_picker_is_revalidated_and_new_approval_still_stops(self):
        self.desktop.data = with_interrupted_picker(state())
        self.quota = quota(100)
        self.assertTrue(self.controller.step())
        self.desktop.data['requests'].append({'method': 'item/permissions/requestApproval'})
        self.assertFalse(self.controller.step())
        self.assertFalse(self.desktop.calls)

    def test_picker_history_change_stops_before_sending(self):
        self.desktop.data = with_interrupted_picker(state())
        self.quota = quota(100)
        self.assertTrue(self.controller.step())
        self.desktop.data['turnHistory']['history']['entitiesByKey']['old-picker']['status'] = 'inProgress'
        self.quota = quota()
        self.controller.clock = lambda: 2100
        self.assertFalse(self.controller.step())
        self.assertFalse(self.desktop.calls)

    def test_normal_completion_stops_without_quota_query(self):
        self.desktop.data = state('completed')
        self.assertFalse(self.controller.step()); self.assertEqual(self.reads, 0)

    def test_active_long_goal_keeps_enrollment_between_turns_without_sending(self):
        self.desktop.data = state('completed')
        self.desktop.data['threadGoal'] = {'status':'active'}
        self.assertTrue(self.controller.step())
        self.assertEqual(self.store.get(THREAD)['enabled'], 1)
        self.assertFalse(self.desktop.calls)
        self.assertEqual(self.reads, 0)
        self.desktop.data['threadGoal'] = {'status':'complete'}
        self.assertFalse(self.controller.step())

    def test_attempt_limit_is_enforced(self):
        self.store.arm(THREAD, 1); self.controller.step()
        self.desktop.data = state(turn_id='t2')
        self.assertFalse(self.controller.step()); self.assertEqual(len(self.desktop.calls), 1)

    def test_next_window_can_resume_new_failed_turn(self):
        self.controller.step()
        self.controller.step()  # A still-stale snapshot of the previous failure.
        self.desktop.data = state(turn_id='t2')
        self.assertTrue(self.controller.step()); self.assertEqual(len(self.desktop.calls), 2)

    def test_stop_between_quota_read_and_claim(self):
        def read():
            self.store.stop(THREAD); return quota()
        self.controller.quota_reader = read
        self.assertFalse(self.controller.step()); self.assertFalse(self.desktop.calls)

    def test_stop_during_final_snapshot_does_not_send_or_overwrite_pause(self):
        app = Desktop('/unused')
        def snapshot(thread):
            self.store.stop(thread)
            return self.desktop.data
        app.snapshot = snapshot
        from unittest.mock import Mock
        app.request = Mock(return_value={'result': {'result': {'turn': {'id': 'new'}}}})
        self.desktop.resume = app.resume
        with patch('codex_resume.app.check_version'), patch.object(app, '_drain_pending'):
            self.assertFalse(self.controller.step())
        app.request.assert_not_called()
        row = self.store.get(THREAD)
        self.assertEqual((row['enabled'], row['status']), (0, 'paused'))
        self.assertTrue(self.store.uncertain(THREAD))

    def test_stop_during_quota_wait_preserves_paused_status(self):
        def read():
            self.store.stop(THREAD)
            return quota(100)
        self.controller.quota_reader = read
        self.controller.step()
        self.assertEqual(self.store.get(THREAD)['status'], 'paused')
        self.assertFalse(self.desktop.calls)

    def test_final_dispatch_guard_checks_claim_and_current_budget(self):
        message = self.store.claim(THREAD, 't1', 'hash')
        self.assertTrue(self.store.can_dispatch(THREAD, 't1', message))
        self.assertFalse(self.store.can_dispatch(THREAD, 't1', 'other-message'))
        self.assertFalse(self.store.can_dispatch(THREAD, 'other-turn', message))
        with self.store.db:
            self.store.db.execute('UPDATE watches SET max_resumes=0 WHERE thread_id=?', (THREAD,))
        self.assertFalse(self.store.can_dispatch(THREAD, 't1', message))
        self.store.arm(THREAD, 3)
        self.store.acknowledged(THREAD, 't1', 'new')
        self.assertFalse(self.store.can_dispatch(THREAD, 't1', message))

    def test_repeated_read_failure_stops(self):
        def fail(): raise OSError()
        self.controller.desktop_factory = fail
        self.assertTrue(self.controller.step()); self.assertTrue(self.controller.step())
        self.assertFalse(self.controller.step()); self.assertFalse(self.desktop.calls)

    def test_disconnection_survives_more_than_three_checks_then_resumes(self):
        self.quota = quota(100)
        self.controller.step()
        with patch.object(self.desktop, 'snapshot', side_effect=ConnectionResetError):
            for _ in range(5):
                self.assertTrue(self.controller.step())
            self.assertEqual(self.store.get(THREAD)['status'], 'waiting_connection')
            self.assertEqual(self.store.get(THREAD)['enabled'], 1)
            self.assertFalse(self.desktop.calls)
        self.controller.clock = lambda: 2100
        self.quota = quota(reset=3000)
        self.assertTrue(self.controller.step())
        self.assertEqual(len(self.desktop.calls), 1)

    def test_disconnection_does_not_forget_waiting_fingerprint(self):
        self.quota = quota(100); self.controller.step()
        with patch.object(self.desktop, 'snapshot', side_effect=TimeoutError):
            self.assertTrue(self.controller.step())
        self.desktop.data['latestModel'] = 'changed'
        self.assertFalse(self.controller.step())
        self.assertFalse(self.desktop.calls)

    def test_reconnect_navigation_is_selected_throttled_and_cancellable(self):
        opened = []; self.controller.open_thread = opened.append
        with patch.object(self.desktop, 'snapshot', side_effect=ThreadUnavailable):
            for _ in range(5):
                self.assertTrue(self.controller.step())
            self.assertEqual(opened, [THREAD])
            self.controller.clock = lambda: 1301
            self.assertTrue(self.controller.step())
            self.assertEqual(opened, [THREAD, THREAD])
            self.store.stop(THREAD)
            self.assertFalse(self.controller.step())
            self.assertEqual(len(opened), 2)
        self.assertFalse(self.desktop.calls)

    def test_app_transport_loss_can_reopen_only_the_selected_task(self):
        opened = []; self.controller.open_thread = opened.append
        with patch.object(self.desktop, 'snapshot', side_effect=FileNotFoundError):
            self.assertTrue(self.controller.step())
            self.assertTrue(self.controller.step())
        self.assertEqual(opened, [THREAD])
        self.assertEqual(self.store.get(THREAD)['status'], 'waiting_connection')
        self.assertEqual(self.store.get(THREAD)['enabled'], 1)
        self.assertFalse(self.desktop.calls)

    def test_quota_read_timeout_keeps_watch_without_dispatch(self):
        opened = []; self.controller.open_thread = opened.append
        with patch.object(self.controller, 'quota_reader', side_effect=TimeoutError):
            for _ in range(5):
                self.assertTrue(self.controller.step())
        self.assertEqual(self.store.get(THREAD)['status'], 'waiting_quota')
        self.assertEqual(opened, [])
        self.assertFalse(self.desktop.calls)

    def test_socket_timeout_keeps_enrollment_on_supported_python_versions(self):
        import socket
        with patch.object(self.desktop, 'snapshot', side_effect=socket.timeout):
            for _ in range(5):
                self.assertTrue(self.controller.step())
        self.assertEqual(self.store.get(THREAD)['enabled'], 1)
        self.assertFalse(self.desktop.calls)

    def test_reconnect_does_not_open_task_that_became_ineligible(self):
        self.controller.open_thread = lambda tid: False
        with patch.object(self.desktop, 'snapshot', side_effect=ThreadUnavailable):
            self.assertFalse(self.controller.step())
        self.assertFalse(self.desktop.calls)
        self.assertEqual(self.store.get(THREAD)['enabled'], 0)

    def test_reconnect_rechecks_completion_approval_and_manual_interruption(self):
        for kind in ('completed','interrupted','approval'):
            self.store.arm(THREAD, 3)
            with patch.object(self.desktop, 'snapshot', side_effect=ThreadUnavailable):
                self.assertTrue(self.controller.step())
            self.desktop.data = state(kind if kind != 'approval' else 'failed')
            if kind == 'approval': self.desktop.data['requests'] = [{'method':'approval'}]
            self.assertFalse(self.controller.step())
            self.assertFalse(self.desktop.calls)


    def test_atomic_claim_across_connections(self):
        other = Store(self.temp.name)
        try:
            self.assertIsNotNone(self.store.claim(THREAD, 't1', 'hash'))
            self.assertIsNone(other.claim(THREAD, 't1', 'hash'))
        finally: other.close()

    def test_process_lock_excludes_duplicate_watcher(self):
        self.store.lock(THREAD); other = Store(self.temp.name)
        try:
            with self.assertRaises(RuntimeError): other.lock(THREAD)
        finally: other.close()


class FakeSocket:
    def __init__(self, messages):
        self.data = b''.join(struct.pack('<I', len(b)) + b for b in (json.dumps(m).encode() for m in messages))
        self.sent = []
    def settimeout(self, timeout): pass
    def sendall(self, data): self.sent.append(json.loads(data[4:]))
    def recv(self, n):
        result, self.data = self.data[:min(n, 3)], self.data[min(n, 3):]
        return result


class TransportTests(unittest.TestCase):
    def test_windows_pipe_uses_exact_app_endpoint_and_byte_stream(self):
        class Operation:
            event = object()
            def __init__(self, data=b'', count=None, result_error=0):
                self.data = bytes(data)
                self.count = len(self.data) if count is None else count
                self.result_error = result_error
                self.cancelled = False
            def GetOverlappedResult(self, _wait): return self.count, self.result_error
            def getbuffer(self): return memoryview(self.data)
            def cancel(self): self.cancelled = True
        class API:
            GENERIC_READ=1; GENERIC_WRITE=2; NULL=0; OPEN_EXISTING=3; FILE_FLAG_OVERLAPPED=4
            ERROR_IO_PENDING=997; ERROR_SEM_TIMEOUT=121; ERROR_PIPE_BUSY=231; ERROR_MORE_DATA=234
            ERROR_BROKEN_PIPE=109; WAIT_TIMEOUT=258
            def __init__(self): self.writes=[]; self.closed=[]; self.reads=[b'ab', b'c']
            def WaitNamedPipe(self, endpoint, timeout): self.endpoint, self.wait = endpoint, timeout
            def CreateFile(self, *args): self.created=args; return 42
            def WriteFile(self, _handle, data, overlapped):
                self.writes.append(bytes(data)); return Operation(count=len(data)), 0
            def ReadFile(self, _handle, _size, overlapped):
                data = self.reads.pop(0)
                return Operation(data, result_error=self.ERROR_MORE_DATA if data == b'ab' else 0), 0
            def PeekNamedPipe(self, _handle): return (len(self.reads), 0)
            def WaitForMultipleObjects(self, *_args): return 0
            def CloseHandle(self, handle): self.closed.append(handle)
        api=API(); pipe=WindowsPipe(timeout=1, api=api)
        self.assertEqual(api.endpoint, WINDOWS_PIPE)
        pipe.sendall(b'frame'); self.assertEqual(api.writes, [b'frame'])
        self.assertEqual(pipe.recv(2), b'ab'); self.assertTrue(pipe.has_data())
        pipe.close(); self.assertEqual(api.closed, [42])

    @patch('codex_resume.app.windows_package_version', return_value='26.901.31953.0')
    @patch('codex_resume.app.subprocess.run')
    def test_windows_version_requires_exact_package_and_cli(self, run, package):
        run.return_value = SimpleNamespace(returncode=0, stdout='codex-cli 0.153.1\n')
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'codex.exe'; binary.touch()
            result=check_version(binary, 'windows')
        self.assertEqual(result['platform'], 'windows')
        self.assertEqual(result['cliVersion'], '0.153.1')

    @patch('codex_resume.app.windows_package_version', return_value='99.0.0.0')
    @patch('codex_resume.app.subprocess.run')
    def test_windows_future_version_fails_closed(self, run, package):
        run.return_value = SimpleNamespace(returncode=0, stdout='codex-cli 0.153.1\n')
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'codex.exe'; binary.touch()
            with self.assertRaises(AppError): check_version(binary, 'windows')

    @patch('codex_resume.app.check_version')
    def test_resume_observes_already_queued_ipc_events_before_dispatch(self, check):
        import socket
        def broadcast(change, owner='owner'):
            return {'type': 'broadcast', 'sourceClientId': owner,
                    'method': 'thread-stream-state-changed', 'version': 11,
                    'params': {'hostId': 'local', 'conversationId': THREAD, 'change': change}}
        initial = broadcast({'type': 'snapshot', 'revision': 1, 'conversationState': state()})
        changed = state(); changed['requests'] = [{'method': 'approval'}]
        cases = [
            ('patch', [broadcast({'type': 'patches', 'revision': 2, 'patches': []})], AppError),
            ('snapshot', [broadcast({'type': 'snapshot', 'revision': 2, 'conversationState': changed})], AppError),
            ('unknown-change', [broadcast({'type': 'future-format'})], AppError),
            ('unrelated', [broadcast({'type': 'patches'}, 'unrelated-owner')], None),
            ('busy', [{'type': 'notification'}] * 129, AppError),
            ('partial-frame', [], (TimeoutError, socket.timeout)),
        ]
        for name, pending, failure in cases:
            with self.subTest(name=name):
                client, peer = socket.socketpair()
                try:
                    client.settimeout(.05); peer.settimeout(.05)
                    app = Desktop('/unused', timeout=.05); app.sock = client
                    data = FakeSocket([initial] + pending).data
                    if name == 'partial-frame': data += struct.pack('<I', 12) + b'{'
                    peer.sendall(data)
                    calls = []
                    def request(method, *args, **kwargs):
                        if method == 'thread-owner-discovery': return {'handledByClientId': 'owner'}
                        calls.append(method)
                        return {'result': {'result': {'turn': {'id': 'new'}}}}
                    app.request = request
                    if failure:
                        with self.assertRaises(failure):
                            app.resume(THREAD, fingerprint(state()), 'synthetic-message')
                        self.assertEqual(calls, [])
                    else:
                        self.assertEqual(app.resume(THREAD, fingerprint(state()), 'synthetic-message'), 'new')
                        self.assertEqual(calls, ['thread-follower-start-turn'])
                finally:
                    client.close(); peer.close()

    def test_fragmented_frames(self):
        app = Desktop('/unused'); app.sock = FakeSocket([{'type': 'response', 'result': '中文'}])
        self.assertEqual(app._read(time.monotonic()+1)['result'], '中文')

    def test_bad_length(self):
        app = Desktop('/unused'); app.sock = FakeSocket([])
        app.sock.data = struct.pack('<I', MAX_FRAME + 1)
        with self.assertRaises(AppError): app._read(time.monotonic()+1)

    def test_approval_requests_never_accepted(self):
        app = Desktop('/unused'); app.sock = FakeSocket([{'type': 'request', 'requestId': 'r', 'method': 'approval'}])
        app._read(time.monotonic()+1)
        self.assertEqual(app.sock.sent[0]['resultType'], 'error')

    def test_unrelated_state_is_ignored(self):
        app = Desktop('/unused'); app.owner='owner'; app.thread_id=THREAD
        app.sock = FakeSocket([{'type':'broadcast','sourceClientId':'other','method':'thread-stream-state-changed','version':11,
                               'params':{'hostId':'local','conversationId':THREAD,'change':{'type':'snapshot','conversationState':state()}}}])
        app._read(time.monotonic()+1); self.assertIsNone(app.snapshot_value)

    @patch('codex_resume.app.check_version')
    def test_resume_inherits_settings_and_rechecks(self, check):
        app=Desktop('/unused'); app.owner='owner'; app.snapshot=lambda tid: state()
        calls=[]
        def request(method, params, version, target):
            calls.append((method,params));return {'result':{'result':{'turn':{'id':'new'}}}}
        app.request=request
        with patch.object(app, '_drain_pending'):
            self.assertEqual(app.resume(THREAD,fingerprint(state()),'message'),'new')
        request=calls[0][1]['turnStart']['request']
        self.assertEqual(set(request), {'threadId','clientUserMessageId','input'})
        with self.assertRaises(AppError): app.resume(THREAD,'changed','message2')
        self.assertEqual(len(calls),1)

    def test_read_only_rpc_refuses_mutations(self):
        server=ReadOnlyServer('/unused','/unused')
        for method in ('turn/start','account/rateLimitResetCredit/consume','account/login/start'):
            with self.assertRaises(ValueError): server.query(method,{})


class StartupTests(unittest.TestCase):
    @patch('codex_resume.__main__.platform_name', return_value='windows')
    @patch('codex_resume.__main__.check_version')
    @patch('codex_resume.__main__.inspect_task', return_value={'canMonitor': True})
    def test_windows_start_uses_detached_process_without_inherited_descriptor(self, inspect, version, system):
        from codex_resume.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            def spawn(*args, **kwargs):
                self.assertNotIn('pass_fds', kwargs)
                self.assertNotIn('start_new_session', kwargs)
                self.assertTrue(kwargs['creationflags'] & 0x00000008)
                child_store = Store(directory)
                try:
                    child_store.update(THREAD, 'watching', 'synthetic Windows child')
                finally:
                    child_store.close()
                return SimpleNamespace(pid=321, poll=lambda: None)
            with patch('codex_resume.__main__.subprocess.Popen', side_effect=spawn):
                main(['--state-dir', directory, 'start', THREAD])
            ledger = Store(directory)
            try:
                self.assertEqual(ledger.get(THREAD)['status'], 'watching')
                ledger.lock(THREAD)
            finally:
                ledger.close()

    @unittest.skipIf(os.name == 'nt', 'POSIX inherited descriptor test')
    @patch('codex_resume.__main__.check_version')
    @patch('codex_resume.__main__.inspect_task', return_value={'canMonitor': True})
    def test_second_start_during_spawn_cannot_replace_budget(self, inspect, version):
        from codex_resume.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            def fail_spawn(*args, **kwargs):
                self.assertEqual(len(kwargs['pass_fds']), 1)
                with self.assertRaisesRegex(RuntimeError, '已有一个监控进程'):
                    main(['--state-dir', directory, 'start', THREAD, '--max-resumes', '7'])
                ledger = Store(directory)
                try:
                    self.assertEqual(ledger.get(THREAD)['max_resumes'], 3)
                finally:
                    ledger.close()
                raise OSError('synthetic spawn failure')
            with patch('codex_resume.__main__.subprocess.Popen', side_effect=fail_spawn):
                with self.assertRaises(OSError):
                    main(['--state-dir', directory, 'start', THREAD])
            ledger = Store(directory)
            try:
                self.assertEqual(ledger.get(THREAD)['enabled'], 0)
                ledger.lock(THREAD)  # Failed launch released the parent's lock.
            finally:
                ledger.close()

    @patch('codex_resume.__main__.check_version')
    @patch('codex_resume.__main__.inspect_task', return_value={'canMonitor': True})
    def test_log_or_spawn_failure_disables_starting_watch(self, inspect, version):
        from codex_resume.__main__ import main
        failures = ('spawn', 'spawn_after_stop') if os.name == 'nt' else ('log', 'spawn', 'spawn_after_stop')
        for failure in failures:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                if failure == 'log':
                    (Path(directory) / (THREAD + '.log')).symlink_to(Path(directory) / 'absent')
                def fail_spawn(*args, **kwargs):
                    if failure == 'spawn_after_stop':
                        other = Store(directory)
                        try:
                            other.stop(THREAD)
                        finally:
                            other.close()
                    raise OSError('synthetic spawn failure')
                with patch('codex_resume.__main__.subprocess.Popen', side_effect=fail_spawn) as spawn:
                    with self.assertRaises((OSError, RuntimeError)):
                        main(['--state-dir', directory, 'start', THREAD])
                    if failure == 'log':
                        spawn.assert_not_called()
                ledger = Store(directory)
                try:
                    row = ledger.get(THREAD)
                    self.assertEqual(row['enabled'], 0)
                    if failure == 'spawn_after_stop':
                        self.assertEqual(row['status'], 'paused')
                    else:
                        self.assertEqual(row['status'], 'blocked')
                finally:
                    ledger.close()


class IntegrationTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX inherited descriptor test')
    def test_inherited_lock_survives_parent_close_and_is_not_reinherited(self):
        import select
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            parent = Store(directory)
            parent.lock(THREAD)
            fd = parent.lock_file.fileno()
            code = ('import os,sys; from codex_resume.store import Store; '
                    's=Store(sys.argv[1]); s.lock(sys.argv[2], inherited_fd=int(sys.argv[3])); '
                    'assert not os.get_inheritable(s.lock_file.fileno()); '
                    'print("held",flush=True); sys.stdin.readline(); s.close()')
            child = subprocess.Popen([sys.executable, '-c', code, directory, THREAD, str(fd)],
                pass_fds=(fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            try:
                self.assertTrue(select.select([child.stdout], [], [], 5)[0])
                self.assertEqual(child.stdout.readline().strip(), 'held')
                parent.close()
                other = Store(directory)
                try:
                    with self.assertRaises(RuntimeError): other.lock(THREAD)
                    child.communicate('\n', timeout=5)
                    self.assertEqual(child.returncode, 0)
                    other.lock(THREAD)
                finally:
                    other.close()
            finally:
                if parent.lock_file and not parent.lock_file.closed:
                    parent.close()
                if child.poll() is None:
                    child.kill(); child.communicate(timeout=5)

    @unittest.skipIf(os.name == 'nt', 'POSIX inherited descriptor test')
    def test_inherited_lock_rejects_unrelated_file_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Store(directory)
            (Path(directory) / (THREAD + '.lock')).touch(mode=0o600)
            try:
                with tempfile.TemporaryFile(dir=directory) as unrelated:
                    with self.assertRaises(RuntimeError):
                        ledger.lock(THREAD, inherited_fd=unrelated.fileno())
            finally:
                ledger.close()

    @unittest.skipIf(os.name == 'nt', 'portable subprocess fixture uses a POSIX shebang')
    def test_rpc_real_subprocess_coalesced_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'server'
            script.write_text("#!/usr/bin/env python3\n" + """
import json,sys
for line in sys.stdin:
    data=json.loads(line)
    if 'id' not in data: continue
    result={'ok': True} if data['method']=='initialize' else {'rateLimits': {'test': True}}
    sys.stdout.write(json.dumps({'method':'notification','params':{}})+'\\n'+json.dumps({'id':data['id'],'result':result})+'\\n')
    sys.stdout.flush()
""")
            script.chmod(0o700)
            with ReadOnlyServer(script, directory, timeout=10) as server:
                self.assertEqual(server.query('account/rateLimits/read'), {'rateLimits': {'test': True}})
                process = server.process
            self.assertIsNotNone(process.poll())

    @unittest.skipIf(os.name == 'nt', 'portable subprocess fixture uses a POSIX shebang')
    def test_rpc_windows_reader_does_not_use_fd_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'server'
            script.write_text("#!/usr/bin/env python3\n" + """
import json,sys
for line in sys.stdin:
    data=json.loads(line)
    if 'id' not in data: continue
    result={'ok': True} if data['method']=='initialize' else {'rateLimits': {'windows': True}}
    sys.stdout.write(json.dumps({'id':data['id'],'result':result})+'\\n'); sys.stdout.flush()
""")
            script.chmod(0o700)
            with ReadOnlyServer(script, directory, timeout=10, system='windows') as server:
                self.assertEqual(server.query('account/rateLimits/read'), {'rateLimits': {'windows': True}})
                self.assertIsNone(server.selector)

    def test_version_mismatch_is_closed(self):
        import plistlib
        from codex_resume.app import check_version
        with tempfile.TemporaryDirectory() as directory:
            contents=Path(directory)/'Contents'; contents.mkdir()
            (contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.openai.codex',
                'CFBundleVersion':'future', 'CFBundleShortVersionString':'next'}))
            with self.assertRaises(AppError): check_version(directory)

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership and mode test')
    def test_private_directory_rejects_shared_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory).chmod(0o755)
            with self.assertRaises(RuntimeError): Store(directory)

    def test_ledger_survives_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first=Store(directory); first.arm(THREAD,3)
            first.claim(THREAD,'failed','baseline');first.close()
            second=Store(directory)
            try:
                self.assertTrue(second.uncertain(THREAD))
                self.assertIsNone(second.claim(THREAD,'failed','baseline'))
            finally:second.close()


if __name__ == '__main__': unittest.main()
