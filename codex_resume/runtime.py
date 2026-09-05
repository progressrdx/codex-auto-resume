"""Operating-system paths and process helpers for the Codex desktop app."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


WINDOWS_PIPE = r'\\.\pipe\codex-ipc'


def platform_name():
    return 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else sys.platform


def default_app_path(system=None):
    system = system or platform_name()
    if system == 'windows':
        local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
        # The Store/MSIX app relocates its bundled CLI here so it remains
        # executable outside the protected WindowsApps directory.
        return local / 'OpenAI' / 'Codex' / 'bin' / 'codex.exe'
    return Path('/Applications/ChatGPT.app')


def codex_binary(app_path, system=None):
    """Resolve the App-bundled CLI without searching arbitrary PATH entries."""
    system = system or platform_name()
    path = Path(app_path)
    if system == 'windows':
        candidates = []
        if path.name.lower() == 'codex.exe':
            candidates.append(path)
        elif path.suffix.lower() == '.exe':
            candidates.extend((path.parent / 'resources' / 'codex.exe', path.parent / 'codex.exe'))
        else:
            candidates.extend((path / 'codex.exe', path / 'resources' / 'codex.exe',
                               path / 'OpenAI' / 'Codex' / 'bin' / 'codex.exe'))
        return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    return path / 'Contents' / 'Resources' / 'codex'


def windows_package_version():
    """Read the current user's Store/MSIX package version without changing it."""
    shell = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if not shell:
        raise RuntimeError('未找到 PowerShell，无法核验 Windows Codex App 版本')
    script = ("$p=Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | "
              "Select-Object -First 1; if ($null -eq $p) { exit 3 }; $p.Version.ToString()")
    result = subprocess.run([shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=10)
    value = result.stdout.strip()
    if result.returncode or not value:
        raise RuntimeError('找不到当前用户安装的 Windows Codex App')
    return value


def process_exists(pid):
    if platform_name() != 'windows':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    import ctypes
    query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(query_limited_information, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True
