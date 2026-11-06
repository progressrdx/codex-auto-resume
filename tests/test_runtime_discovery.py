"""Offline Windows engine-discovery regressions; never touches a real process."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_resume.__main__ import main, parser
from codex_resume.runtime import codex_binary, default_app_path, discover_app_path

THREAD = '11111111-1111-4111-8111-111111111111'


class WindowsDiscoveryTests(unittest.TestCase):
    """Run discovery against a temporary install tree instead of a real App."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name)
        self.root = self.local / 'OpenAI' / 'Codex' / 'bin'
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)})
        environment.start()
        self.addCleanup(environment.stop)

    def binary(self, *parts):
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def discover(self, *running):
        with patch('codex_resume.runtime._windows_process_paths',
                   return_value=list(running)):
            return discover_app_path('windows')

    def test_legacy_layout_is_used_when_nothing_else_exists(self):
        legacy = self.binary('codex.exe')
        self.assertEqual(self.discover(), legacy)

    def test_hash_subdirectory_layout_is_used(self):
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        self.assertEqual(self.discover(), current)

    def test_discovered_hash_binary_is_used_directly(self):
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        self.assertEqual(codex_binary(self.discover(), 'windows'), current)

    def test_search_stays_inside_the_install_directory(self):
        outside = self.local / 'other' / 'codex.exe'
        outside.parent.mkdir()
        outside.touch()
        self.assertEqual(self.discover(), self.root / 'codex.exe')

    def test_search_does_not_recurse_below_one_subdirectory(self):
        self.binary('parent', 'nested', 'codex.exe')
        self.assertEqual(self.discover(), self.root / 'codex.exe')

    def test_running_engine_wins_over_legacy_copy(self):
        legacy = self.binary('codex.exe')
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        self.assertEqual(self.discover(str(current)), current)
        self.assertEqual(self.discover(str(legacy)), legacy)

    def test_same_file_from_multiple_processes_is_deduplicated(self):
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        self.assertEqual(self.discover(str(current), str(current)), current)

    @unittest.skipUnless(os.name == 'nt', 'Windows paths are case-insensitive')
    def test_same_file_with_different_case_is_deduplicated(self):
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        self.assertEqual(self.discover(str(current), str(current).lower()), current)

    def test_running_engine_outside_install_directory_is_ignored(self):
        installed = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        outside = self.local / 'other' / 'codex.exe'
        outside.parent.mkdir()
        outside.touch()
        self.assertEqual(self.discover(str(outside)), installed)

    def test_stale_process_path_falls_back_to_installed_binary(self):
        installed = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        self.assertEqual(self.discover(str(self.root / 'gone' / 'codex.exe')), installed)

    def test_multiple_running_engines_raise_instead_of_guessing(self):
        first = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        second = self.binary('c0ffee', 'codex.exe')
        with self.assertRaisesRegex(RuntimeError, '多个正在运行'):
            self.discover(str(first), str(second))

    def test_legacy_and_hash_candidates_are_ambiguous(self):
        self.binary('codex.exe')
        self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        with self.assertRaisesRegex(RuntimeError, '多个 codex.exe'):
            self.discover()

    def test_two_hash_candidates_are_ambiguous(self):
        self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        self.binary('c0ffee', 'codex.exe')
        with self.assertRaisesRegex(RuntimeError, '多个 codex.exe'):
            self.discover()

    def test_no_candidate_keeps_the_legacy_default(self):
        self.assertEqual(self.discover(), self.root / 'codex.exe')

    def test_macos_keeps_the_fixed_app_bundle(self):
        with patch('codex_resume.runtime._windows_process_paths',
                   side_effect=AssertionError('Windows only')):
            self.assertEqual(discover_app_path('macos'), default_app_path('macos'))


class CommandWiringTests(unittest.TestCase):
    """Every command resolves the engine once, and --app always comes first."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = self.temp.name

    def invoke(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            main(['--home', self.state, '--state-dir', self.state, *args])
        value = stream.getvalue()
        return json.loads(value) if value.strip() else None

    def test_parser_defers_discovery_until_a_command_runs(self):
        self.assertIsNone(parser().parse_args(['status']).app)

    @patch('codex_resume.__main__.discover_app_path', side_effect=RuntimeError('ambiguous'))
    def test_status_is_not_blocked_by_ambiguous_discovery(self, discover):
        self.assertEqual(self.invoke('status'), [])
        discover.assert_not_called()

    @patch('codex_resume.__main__.ReadOnlyServer')
    @patch('codex_resume.__main__.Desktop')
    @patch('codex_resume.__main__.check_version', return_value={'platform': 'windows'})
    @patch('codex_resume.__main__.discover_app_path', return_value=Path('/detected/codex.exe'))
    def test_doctor_uses_the_discovered_engine(self, discover, version, desktop, server):
        server.return_value.__enter__.return_value.query.return_value = {}
        self.invoke('doctor')
        discover.assert_called_once_with()
        version.assert_called_once_with(Path('/detected/codex.exe').expanduser().absolute())

    @patch('codex_resume.__main__.inspect_task')
    @patch('codex_resume.__main__.check_version', return_value={'platform': 'windows'})
    @patch('codex_resume.__main__.discover_app_path', return_value=Path('/detected/codex.exe'))
    def test_check_uses_the_discovered_engine(self, discover, version, inspect):
        inspect.return_value = {'canMonitor': False, 'decision': 'stop', 'reason': 'no'}
        self.invoke('check', THREAD)
        inspect.assert_called_once_with(Path(self.state).expanduser().absolute(),
                                        Path('/detected/codex.exe').expanduser().absolute(),
                                        THREAD, list_timeout=55)

    @patch('codex_resume.__main__.ReadOnlyServer')
    @patch('codex_resume.__main__.check_version', return_value={'platform': 'windows'})
    @patch('codex_resume.__main__.discover_app_path', return_value=Path('/detected/codex.exe'))
    def test_list_uses_the_discovered_engine(self, discover, version, server):
        server.return_value.__enter__.return_value.query.return_value = {
            'data': [], 'nextCursor': None}
        self.assertEqual(self.invoke('list'), [])
        discover.assert_called_once_with()

    @patch('codex_resume.web.serve')
    @patch('codex_resume.__main__.discover_app_path', return_value=Path('/detected/codex.exe'))
    def test_web_starts_with_the_discovered_engine(self, discover, serve):
        self.invoke('web')
        serve.assert_called_once()
        self.assertEqual(serve.call_args.args[0].app,
                         Path('/detected/codex.exe').expanduser().absolute())

    @patch('codex_resume.__main__.ReadOnlyServer')
    @patch('codex_resume.__main__.Desktop')
    @patch('codex_resume.__main__.check_version', return_value={'platform': 'windows'})
    @patch('codex_resume.__main__.discover_app_path',
           side_effect=AssertionError('explicit --app must not discover'))
    def test_explicit_app_path_has_priority(self, discover, version, desktop, server):
        server.return_value.__enter__.return_value.query.return_value = {}
        chosen = Path(self.state) / 'chosen' / 'codex.exe'
        self.invoke('--app', str(chosen), 'doctor')
        discover.assert_not_called()
        version.assert_called_once_with(chosen.expanduser().absolute())


if __name__ == '__main__':
    unittest.main()
