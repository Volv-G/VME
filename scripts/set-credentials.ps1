#Requires -Version 5.1
<#
.SYNOPSIS
    Set the username + password used by HTTP Basic Auth.

.DESCRIPTION
    Prompts for a username and password, hashes the password with PBKDF2, and
    writes VME_USERNAME / VME_PASSWORD_HASH / VME_AUTH_REQUIRED into
    <root>/secrets.env. If the MatchEditor service is installed, its environment
    is updated and the service is restarted.

.PARAMETER User
    Optional username (default: prompts).

.PARAMETER Password
    Optional plaintext password (default: prompts twice with masking).
    Avoid passing this on the command line in shared shells.

.PARAMETER NoServiceRestart
    Do not restart the Windows service even if it is installed.
#>
param(
    [string]$User,
    [string]$Password,
    [switch]$NoServiceRestart
)

. "$PSScriptRoot\_common.ps1"
Refresh-Path
Clear-PythonHome

$python = Get-VenvPython
if (-not (Test-Path $python)) {
    Write-Error "Backend venv not found at $python. Run 'uv sync' inside backend/ first."
    exit 1
}

if (-not $User) {
    $User = Read-Host "Username"
}
if (-not $User) {
    Write-Error "Username cannot be empty."
    exit 1
}

if (-not $Password) {
    $sec1 = Read-Host -AsSecureString "Password"
    $sec2 = Read-Host -AsSecureString "Confirm password"
    $p1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec1))
    $p2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec2))
    if ($p1 -ne $p2) {
        Write-Error "Passwords do not match."
        exit 1
    }
    $Password = $p1
}

if ($Password.Length -lt 8) {
    Write-Warning "Password is shorter than 8 characters. Continuing anyway."
}

$backend = Join-Path (Get-VmeRoot) "backend"
Push-Location $backend
try {
    $hash = & $python -m app.auth hash $Password 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0 -or -not $hash) {
        Write-Error "Failed to hash password: $hash"
        exit 1
    }
}
finally {
    Pop-Location
}

Write-SecretsFile @{
    VME_USERNAME       = $User
    VME_PASSWORD_HASH  = $hash
    VME_AUTH_REQUIRED  = "1"
}

Write-Host "Wrote credentials to $(Get-SecretsFile)" -ForegroundColor Green

# Update Windows service environment if installed.
$serviceName = "MatchEditor"
$tools = Join-Path (Get-VmeRoot) "tools"
$nssmExe = Join-Path $tools "nssm.exe"
$svc = Get-Service $serviceName -ErrorAction SilentlyContinue

if ($svc -and (Test-Path $nssmExe)) {
    $secrets = Read-SecretsFile
    $envPairs = @()
    foreach ($key in @("VME_USERNAME", "VME_PASSWORD_HASH", "VME_AUTH_REQUIRED",
                       "VME_CERT_FILE", "VME_KEY_FILE", "VME_HTTP_PORT", "VME_HTTPS_PORT",
                       "VME_BIND_HOST", "VME_PUBLIC_HOST")) {
        if ($secrets.ContainsKey($key) -and $secrets[$key]) {
            $envPairs += "$key=$($secrets[$key])"
        }
    }
    if ($envPairs.Count -gt 0) {
        & $nssmExe set $serviceName AppEnvironmentExtra $envPairs | Out-Null
    }
    if (-not $NoServiceRestart) {
        Write-Host "Restarting $serviceName..." -ForegroundColor Cyan
        & $nssmExe restart $serviceName | Out-Null
    } else {
        Write-Host "Service env updated. Restart it manually for changes to take effect." -ForegroundColor Yellow
    }
} elseif ($svc) {
    Write-Warning "Service '$serviceName' is installed but NSSM is missing at $nssmExe."
}
