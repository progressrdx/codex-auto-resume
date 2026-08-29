"""Offline version and tracked-file hygiene checks; never reads local credentials."""
import ast
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent.parent


def verify():
    module = ast.parse((ROOT / 'codex_resume/__init__.py').read_text())
    version = next(ast.literal_eval(node.value) for node in module.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == '__version__'
                           for target in node.targets))
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.]+)?', version):
        raise ValueError('Invalid version format')
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    findings = []
    sensitive = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
                           r'|(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_]{16,}')
    for name in filter(None, paths):
        path = Path(name)
        if (name.startswith('.light-dev/')
                or (name.startswith('tests/evidence/') and name != 'tests/evidence/README.md')
                or path.name == 'project.private.config.json'
                or path.name == '.env' or path.name.startswith('.env.')
                or path.suffix in {'.pem', '.key', '.log', '.db', '.sqlite', '.sqlite3'}):
            findings.append((name, 'private runtime artifact tracked'))
            continue
        content = (ROOT / path).read_bytes()
        if sensitive.search(content.decode('utf-8', errors='replace')):
            findings.append((name, 'credential-like content'))
    if findings:
        # Never echo matched content; even a failed check must not leak a secret.
        raise ValueError('; '.join(f'{name}: {reason}' for name, reason in findings))
    print(f'Version {version}: tracked-file hygiene checks passed')


if __name__ == '__main__':
    try:
        verify()
    except (ValueError, StopIteration) as error:
        raise SystemExit(str(error))
