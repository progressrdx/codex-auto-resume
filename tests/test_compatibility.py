"""Version diagnosis must never authorize an unverified App or access tasks."""
import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_resume.app import AppError, check_version, compatibility_report
from codex_resume.__main__ import main


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = Path(self.directory.name)
        (self.app / 'Contents').mkdir()
        self.write_info()

    def write_info(self, **overrides):
        info = dict(CFBundleIdentifier='com.openai.codex',
                    CFBundleShortVersionString='26.901.51231', CFBundleVersion='8109')
        info.update(overrides)
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))

    def test_unknown_version_is_diagnosed_but_still_rejected(self):
        report = compatibility_report(self.app, 'macos')
        self.assertFalse(report['versionVerified'])
        self.assertEqual(report['app']['build'], '8109')
        self.assertIn('7119', report['reason'])
        with self.assertRaisesRegex(AppError, '26.901.51231 / 8109'):
            check_version(self.app, 'macos')

    def test_pinned_version_retains_existing_result(self):
        self.write_info(CFBundleShortVersionString='26.820.60940', CFBundleVersion='7119')
        self.assertTrue(compatibility_report(self.app, 'macos')['versionVerified'])
        self.assertEqual(check_version(self.app, 'macos'),
                         dict(platform='macos', version='26.820.60940', build='7119'))

    def test_foreign_or_malformed_metadata_is_not_a_codex_version(self):
        for overrides in [dict(CFBundleIdentifier='foreign.app'),
                          dict(CFBundleVersion=[]), dict(CFBundleVersion='')]:
            with self.subTest(overrides=overrides):
                self.write_info(**overrides)
                with self.assertRaises(AppError):
                    compatibility_report(self.app, 'macos')
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps([]))
        with self.assertRaises(AppError):
            compatibility_report(self.app, 'macos')

    @patch('codex_resume.app.platform_name', return_value='macos')
    def test_diagnostic_avoids_connections_and_task_commands_fail_before_access(self, platform):
        with patch('codex_resume.__main__.ReadOnlyServer') as rpc, \
             patch('codex_resume.__main__.Desktop') as desktop, \
             patch('codex_resume.__main__.Store') as store, \
             patch('codex_resume.__main__.inspect_task') as inspect, \
             patch('codex_resume.__main__.output') as output:
            main(['--app', str(self.app), 'compatibility'])
            self.assertFalse(output.call_args.args[0]['versionVerified'])
            for command in [['doctor'], ['list'],
                            ['check', '00000000-0000-4000-8000-000000000001'],
                            ['start', '00000000-0000-4000-8000-000000000001'],
                            ['_watch', '00000000-0000-4000-8000-000000000001']]:
                with self.subTest(command=command), self.assertRaises(AppError):
                    main(['--app', str(self.app), *command])
            for mock in (rpc, desktop, store, inspect):
                mock.assert_not_called()
