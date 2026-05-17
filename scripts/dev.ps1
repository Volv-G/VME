#Requires -Version 5.1
<#
.SYNOPSIS
    Run backend (uvicorn) and frontend (Vite) dev servers concurrently.

.DESCRIPTION
    Backend listens on :8001, frontend on :5173 with API proxying to backend.
    Port 8001 is used so the dev backend doesn't collide with the production
    Windows service (which listens on 127.0.0.1:8000).
    Press Ctrl+C to stop both.
#>

. "$PSScriptRoot\_common.ps1"

Refresh-Path
Clear-PythonHome

$root = Get-VmeRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Get-VenvPython

if (-not (Test-Path $python)) {
    Write-Error "Backend venv not found at $python. Run 'uv sync' inside backend/ first."
    exit 1
}

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
    Push-Location $frontend
    npm install --no-fund --no-audit
    Pop-Location
}

Write-Host "Starting backend on http://localhost:8001 ..." -ForegroundColor Cyan
$backendProc = Start-Process -PassThru -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload") `
    -WorkingDirectory $backend `
    -NoNewWindow

Start-Sleep -Seconds 1

Write-Host "Starting frontend on http://localhost:5173 ..." -ForegroundColor Cyan
$frontendProc = Start-Process -PassThru -FilePath "npm.cmd" `
    -ArgumentList @("run", "dev") `
    -WorkingDirectory $frontend `
    -NoNewWindow

Write-Host ""
Write-Host "Both servers running. Open http://localhost:5173" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
Write-Host ""

try {
    while ($true) {
        if ($backendProc.HasExited) {
            Write-Warning "Backend exited."
            break
        }
        if ($frontendProc.HasExited) {
            Write-Warning "Frontend exited."
            break
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    Write-Host "Stopping..." -ForegroundColor Yellow
    if (-not $backendProc.HasExited) { Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue }
    if (-not $frontendProc.HasExited) { Stop-Process -Id $frontendProc.Id -Force -ErrorAction SilentlyContinue }
}
