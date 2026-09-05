$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $launcher) {
    & py -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'Python 版本过低，需要 Python 3.9 或更高版本。'
        exit 2
    }
    & py -3 -m codex_resume @args
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    Write-Error '未找到 Python 3。请先安装 Python 3.9 或更高版本。'
    exit 127
}
& python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Python 版本过低，需要 Python 3.9 或更高版本。'
    exit 2
}
& python -m codex_resume @args
exit $LASTEXITCODE
