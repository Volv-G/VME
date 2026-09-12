#Requires -Version 5.1
<#
.SYNOPSIS
    Build the React frontend and copy the output into backend/static/.
#>

. "$PSScriptRoot\_common.ps1"
Refresh-Path

$root = Get-VmeRoot
$frontend = Join-Path $root "frontend"
$static = Join-Path $root "backend\static"

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
    Push-Location $frontend
    npm install --no-fund --no-audit
    Pop-Location
}

# Backend lint FIRST, and only the checks that catch code which cannot
# possibly run: undefined names, redefinitions, unreachable-by-syntax.
# A NameError in a rarely-exercised branch of an API handler shipped
# once and only surfaced as a 500 in production; `python -c import app`
# doesn't catch it because the branch never executes at import time.
# Style issues are deliberately NOT gated - this must never be the
# reason a deploy is blocked.
$ruff = Join-Path $root "backend\.venv\Scripts\python.exe"
if (Test-Path $ruff) {
    Write-Host "Checking backend for undefined names..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "backend")
    try {
        & $ruff -E -m ruff check --select F821,F811,F502,F522,F524 app scripts
        if ($LASTEXITCODE -ne 0) { throw "backend check failed" }
    }
    finally {
        Pop-Location
    }
}

Write-Host "Building frontend..." -ForegroundColor Cyan
Push-Location $frontend
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
}
finally {
    Pop-Location
}

$dist = Join-Path $frontend "dist"
if (-not (Test-Path $dist)) { Write-Error "Build output not found: $dist"; exit 1 }

Write-Host "Copying dist -> $static ..." -ForegroundColor Cyan
if (Test-Path $static) { Remove-Item $static -Recurse -Force }
New-Item -ItemType Directory -Force -Path $static | Out-Null
Copy-Item -Path (Join-Path $dist "*") -Destination $static -Recurse -Force

Write-Host "Done." -ForegroundColor Green
