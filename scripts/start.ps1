param([switch]$BackendOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
Set-Location -LiteralPath $projectRoot
if ($BackendOnly) { & $pythonPath scripts/run_local.py --backend-only }
else { & $pythonPath scripts/run_local.py }
exit $LASTEXITCODE

