"""Operating-system paths and process helpers for the Codex desktop app."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


WINDOWS_PIPE = r'\\.\pipe\codex-ipc'
CODEX_EXE = 'codex.exe'


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
    resources = path / 'Contents' / 'Resources'
    # Newer macOS builds package the CLI as a versioned app resource bundle.
    candidates = (resources / 'codex-cli' / 'bin' / 'codex', resources / 'codex')
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def discover_app_path(system=None):
    """Resolve the Windows engine path, preferring the copy the App is running.

    An explicitly supplied --app path never reaches this function. Without
    process evidence, only the documented install directory is searched: the
    legacy ``bin\\codex.exe`` file and one level of hash subdirectories.
    """
    system = system or platform_name()
    default = default_app_path(system)
    if system != 'windows':
        return default
    root = Path(os.path.abspath(default.parent))
    running = _trusted_binaries(_windows_process_paths(CODEX_EXE), root)
    if len(running) > 1:
        raise RuntimeError(_ambiguous_message(
            '检测到多个正在运行的 Codex 引擎', running, '请关闭多余的 Codex App 后再试'))
    if running:
        return running[0]
    installed = _installed_binaries(root)
    if len(installed) > 1:
        raise RuntimeError(_ambiguous_message(
            '安装目录中找到多个 codex.exe', installed, '请启动 Codex App 让正在运行的引擎可被识别'))
    return installed[0] if installed else default


def _trusted_binaries(paths, root):
    """Existing codex.exe files under the trusted install root, deduplicated."""
    candidates, seen = [], set()
    for value in paths:
        path = Path(value)
        if path.name.lower() != CODEX_EXE or not path.is_file():
            continue
        absolute = Path(os.path.abspath(path))
        if not absolute.is_relative_to(root):
            continue
        key = os.path.normcase(str(absolute))
        if key not in seen:
            seen.add(key)
            candidates.append(absolute)
    return candidates


def _installed_binaries(root):
    """Legacy top-level codex.exe and one level of hash subdirectories."""
    candidates = []
    legacy = root / CODEX_EXE
    if legacy.is_file():
        candidates.append(legacy)
    try:
        children = sorted(root.iterdir(), key=lambda item: os.path.normcase(item.name))
    except OSError:
        return candidates
    for child in children:
        binary = child / CODEX_EXE
        if child.is_dir() and binary.is_file():
            candidates.append(binary)
    return candidates


def _ambiguous_message(summary, candidates, hint):
    listing = '\n'.join(f'  {candidate}' for candidate in candidates)
    return f'{summary}，无法可靠选择：\n{listing}\n{hint}；或用 --app 显式指定 codex.exe 路径'


def _windows_process_paths(image_name):
    """Executable paths of running processes with the given image name.

    Best-effort: an unreadable process list yields no paths, which lets the
    caller fall back to searching the install directory.
    """
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize', wintypes.DWORD),
            ('cntUsage', wintypes.DWORD),
            ('th32ProcessID', wintypes.DWORD),
            ('th32DefaultHeapID', ctypes.c_size_t),
            ('th32ModuleID', wintypes.DWORD),
            ('cntThreads', wintypes.DWORD),
            ('th32ParentProcessID', wintypes.DWORD),
            ('pcPriClassBase', wintypes.LONG),
            ('dwFlags', wintypes.DWORD),
            ('szExeFile', wintypes.WCHAR * 260),
        ]

    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = kernel32.Process32FirstW.argtypes
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
    except (AttributeError, OSError):
        return []

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return []
    paths = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.lower() == image_name.lower():
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                              entry.th32ProcessID)
                if handle:
                    buffer = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(buffer))
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        paths.append(buffer.value)
                    kernel32.CloseHandle(handle)
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return paths


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
