"""New Windows contract regressions; fake App, never a real user task."""
import copy
import json
from pathlib import Path
import struct
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from codex_resume.app import AppError, CONTINUATION, Desktop, check_version
from codex_resume.controller import Controller
from codex_resume.policy import decide, fingerprint
from codex_resume.store import Store


THREAD = '11111111-1111-4111-8111-111111111111'
APP = '26.1021.3705.0'
CLI = '0.164.0-alpha.11.1'
INFO = {'platform': 'windows', 'version': APP, 'cliVersion': CLI}


def snapshot():
    # Synthetic values, shaped like the statically inspected v11 contract.
    # Optional thread.ephemeral is absent, as with JSON.stringify(undefined).
    return {'id': THREAD, 'hostId': 'local', 'resumeState': 'resumed',
            'requests': [], 'queuedFollowUps': [],
            'threadRuntimeStatus': {'type': 'systemError'},
            'turnHistory': {'kind': 'canonical', 'history': {
                'islands': [{'newerBoundary': {'status': 'exhausted'},
                             'entries': [{'value': 'tail'}]}],
                'entitiesByKey': {'tail': {'turnId': 'quota-failed-turn',
                    'status': 'failed', 'error': {'codexErrorInfo': 'usageLimitExceeded'}}}}}}


class ScriptedPipe:
    """Length-prefixed byte stream, including fragmented reads and v2 replies."""
    def __init__(self, *args):
        self.pending = bytearray()
        self.messages = []
        self.state = snapshot()
        self.state_version = 11

    def sendall(self, data):
        if struct.unpack('<I', data[:4])[0] != len(data) - 4:
            raise AssertionError('invalid frame')
        msg = json.loads(data[4:])
        self.messages.append(msg)
        if msg['type'] == 'request':
            method = msg['method']
            result = ({'clientId': 'reader'} if method == 'initialize' else
                      {'result': {'turn': {'id': 'new-turn'}}}
                      if method == 'thread-follower-start-turn' else {})
            self.enqueue({'type': 'response', 'requestId': msg['requestId'],
                          'method': method, 'resultType': 'success',
                          'handledByClientId': 'owner', 'result': result})
        elif msg.get('method') == 'thread-stream-following-changed' and msg['params']['following']:
            self.enqueue({'type': 'broadcast', 'method': 'thread-stream-state-changed',
                          'version': self.state_version, 'sourceClientId': 'owner',
                          'params': {'hostId': 'local', 'conversationId': THREAD,
                                     'change': {'type': 'snapshot', 'revision': 1,
                                                'conversationState': self.state}}})

    def enqueue(self, message):
        data = json.dumps(message).encode()
        self.pending.extend(struct.pack('<I', len(data)) + data)

    def recv(self, count):
        data = bytes(self.pending[:min(count, 7)])
        del self.pending[:len(data)]
        return data

    def has_data(self):
        return bool(self.pending)

    def settimeout(self, timeout):
        pass

    def close(self):
        pass


class CurrentWindowsTests(unittest.TestCase):
    def version(self, root, app=APP, cli=CLI):
        binary = Path(root) / 'codex.exe'
        binary.touch()
        with patch('codex_resume.app.windows_package_version', return_value=app), \
                patch('codex_resume.app.subprocess.run',
                      return_value=CompletedProcess([], 0, f'codex-cli {cli}\n', '')):
            return check_version(binary, 'windows')

    def test_only_inspected_app_cli_pair_is_supported(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.version(root), INFO)
            self.assertEqual(self.version(root, '26.901.31953.0', '0.153.1')['cliVersion'], '0.153.1')
            for app, cli in ((APP, '0.153.1'), ('26.901.31953.0', CLI),
                             ('26.1021.3706.0', CLI), (APP, CLI + '.2')):
                with self.subTest(app=app, cli=cli), self.assertRaises(AppError):
                    self.version(root, app, cli)

    @patch('codex_resume.app.check_version', return_value=INFO)
    def test_full_exchange_preserves_original_thread_and_start_contract(self, version):
        with Desktop('/unused', system='windows', transport_factory=ScriptedPipe) as app:
            state = app.snapshot(THREAD)
            self.assertFalse(state['ephemeral'])
            self.assertEqual(decide(state).action, 'resume')
            self.assertEqual(app.resume(THREAD, fingerprint(state), 'unique-message'), 'new-turn')
            requests = [msg for msg in app.sock.messages if msg['type'] == 'request']
            self.assertEqual([(m['method'], m['version']) for m in requests], [
                ('initialize', 0), ('thread-owner-discovery', 1),
                ('thread-owner-discovery', 1), ('thread-follower-start-turn', 2)])
            sent = requests[-1]
            self.assertEqual(sent['targetClientId'], 'owner')
            self.assertEqual(sent['params'], {'conversationId': THREAD, 'turnStart': {'request': {
                'threadId': THREAD, 'clientUserMessageId': 'unique-message',
                'input': [{'type': 'text', 'text': CONTINUATION, 'text_elements': []}]}}})

    @patch('codex_resume.app.check_version', return_value=INFO)
    def test_simulated_reset_waits_for_real_query_and_sends_only_once(self, version):
        now = [1000]
        replies = iter([
            {'rateLimits': {'primary': {'usedPercent': 100, 'resetsAt': 1100, 'windowDurationMins': 300}}},
            {'rateLimits': {'primary': {'usedPercent': 0, 'resetsAt': 2000, 'windowDurationMins': 300}}},
        ])
        pipes = []
        def transport(*args):
            pipe = ScriptedPipe()
            pipes.append(pipe)
            return pipe
        def sends():
            return [msg for pipe in pipes for msg in pipe.messages
                    if msg.get('method') == 'thread-follower-start-turn']
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            try:
                store.arm(THREAD, 3)
                controller = Controller(THREAD, store,
                    lambda: Desktop(root, system='windows', transport_factory=transport),
                    lambda: next(replies), clock=lambda: now[0])
                self.assertTrue(controller.step())
                self.assertEqual(sends(), [])
                now[0] = 1100  # Reset time alone does not authorize sending.
                self.assertTrue(controller.step())
                self.assertEqual(sends(), [])
                now[0] = 1116
                self.assertTrue(controller.step())
                self.assertEqual(len(sends()), 1)
                self.assertTrue(controller.step())
                self.assertEqual(len(sends()), 1)
                self.assertEqual(store.count(THREAD), 1)
            finally:
                store.close()

    @patch('codex_resume.app.check_version', return_value=INFO)
    def test_new_build_still_rejects_changed_protocol_and_unsafe_state(self, version):
        with Desktop('/unused', system='windows', transport_factory=ScriptedPipe) as app:
            for value in (True, None):
                app.sock.state = dict(snapshot(), ephemeral=value)
                self.assertEqual(decide(app.snapshot(THREAD)).action, 'stop')
            for field, value in (
                ('requests', [{'method': 'approval'}]),
                ('queuedFollowUps', ['user-message']),
                ('unconfirmedTurnSubmissions', [{'requestId': 'uncertain'}]),
                ('unconfirmedTurnSubmissions', [{'terminal': True}]),
                ('threadGoalResumeConfirmation', {'status': 'active'}),
            ):
                app.sock.state = dict(snapshot(), **{field: value})
                self.assertEqual(decide(app.snapshot(THREAD)).action, 'stop')
            app.sock.state_version = 12
            with self.assertRaisesRegex(AppError, '状态协议'):
                app.snapshot(THREAD)
            self.assertFalse(any(m.get('method') == 'thread-follower-start-turn' for m in app.sock.messages))

    @patch('codex_resume.app.check_version', return_value=INFO)
    def test_change_to_another_supported_build_requires_reconnection(self, version):
        with Desktop('/unused', system='windows', transport_factory=ScriptedPipe) as app:
            state = app.snapshot(THREAD)
            version.return_value = {'platform': 'windows', 'version': '26.901.31953.0', 'cliVersion': '0.153.1'}
            with self.assertRaisesRegex(AppError, '连接期间已更新'):
                app.resume(THREAD, fingerprint(state), 'message')
            self.assertFalse(any(m.get('method') == 'thread-follower-start-turn' for m in app.sock.messages))

    def test_environment_and_uncertain_send_changes_invalidate_fingerprint(self):
        original = dict(snapshot(), ephemeral=False)
        for field, value in (('environments', [{'environmentId': 'remote'}]),
                             ('threadAccess', 'shared'),
                             ('unconfirmedTurnSubmissions', [{'requestId': 'new'}])):
            changed = copy.deepcopy(original)
            changed[field] = value
            self.assertNotEqual(fingerprint(original), fingerprint(changed))


if __name__ == '__main__':
    unittest.main()
