#Requires -Version 5.1
<#
.SYNOPSIS
    One-time YouTube OAuth authorization for the VME upload feature.

.DESCRIPTION
    Thin wrapper around `backend/scripts/yt_authorize.py` that takes
    care of the Windows-specific env-var hygiene:
      - Clears PYTHONHOME / PYTHONPATH so the venv interpreter doesn't
        pick up a different Python installation's stdlib.
      - Loads VME_DATA_DIR (and any other vars) from secrets.env so
        the Python side resolves the data dir the same way the
        running service does.

    Pass the path to the client-secret JSON downloaded from Google
    Cloud Console; the Python side copies it into the data dir so
    subsequent runs of /youtube/status, of this script, and of the
    server itself all find it.

.PARAMETER ClientSecret
    Path to the OAuth client-secret JSON. Optional - omit it if the
    file is already at <VME_DATA_DIR>\youtube_client_secret.json
    (e.g. you placed it there manually or already ran this script).

.EXAMPLE
    .\scripts\yt-authorize.ps1 ~\Downloads\client_secret_xxx.json

.EXAMPLE
    .\scripts\yt-authorize.ps1
#>
param(
    [Parameter(Position = 0)]
    [string]$ClientSecret
)

. "$PSScriptRoot\_common.ps1"

# Same env-var hygiene as scripts/dev.ps1. PYTHONHOME redirects the
# stdlib lookup at C-runtime level, which is what causes the "3.14
# typing.py refuses to parse in 3.11" failure mode we hit during install.
# PYTHONPATH is the moral equivalent for pure-Python imports and bites
# in the same way for site-packages.
Refresh-Path
Clear-PythonHome
if (Test-Path Env:PYTHONPATH) {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
}

# Pull VME_DATA_DIR (and friends) from secrets.env so the Python side
# resolves paths exactly the way the Windows service does. Without
# this, running the script from a fresh shell would default to the
# project root's `media/` instead of the configured data dir.
Load-VmeEnv

$root = Get-VmeRoot
$backend = Join-Path $root "backend"
$python = Get-VenvPython

if (-not (Test-Path $python)) {
    Write-Error "Backend venv not found at $python. Run 'uv sync' inside backend/ first."
    exit 1
}

# Build the python argv. If the caller passed a path, hand it through;
# the Python script copies it into the data dir for us.
$pyArgs = @("-m", "scripts.yt_authorize")
if ($PSBoundParameters.ContainsKey("ClientSecret") -and $ClientSecret) {
    # Resolve relative paths against the caller's CWD before the cwd
    # changes to backend/. Use Resolve-Path so `~` expands and a missing
    # file fails fast with a clear error here rather than deep in Python.
    try {
        $resolved = (Resolve-Path -LiteralPath $ClientSecret -ErrorAction Stop).Path
    } catch {
        Write-Error "Client-secret file not found: $ClientSecret"
        exit 1
    }
    $pyArgs += $resolved
}

Write-Host "Running YouTube OAuth flow..." -ForegroundColor Cyan
Write-Host "  python : $python"
Write-Host "  data dir: $($env:VME_DATA_DIR)" -ForegroundColor DarkGray

# `python -m scripts.yt_authorize` resolves `scripts` relative to the
# CWD, so this MUST run from backend/ - from the repo root it would
# find the repo's own scripts/ folder (PowerShell only, no Python
# module) and fail with "No module named scripts.yt_authorize".
Push-Location $backend
try {
    # `& $python` runs the interpreter inline so its exit code propagates
    # and stdout/stderr stream straight to this shell - important since the
    # OAuth flow prints the success message we want to see.
    & $python @pyArgs
    $exit = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($exit -ne 0) {
    Write-Error "yt_authorize exited with code $exit"
    exit $exit
}
