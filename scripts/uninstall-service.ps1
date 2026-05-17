#Requires -Version 5.1
<#
.SYNOPSIS
    Remove the VME Windows service.
#>

. "$PSScriptRoot\_common.ps1"

if (-not (Test-Admin)) {
    Write-Error "This script must run as Administrator."
    exit 1
}

$serviceName = "MatchEditor"
$root = Get-VmeRoot
$nssmExe = Join-Path $root "tools\nssm.exe"

if (-not (Get-Service $serviceName -ErrorAction SilentlyContinue)) {
    Write-Host "Service $serviceName is not installed." -ForegroundColor Yellow
    exit 0
}

if (Test-Path $nssmExe) {
    & $nssmExe stop $serviceName 2>$null | Out-Null
    & $nssmExe remove $serviceName confirm
} else {
    Stop-Service $serviceName -Force -ErrorAction SilentlyContinue
    & sc.exe delete $serviceName | Out-Null
}

foreach ($name in @("VME HTTP", "VME HTTPS")) {
    $rule = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($rule) {
        Write-Host "Removing firewall rule '$name'..." -ForegroundColor Cyan
        Remove-NetFirewallRule -DisplayName $name | Out-Null
    }
}

Write-Host "Service removed." -ForegroundColor Green
