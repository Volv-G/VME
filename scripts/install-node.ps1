#Requires -Version 5.1
<#
.SYNOPSIS
    Installs Node.js LTS via winget if not already present.
.DESCRIPTION
    Checks for `node` on PATH; if missing, installs OpenJS.NodeJS.LTS via winget
    and refreshes the current session's PATH so subsequent commands can find it.
#>

$ErrorActionPreference = "Stop"

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

if (Test-Command "node") {
    $version = (& node --version)
    Write-Host "Node.js already installed: $version" -ForegroundColor Green
    return
}

if (-not (Test-Command "winget")) {
    Write-Error "winget is not available. Please install Node.js LTS manually from https://nodejs.org/"
    exit 1
}

Write-Host "Installing Node.js LTS via winget..." -ForegroundColor Cyan
winget install --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements

Update-SessionPath

if (-not (Test-Command "node")) {
    Write-Warning "Node.js was installed but is not on PATH for this session."
    Write-Warning "Open a new PowerShell window and continue."
    exit 1
}

$nodeVersion = (& node --version)
$npmVersion = (& npm --version)
Write-Host "Node.js installed: $nodeVersion" -ForegroundColor Green
Write-Host "npm installed:     $npmVersion" -ForegroundColor Green
