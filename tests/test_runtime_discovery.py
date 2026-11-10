"""Offline Windows engine-discovery regressions; never reads the real machine."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_resume.__main__ import main, parser
from codex_resume.runtime import codex_binary, default_app_path, discover_app_path

THREAD = '11111111-1111-4111-8111-111111111111'
# The App is an MSIX package: its processes run from the package directory and
# it starts the engine from there, while the engine binary itself lives under
# %LOCALAPPDATA%\OpenAI\Codex\bin, which discovery treats as the install root.
APP_HOST = 4000
APP_HOST_IMAGE = (r'C:\Program Files\WindowsApps\OpenAI.Codex_26.1103.4239.0_x64__'
                  r'2p2nqsd0c76g0\app\ChatGPT.exe')
PYTHON_IMAGE = r'C:\Python312\python.exe'
GONE_PARENT = 9000


class ProcessTable:
    """Synthetic Toolhelp32 snapshot; discovery never reads the real machine."""

    def __init__(self):
        self.rows = [(APP_HOST, 1, 'ChatGPT.exe')]
        self.images = {APP_HOST: APP_HOST_IMAGE}
        self.next_pid = 5000

    def add(self, name, parent, image):
        pid = self.next_pid
        self.next_pid += 1
        self.rows.append((pid, parent, name))
        self.images[pid] = str(image)
        return pid

    def engine(self, path, parent=APP_HOST):
        return self.add('codex.exe', parent, path)

    def python(self):
        return self.add('python.exe', 1, PYTHON_IMAGE)

    def image(self, pid):
        return self.images.get(pid)

    def apply(self):
        stack = contextlib.ExitStack()
        stack.enter_context(patch('codex_resume.runtime._windows_process_table',
                                  return_value=list(self.rows)))
        stack.enter_context(patch('codex_resume.runtime._windows_image_path',
                                  side_effect=self.image))
        return stack


def directory_link(link, target):
    """Create a directory symlink or junction; False when the OS refuses."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name != 'nt':
        return False
    created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                             capture_output=True, text=True)
    return created.returncode == 0


def file_link(link, target):
    """Create a file symlink; False when the OS refuses (Windows needs a privilege)."""
    try:
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError):
        return False


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

    def discover(self, table=None):
        with (table or ProcessTable()).apply():
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
        table = ProcessTable()
        table.engine(current)
        self.assertEqual(self.discover(table), current)
        table = ProcessTable()
        table.engine(legacy)
        self.assertEqual(self.discover(table), legacy)

    def test_same_file_from_multiple_processes_is_deduplicated(self):
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        table = ProcessTable()
        table.engine(current)
        table.engine(current)
        self.assertEqual(self.discover(table), current)

    @unittest.skipUnless(os.name == 'nt', 'Windows paths are case-insensitive')
    def test_same_file_with_different_case_is_deduplicated(self):
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        table = ProcessTable()
        table.engine(current)
        table.engine(str(current).lower())
        self.assertEqual(self.discover(table), current)

    def test_running_engine_outside_install_directory_is_ignored(self):
        installed = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        outside = self.local / 'other' / 'codex.exe'
        outside.parent.mkdir()
        outside.touch()
        table = ProcessTable()
        table.engine(outside)
        self.assertEqual(self.discover(table), installed)

    def test_stale_process_path_falls_back_to_installed_binary(self):
        installed = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        table = ProcessTable()
        table.engine(self.root / 'gone' / 'codex.exe')
        self.assertEqual(self.discover(table), installed)

    def test_multiple_running_engines_raise_instead_of_guessing(self):
        first = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        second = self.binary('c0ffee', 'codex.exe')
        table = ProcessTable()
        table.engine(first)
        table.engine(second)
        with self.assertRaisesRegex(RuntimeError, '多个正在运行'):
            self.discover(table)

    def test_watcher_app_server_is_not_engine_evidence(self):
        """The bug from review: our own app-server must not look like the App."""
        legacy = self.binary('codex.exe')
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        table = ProcessTable()
        python = table.python()
        table.engine(legacy, parent=python)
        table.engine(current)
        self.assertEqual(self.discover(table), current)

    def test_orphaned_query_process_is_not_engine_evidence(self):
        installed = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        table = ProcessTable()
        table.engine(self.root / 'old-hash' / 'codex.exe', parent=GONE_PARENT)
        self.assertEqual(self.discover(table), installed)

    def test_engine_started_by_an_unrelated_process_is_not_evidence(self):
        installed = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        table = ProcessTable()
        explorer = table.add('explorer.exe', 1, r'C:\Windows\explorer.exe')
        table.engine(self.root / 'manual' / 'codex.exe', parent=explorer)
        self.assertEqual(self.discover(table), installed)

    def test_attributed_engine_resolves_an_ambiguous_install(self):
        self.binary('codex.exe')
        current = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        table = ProcessTable()
        table.engine(current)
        self.assertEqual(self.discover(table), current)

    def test_legacy_and_hash_candidates_are_ambiguous(self):
        self.binary('codex.exe')
        self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        with self.assertRaisesRegex(RuntimeError, '多个 codex.exe'):
            self.discover()

    def test_unattributed_process_is_named_in_the_ambiguity_hint(self):
        legacy = self.binary('codex.exe')
        self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        table = ProcessTable()
        table.engine(legacy, parent=table.python())
        with self.assertRaisesRegex(RuntimeError, '无法确认它属于 App'):
            self.discover(table)

    def test_two_hash_candidates_are_ambiguous(self):
        self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        self.binary('c0ffee', 'codex.exe')
        with self.assertRaisesRegex(RuntimeError, '多个 codex.exe'):
            self.discover()

    def test_no_candidate_keeps_the_legacy_default(self):
        self.assertEqual(self.discover(), self.root / 'codex.exe')

    def test_macos_keeps_the_fixed_app_bundle(self):
        with patch('codex_resume.runtime._windows_process_table',
                   side_effect=AssertionError('Windows only')):
            self.assertEqual(discover_app_path('macos'), default_app_path('macos'))


class LinkEscapeTests(unittest.TestCase):
    """Links and junctions must never carry discovery outside the install root."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name)
        self.root = self.local / 'OpenAI' / 'Codex' / 'bin'
        self.root.mkdir(parents=True)
        self.outside = self.local / 'outside'
        self.outside.mkdir()
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)})
        environment.start()
        self.addCleanup(environment.stop)

    def binary(self, *parts):
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def discover(self, table=None):
        with (table or ProcessTable()).apply():
            return discover_app_path('windows')

    def test_linked_hash_directory_outside_the_root_is_not_searched(self):
        (self.outside / 'codex.exe').touch()
        if not directory_link(self.root / 'linked', self.outside):
            self.skipTest('cannot create a directory link here')
        self.assertEqual(self.discover(), self.root / 'codex.exe')

    def test_running_engine_behind_an_escaping_link_is_ignored(self):
        installed = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        (self.outside / 'codex.exe').touch()
        if not directory_link(self.root / 'linked', self.outside):
            self.skipTest('cannot create a directory link here')
        table = ProcessTable()
        table.engine(self.root / 'linked' / 'codex.exe')
        self.assertEqual(self.discover(table), installed)

    def test_link_that_stays_inside_the_root_is_accepted(self):
        target = self.binary('0a1b2c3d4e5f6a7b', 'codex.exe')
        legacy = self.root / 'codex.exe'
        if not file_link(legacy, target):
            self.skipTest('cannot create a file link here')
        self.assertEqual(self.discover(), legacy)

    def test_legacy_default_linking_outside_the_root_is_refused(self):
        outside = self.local / 'elsewhere.exe'
        outside.touch()
        legacy = self.root / 'codex.exe'
        if not file_link(legacy, outside):
            self.skipTest('cannot create a file link here')
        with self.assertRaisesRegex(RuntimeError, '安装目录之外'):
            self.discover()


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
    @patch('codex_resume.__main__.discover_app_path', side_effect=RuntimeError('ambiguous'))
    def test_web_starts_without_resolving_the_engine(self, discover, serve):
        self.invoke('web')
        serve.assert_called_once()
        self.assertIsNone(serve.call_args.args[0].app)
        discover.assert_not_called()

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
