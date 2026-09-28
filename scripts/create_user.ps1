param(
    [Parameter(Mandatory=$true)][string]$Username,
    [ValidateSet('admin','analyst','reviewer')][string]$Role = 'analyst'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
$securePassword = Read-Host 'New account password (at least 12 characters)' -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $env:LOGFORGE_BOOTSTRAP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    Set-Location -LiteralPath $projectRoot
    & $pythonPath scripts/run_local.py --create-user $Username --role $Role
    if ($LASTEXITCODE -ne 0) { throw 'Account creation failed.' }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    Remove-Item Env:\LOGFORGE_BOOTSTRAP_PASSWORD -ErrorAction SilentlyContinue
}

