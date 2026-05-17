#Requires -Version 5.1
<#
.SYNOPSIS
    Install VME as a Windows service named `MatchEditor` using NSSM.

.DESCRIPTION
    Two operating modes are supported, picked from secrets.env (VME_BEHIND_PROXY):

      proxy mode (VME_BEHIND_PROXY=1, set by scripts/setup-iis.ps1):
        - Runs uvicorn on 127.0.0.1:<VME_BACKEND_PORT> (default 8000) as plain HTTP
        - No firewall changes, no cert needed: IIS owns 80/443 in front
        - Trusts X-Forwarded-* headers from 127.0.0.1

      standalone mode (VME_BEHIND_PROXY unset/0):
        - Runs `python -m app.serve` (Hypercorn binding 80 + 443 with TLS)
        - Opens TCP 80/443 inbound in Windows Firewall
        - Requires VME_CERT_FILE / VME_KEY_FILE

    Always required:
        - VME_USERNAME and VME_PASSWORD_HASH must be set (run set-credentials.ps1)
        - frontend built into backend/static/ (run build.ps1)

    Must run as Administrator.
#>

. "$PSScriptRoot\_common.ps1"
Refresh-Path
Clear-PythonHome

if (-not (Test-Admin)) {
    Write-Error "This script must run as Administrator."
    exit 1
}

$serviceName = "MatchEditor"
$root = Get-VmeRoot
$backend = Join-Path $root "backend"
$python = Get-VenvPython
$logs = Join-Path $root "logs"
$tools = Join-Path $root "tools"
$nssmExe = Join-Path $tools "nssm.exe"

if (-not (Test-Path $python)) {
    Write-Error "Backend venv not found at $python. Run 'uv sync' inside backend/ first."
    exit 1
}

if (-not (Test-Path (Join-Path $backend "static\index.html"))) {
    Write-Warning "backend/static is empty - run scripts/build.ps1 before installing the service or the UI will not load."
}

$secrets = Read-SecretsFile
if (-not $secrets["VME_USERNAME"] -or -not $secrets["VME_PASSWORD_HASH"]) {
    Write-Error "No credentials configured. Run scripts/set-credentials.ps1 first."
    exit 1
}

$behindProxy = ($secrets["VME_BEHIND_PROXY"] -eq "1")
$backendPort = if ($secrets["VME_BACKEND_PORT"]) { [int]$secrets["VME_BACKEND_PORT"] } else { 8000 }
$backendHost = if ($secrets["VME_BACKEND_HOST"]) { $secrets["VME_BACKEND_HOST"] } else { "127.0.0.1" }

if ($behindProxy) {
    Write-Host "Installing in proxy mode (uvicorn on $backendHost`:$backendPort, IIS in front)." -ForegroundColor Cyan
} else {
    Write-Host "Installing in standalone mode (Hypercorn on :80 + :443)." -ForegroundColor Cyan
    $cert = if ($secrets["VME_CERT_FILE"]) { $secrets["VME_CERT_FILE"] } else { Join-Path $root "certs\cert.pem" }
    $key  = if ($secrets["VME_KEY_FILE"])  { $secrets["VME_KEY_FILE"]  } else { Join-Path $root "certs\key.pem" }
    if (-not (Test-Path $cert) -or -not (Test-Path $key)) {
        Write-Error "Cert/key not found ($cert / $key). Run scripts/set-cert.ps1 or scripts/generate-cert.ps1 first."
        exit 1
    }
}

# ---- NSSM bootstrap ----------------------------------------------------------
if (-not (Test-Path $nssmExe)) {
    Write-Host "Downloading NSSM..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    $zipUrl = "https://nssm.cc/release/nssm-2.24.zip"
    $zipPath = Join-Path $tools "nssm.zip"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $tools -Force
    $extracted = Get-ChildItem -Path $tools -Directory -Filter "nssm-*" | Select-Object -First 1
    if ($null -eq $extracted) { Write-Error "NSSM extraction failed."; exit 1 }
    $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
    Copy-Item -Path (Join-Path $extracted.FullName "$arch\nssm.exe") -Destination $nssmExe -Force
    Remove-Item $zipPath -ErrorAction SilentlyContinue
    Remove-Item $extracted.FullName -Recurse -Force -ErrorAction SilentlyContinue
}

# ---- Tear down previous install ----------------------------------------------
if (Get-Service $serviceName -ErrorAction SilentlyContinue) {
    Write-Host "Stopping existing service..." -ForegroundColor Yellow
    & $nssmExe stop $serviceName 2>$null | Out-Null
    & $nssmExe remove $serviceName confirm | Out-Null
}

Write-Host "Installing service '$serviceName'..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# ---- Build the command line per mode -----------------------------------------
if ($behindProxy) {
    $svcArgs = @(
        "-m", "uvicorn", "app.main:app",
        "--host", $backendHost,
        "--port", "$backendPort",
        "--proxy-headers",
        "--forwarded-allow-ips", "127.0.0.1"
    )
    if ($secrets["VME_URL_PREFIX"]) {
        $svcArgs += @("--root-path", $secrets["VME_URL_PREFIX"])
    }
} else {
    $svcArgs = @("-m", "app.serve")
}

& $nssmExe install $serviceName $python @svcArgs
& $nssmExe set $serviceName AppDirectory $backend
& $nssmExe set $serviceName Start SERVICE_AUTO_START
& $nssmExe set $serviceName DisplayName "Match Editor (VME)"
& $nssmExe set $serviceName Description "VME Match Editor backend"
& $nssmExe set $serviceName AppStdout (Join-Path $logs "service.log")
& $nssmExe set $serviceName AppStderr (Join-Path $logs "service.log")
& $nssmExe set $serviceName AppRotateFiles 1
& $nssmExe set $serviceName AppRotateBytes 5242880

# Inject env vars from secrets.env. Explicit allowlist - junk in secrets.env
# never makes it into the service environment.
$envPairs = @(
    # NSSM treats "KEY=" (empty) as "unset KEY in the child process".
    # PYTHONHOME is set machine-wide on this box pointing to a different Python
    # version, which would cause our venv's interpreter to load the wrong
    # stdlib and crash with 'AttributeError: module _thread has no attribute
    # start_joinable_thread'. Clear it so venv autodiscovery via pyvenv.cfg
    # works correctly.
    "PYTHONHOME=",
    "PYTHONPATH="
)
foreach ($key in @("VME_USERNAME", "VME_PASSWORD_HASH", "VME_AUTH_REQUIRED",
                   "VME_CERT_FILE", "VME_KEY_FILE",
                   "VME_HTTP_PORT", "VME_HTTPS_PORT", "VME_BIND_HOST",
                   "VME_PUBLIC_HOST", "VME_BEHIND_PROXY",
                   "VME_BACKEND_HOST", "VME_BACKEND_PORT", "VME_URL_PREFIX")) {
    if ($secrets.ContainsKey($key) -and $secrets[$key]) {
        $envPairs += "$key=$($secrets[$key])"
    }
}
& $nssmExe set $serviceName AppEnvironmentExtra $envPairs | Out-Null

# ---- Firewall (standalone mode only) -----------------------------------------
if ($behindProxy) {
    Write-Host "Skipping firewall rules - IIS is the public-facing listener." -ForegroundColor DarkGray
    foreach ($name in @("VME HTTP", "VME HTTPS")) {
        if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
            Write-Host "Removing stale firewall rule '$name'..." -ForegroundColor DarkGray
            Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
        }
    }
} else {
    $httpPort  = if ($secrets["VME_HTTP_PORT"])  { [int]$secrets["VME_HTTP_PORT"] }  else { 80  }
    $httpsPort = if ($secrets["VME_HTTPS_PORT"]) { [int]$secrets["VME_HTTPS_PORT"] } else { 443 }
    foreach ($pair in @(@("VME HTTP", $httpPort), @("VME HTTPS", $httpsPort))) {
        $name = $pair[0]
        $port = $pair[1]
        if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
            Write-Host "Adding firewall rule '$name' (TCP $port)..." -ForegroundColor Cyan
            New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP `
                -LocalPort $port -Action Allow -Profile Any | Out-Null
        }
    }
}

Write-Host "Starting service..." -ForegroundColor Cyan
& $nssmExe start $serviceName

Start-Sleep -Seconds 2
Get-Service $serviceName | Format-Table -AutoSize

if ($behindProxy) {
    $publicHost = $secrets["VME_PUBLIC_HOST"]
    $urlPrefix = $secrets["VME_URL_PREFIX"]
    Write-Host "Service running on http://${backendHost}:$backendPort." -ForegroundColor Green
    if ($publicHost) {
        Write-Host "Open https://$publicHost$urlPrefix/ (IIS terminates TLS and proxies to the service)." -ForegroundColor Green
    }
} else {
    $publicHost = $secrets["VME_PUBLIC_HOST"]
    if ($publicHost) {
        Write-Host "Service installed. Open https://$publicHost." -ForegroundColor Green
    } else {
        Write-Host "Service installed. Open https://localhost." -ForegroundColor Green
    }
}
