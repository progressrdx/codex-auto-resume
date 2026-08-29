from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class SourceReleaseTests(unittest.TestCase):
    def test_launcher_works_outside_repository(self):
        with tempfile.TemporaryDirectory() as outside:
            result = subprocess.run([ROOT / 'resume', '--help'], cwd=outside,
                                    text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('本地额度恢复续跑工具', result.stdout)

    def test_launcher_rejects_missing_python_without_running_tool(self):
        env = os.environ.copy()
        env['PYTHON_BIN'] = 'definitely-not-a-python-command'
        result = subprocess.run([ROOT / 'resume', 'status'], env=env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 127)
        self.assertIn('Python 3', result.stderr)

    def test_repository_skill_has_valid_entrypoint_and_metadata(self):
        skill = ROOT / '.agents/skills/codex-auto-resume/SKILL.md'
        metadata = ROOT / '.agents/skills/codex-auto-resume/agents/openai.yaml'
        self.assertTrue(skill.is_file())
        self.assertTrue(metadata.is_file())
        content = skill.read_text()
        self.assertTrue(content.startswith('---\nname: codex-auto-resume\n'))
        self.assertIn('`./resume`', content)
        self.assertTrue(os.access(ROOT / 'resume', os.X_OK))

    def test_checkout_excludes_retired_clients_and_packaging_dependencies(self):
        for path in (
            'macos', 'miniprogram', 'package.json', 'package-lock.json',
            'scripts/build_macos_app.sh', 'scripts/macos_cli_entry.py',
            'scripts/sign_notarize_macos.sh', 'tests/test_miniprogram.cjs',
            'tests/test_mobile_http.cjs',
        ):
            with self.subTest(path=path):
                candidate = ROOT / path
                if candidate.is_dir():
                    self.assertFalse(any(item.is_file() for item in candidate.rglob('*')))
                else:
                    self.assertFalse(candidate.exists())


if __name__ == '__main__':
    unittest.main()
