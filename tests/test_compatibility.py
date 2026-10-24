"""Offline compatibility boundaries; these are not native App certification."""
import contextlib
import io
import json
from pathlib import Path
import plistlib
import struct
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import Mock, patch

from codex_resume.app import AppError, Desktop, check_version
from codex_resume.__main__ import main


THREAD = '11111111-1111-4111-8111-111111111111'
# Deliberately mismatched new App / stale relocated CLI, not the supported pair.
UNKNOWN = {'platform': 'windows', 'version': '26.1021.3705.0',
           'cliVersion': '0.153.1', 'diagnosticOnly': True,
           'compatibilityReason': '未经续跑验证，仅允许只读诊断'}


class VersionDiagnosticsTests(unittest.TestCase):
    @patch('codex_resume.app.windows_package_version', return_value='26.1021.3705.0')
    @patch('codex_resume.app.subprocess.run')
    def test_new_windows_app_with_stale_cli_is_diagnostic_only(self, run, package):
        run.return_value = CompletedProcess([], 0, 'codex-cli 0.153.1\n', '')
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / 'codex.exe'
            binary.touch()
            info = check_version(binary, 'windows', allow_unverified=True)
            self.assertTrue(info['diagnosticOnly'])
            self.assertEqual(info['version'], '26.1021.3705.0')
            with self.assertRaisesRegex(AppError, '26.1021.3705.0'):
                check_version(binary, 'windows')

    @patch('codex_resume.app.windows_package_version', return_value='26.901.31953.0')
    @patch('codex_resume.app.subprocess.run')
    def test_unknown_cli_stays_diagnostic_and_failed_cli_is_not_accepted(self, run, package):
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / 'codex.exe'
            binary.touch()
            run.return_value = CompletedProcess([], 0, 'codex-cli 99.0.0\n', '')
            self.assertTrue(check_version(binary, 'windows', allow_unverified=True)['diagnosticOnly'])
            with self.assertRaisesRegex(AppError, '99.0.0'):
                check_version(binary, 'windows')
            run.return_value = CompletedProcess([], 1, 'codex-cli 0.153.1\n', '')
            with self.assertRaises(AppError):
                check_version(binary, 'windows', allow_unverified=True)

    def test_macos_unknown_build_and_identity_checks(self):
        with tempfile.TemporaryDirectory() as root:
            contents = Path(root) / 'Contents'
            contents.mkdir()
            info = {'CFBundleIdentifier': 'com.openai.codex',
                    'CFBundleShortVersionString': '26.1002.52244', 'CFBundleVersion': '99999'}
            manifest = contents / 'Info.plist'
            manifest.write_bytes(plistlib.dumps(info))
            self.assertTrue(check_version(root, 'macos', allow_unverified=True)['diagnosticOnly'])
            with self.assertRaisesRegex(AppError, '99999'):
                check_version(root, 'macos')
            for bad in ({'CFBundleIdentifier': 'other.app'}, {'CFBundleVersion': ''}):
                manifest.write_bytes(plistlib.dumps(dict(info, **bad)))
                with self.assertRaises(AppError):
                    check_version(root, 'macos', allow_unverified=True)


class ReadOnlyTransportTests(unittest.TestCase):
    @patch('codex_resume.app.windows_package_version', return_value='26.1021.3705.0')
    @patch('codex_resume.app.subprocess.run')
    def test_unknown_windows_read_only_exchange_with_simulated_pipe(self, run, package):
        """Byte-framed simulation on any OS, NOT a native Windows test."""
        class SimulatedPipe:
            def __init__(self, *args):
                self.messages, self.pending = [], bytearray()

            def sendall(self, data):
                msg = json.loads(data[4:])
                self.assert_size = struct.unpack('<I', data[:4])[0] == len(data) - 4
                self.messages.append(msg)
                if msg['type'] == 'request':
                    result = {'clientId': 'reader'} if msg['method'] == 'initialize' else {}
                    self.enqueue({'type': 'response', 'requestId': msg['requestId'],
                                  'method': msg['method'], 'resultType': 'success',
                                  'handledByClientId': 'owner', 'result': result})
                elif msg['params']['following']:
                    self.enqueue({'type': 'broadcast', 'method': 'thread-stream-state-changed',
                                  'version': 11, 'sourceClientId': 'owner',
                                  'params': {'hostId': 'local', 'conversationId': THREAD,
                                             'change': {'type': 'snapshot', 'revision': 1,
                                                        'conversationState': {'id': THREAD}}}})

            def enqueue(self, msg):
                data = json.dumps(msg).encode()
                self.pending.extend(struct.pack('<I', len(data)) + data)

            def settimeout(self, timeout):
                pass

            def recv(self, size):
                data = bytes(self.pending[:size])
                del self.pending[:size]
                return data

            def close(self):
                pass

        run.return_value = CompletedProcess([], 0, 'codex-cli 0.153.1\n', '')
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / 'codex.exe'
            binary.touch()
            with Desktop(root, binary, system='windows', read_only=True,
                         transport_factory=SimulatedPipe) as app:
                self.assertTrue(app.app_info['diagnosticOnly'])
                self.assertEqual(app.snapshot(THREAD), {'id': THREAD})
                self.assertTrue(app.sock.assert_size)
                self.assertEqual([m['method'] for m in app.sock.messages],
                                 ['initialize', 'thread-owner-discovery',
                                  'thread-stream-following-changed', 'thread-stream-following-changed'])
                before = len(app.sock.messages)
                with self.assertRaises(AppError):
                    app.resume(THREAD, 'fingerprint', 'message-id')
                self.assertEqual(len(app.sock.messages), before)

    def test_resume_blocked_even_on_verified_build(self):
        app = Desktop('/unused', read_only=True)
        app.snapshot = Mock()
        with self.assertRaisesRegex(AppError, '只读'):
            app.resume(THREAD, 'fingerprint', 'message-id')
        app.snapshot.assert_not_called()

    def test_direct_send_cannot_bypass_read_only_guard(self):
        app = Desktop('/unused', read_only=True)
        app.sock = Mock()
        for message in (
            {'type': 'request', 'method': 'thread-follower-start-turn'},
            {'type': 'request', 'method': 'unknown-operation'},
            {'type': 'broadcast', 'method': 'unknown-operation'},
            {'type': 'response', 'resultType': 'success'},
        ):
            with self.subTest(message=message), self.assertRaises(AppError):
                app._send(message)
        app.sock.sendall.assert_not_called()
        for message in (
            {'type': 'request', 'method': 'initialize'},
            {'type': 'request', 'method': 'thread-owner-discovery'},
            {'type': 'broadcast', 'method': 'thread-stream-following-changed'},
            {'type': 'response', 'resultType': 'error'},
        ):
            app._send(message)
        self.assertEqual(app.sock.sendall.call_count, 4)

    @patch('codex_resume.app.check_version', side_effect=AppError('updated'))
    def test_update_is_rechecked_before_reading_state_or_sending(self, version):
        app = Desktop('/unused')
        app.snapshot = Mock()
        with self.assertRaisesRegex(AppError, 'updated'):
            app.resume(THREAD, 'fingerprint', 'message-id')
        app.snapshot.assert_not_called()


class DiagnosticCommandTests(unittest.TestCase):
    def invoke(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            main(list(args))
        return json.loads(stream.getvalue())

    @patch('codex_resume.__main__.ReadOnlyServer')
    @patch('codex_resume.__main__.Desktop')
    @patch('codex_resume.__main__.check_version', return_value=UNKNOWN)
    def test_doctor_reports_connection_without_certifying_automation(self, version, desktop, server):
        server.return_value.__enter__.return_value.query.return_value = {}
        result = self.invoke('doctor')
        self.assertEqual(result['ipc'], 'connected')
        self.assertFalse(result['autoResumeAllowed'])
        self.assertEqual(result['app']['version'], UNKNOWN['version'])
        self.assertEqual(result['reason'], UNKNOWN['compatibilityReason'])
        self.assertIn('quotaReason', result)
        self.assertTrue(desktop.call_args.kwargs['read_only'])
        version.assert_called_once_with(unittest.mock.ANY, allow_unverified=True)

    @patch('codex_resume.__main__.ReadOnlyServer')
    @patch('codex_resume.__main__.check_version', return_value=UNKNOWN)
    def test_failed_probe_keeps_compatibility_context(self, version, server):
        server.return_value.__enter__.side_effect = RuntimeError('private subprocess details')
        with self.assertRaisesRegex(AppError, '仅允许只读诊断') as error:
            self.invoke('doctor')
        self.assertNotIn('private subprocess details', str(error.exception))

    @patch('codex_resume.__main__.inspect_task')
    @patch('codex_resume.__main__.Desktop')
    @patch('codex_resume.__main__.check_version', return_value=UNKNOWN)
    def test_unknown_check_reads_snapshot_without_navigation_or_enrollment(self, version, desktop, inspect):
        result = self.invoke('check', THREAD)
        self.assertFalse(result['canMonitor'])
        self.assertFalse(result['autoResumeAllowed'])
        self.assertEqual(result['decision'], 'stop')
        desktop.return_value.__enter__.return_value.snapshot.assert_called_once_with(THREAD)
        self.assertTrue(desktop.call_args.kwargs['read_only'])
        inspect.assert_not_called()

    @patch('codex_resume.__main__.Desktop')
    @patch('codex_resume.__main__.check_version', return_value=UNKNOWN)
    def test_unknown_snapshot_protocol_still_fails_closed(self, version, desktop):
        desktop.return_value.__enter__.return_value.snapshot.side_effect = AppError('protocol changed')
        with self.assertRaisesRegex(AppError, 'protocol changed'):
            self.invoke('check', THREAD)

    @patch('codex_resume.__main__.ReadOnlyServer')
    @patch('codex_resume.__main__.check_version', return_value=UNKNOWN)
    def test_unknown_version_can_list_without_desktop_write_connection(self, version, server):
        server.return_value.__enter__.return_value.query.return_value = {'data': [], 'nextCursor': None}
        self.assertEqual(self.invoke('list'), [])

    @patch('codex_resume.__main__.Store')
    @patch('codex_resume.__main__.check_version', side_effect=AppError('unverified'))
    def test_start_and_watcher_reject_before_creating_state(self, version, store):
        for command in ('start', '_watch'):
            with self.subTest(command=command), self.assertRaisesRegex(AppError, 'unverified'):
                self.invoke(command, THREAD)
        store.assert_not_called()
        self.assertTrue(all(not call.kwargs for call in version.call_args_list))


if __name__ == '__main__':
    unittest.main()
