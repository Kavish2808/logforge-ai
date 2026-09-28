param([switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $bootstrapPython = if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') { $pythonCommand.Source } elseif (Test-Path -LiteralPath $bundledPython) { $bundledPython } else { throw 'Install Python 3.11 or newer and add it to PATH.' }
    & $bootstrapPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
}
& $pythonPath -m pip install -r backend/requirements.txt -c backend/requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
if (-not (Test-Path -LiteralPath '.env')) { & $pythonPath scripts/generate_env.py }
if (-not $SkipFrontend) {
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    $bundledPnpm = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd'
    Push-Location frontend
    try {
        if ($npmCommand) {
            if (Test-Path -LiteralPath 'package-lock.json') { & $npmCommand.Source ci }
            else { & $npmCommand.Source install }
        }
        elseif ($pnpmCommand) { & $pnpmCommand.Source install --frozen-lockfile }
        elseif (Test-Path -LiteralPath $bundledPnpm) { & $bundledPnpm install --frozen-lockfile }
        else { throw 'Install Node.js 22 LTS with npm or pnpm.' }
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    } finally { Pop-Location }
}
& $pythonPath scripts/run_local.py --migrate-only
if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
Write-Host 'Setup complete. Create two accounts with scripts/create_user.ps1, then run scripts/start.ps1.'

