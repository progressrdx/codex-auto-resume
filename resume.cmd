@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
  if errorlevel 1 goto :badpython
  py -3 -m codex_resume %*
  exit /b
)

where python >nul 2>nul
if errorlevel 1 goto :missing
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 goto :badpython
python -m codex_resume %*
exit /b

:missing
echo 未找到 Python 3。请先安装 Python 3.9 或更高版本。 1>&2
exit /b 127

:badpython
echo Python 版本过低，需要 Python 3.9 或更高版本。 1>&2
exit /b 2
