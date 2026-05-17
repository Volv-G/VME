#Requires -Version 5.1
<#
.SYNOPSIS
    Run the production server in the foreground (HTTPS on :443, redirect on :80).

.DESCRIPTION
    Requires elevated PowerShell because ports < 1024 need administrator rights.
    Use scripts/install-service.ps1 to install as a Windows service instead.
#>

. "$PSScriptRoot\_common.ps1"
Refresh-Path
Clear-PythonHome
Load-VmeEnv

if (-not (Test-Admin)) {
    Write-Error "This script must run as Administrator (ports 80/443 require elevation)."
    exit 1
}

$root = Get-VmeRoot
$backend = Join-Path $root "backend"
$python = Get-VenvPython
$cert = $env:VME_CERT_FILE
if (-not $cert) { $cert = Join-Path $root "certs\cert.pem" }
$key = $env:VME_KEY_FILE
if (-not $key) { $key = Join-Path $root "certs\key.pem" }

if (-not (Test-Path $cert) -or -not (Test-Path $key)) {
    Write-Error "Cert/key not found ($cert / $key). Run scripts/generate-cert.ps1 or scripts/set-cert.ps1 first."
    exit 1
}

if (-not $env:VME_USERNAME -or -not $env:VME_PASSWORD_HASH) {
    Write-Warning "No credentials configured. The server will be UNAUTHENTICATED."
    Write-Warning "Run scripts/set-credentials.ps1 before exposing this beyond localhost."
}

if (-not (Test-Path (Join-Path $backend "static\index.html"))) {
    Write-Warning "backend/static is empty. Run scripts/build.ps1 first or the UI will not load."
}

Write-Host "Starting VME on https://localhost (and redirect on http://localhost)..." -ForegroundColor Green
Push-Location $backend
try {
    & $python -m app.serve
}
finally {
    Pop-Location
}
