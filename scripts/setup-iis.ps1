#Requires -Version 5.1
<#
.SYNOPSIS
    Add VME as an IIS Application under an existing site (default: Default Web Site)
    at virtual path /vme. IIS reverse-proxies it to the local FastAPI service.

.DESCRIPTION
    Idempotent setup that:
      * Installs URL Rewrite 2.1 + ARR 3.0 if missing
      * Enables ARR proxy globally and disables response buffering
        (required for the render-progress SSE stream)
      * Creates a dedicated app pool 'MatchEditor'
      * Adds (or updates) an IIS Application at <ParentSite>/<UrlPrefix>
        physically rooted at <repo>/iis (which holds web.config)
      * Tears down the standalone 'MatchEditor' site if it was created by
        an older version of this script
      * Adds an HTTPS binding to the parent site for the chosen public host
        (using SNI so other certs on the same site are not disturbed) and
        binds the supplied cert to it
      * Persists VME_BEHIND_PROXY=1 etc. into secrets.env

    Must run as Administrator.

.PARAMETER PublicHost
    Public hostname clients hit (e.g. satis2.duckdns.org). Pulled from
    secrets.env (VME_PUBLIC_HOST) if omitted.

.PARAMETER Thumbprint
    SHA-1 thumbprint of the cert in Cert:\LocalMachine\My to bind for the
    public host. If omitted, the script picks the cert whose subject or
    DNS names contain PublicHost (errors out if zero or multiple match).

.PARAMETER ParentSite
    IIS site to add the application under. Default 'Default Web Site'.

.PARAMETER UrlPrefix
    Application virtual path. Default '/vme'.

.PARAMETER BackendPort
    Local TCP port the FastAPI service listens on. Default 8000.
#>
param(
    [string]$PublicHost,
    [string]$Thumbprint,
    [string]$ParentSite = "Default Web Site",
    [string]$UrlPrefix = "/vme",
    [int]$BackendPort = 8000
)

. "$PSScriptRoot\_common.ps1"
Refresh-Path

if (-not (Test-Admin)) {
    Write-Error "This script must run as Administrator."
    exit 1
}

if (-not $UrlPrefix.StartsWith("/")) { $UrlPrefix = "/$UrlPrefix" }
$UrlPrefix = $UrlPrefix.TrimEnd("/")
if (-not $UrlPrefix) { Write-Error "UrlPrefix cannot be '/'."; exit 1 }

$root = Get-VmeRoot
$srcIisDir = Join-Path $root "iis"
if (-not (Test-Path (Join-Path $srcIisDir "web.config"))) {
    Write-Error "iis/web.config not found at $srcIisDir."
    exit 1
}

# IIS cannot read content from a user profile path (OneDrive / Documents /
# anything under C:\Users\<you>) because the worker process runs as
# 'IIS APPPOOL\MatchEditor' and has no traverse rights on your profile.
# Copy the IIS-facing files to ProgramData where everyone-read is the default.
$deployedIisDir = Join-Path $env:ProgramData "VME\iis"
New-Item -ItemType Directory -Force -Path $deployedIisDir | Out-Null

# ---- Resolve PublicHost ------------------------------------------------------
if (-not $PublicHost) {
    $secrets = Read-SecretsFile
    if ($secrets["VME_PUBLIC_HOST"]) { $PublicHost = $secrets["VME_PUBLIC_HOST"] }
}
if (-not $PublicHost) {
    $PublicHost = Read-Host "Public hostname (e.g. satis2.duckdns.org)"
}
if (-not $PublicHost) { Write-Error "PublicHost is required."; exit 1 }

# ---- Verify IIS --------------------------------------------------------------
$w3svc = Get-Service W3SVC -ErrorAction SilentlyContinue
if (-not $w3svc) {
    Write-Error "IIS is not installed. Enable 'Internet Information Services' in Windows Features and rerun."
    exit 1
}
if ($w3svc.Status -ne "Running") {
    Write-Host "Starting W3SVC..." -ForegroundColor Cyan
    Start-Service W3SVC
}

# ---- Install URL Rewrite + ARR if missing ------------------------------------
function Test-MsiInstalled([string]$displayNameLike) {
    $paths = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($p in $paths) {
        if (-not (Test-Path $p)) { continue }
        $hits = Get-ChildItem $p | ForEach-Object {
            try { Get-ItemProperty $_.PSPath -ErrorAction Stop } catch { $null }
        } | Where-Object { $_ -and $_.DisplayName -and $_.DisplayName -like $displayNameLike }
        if ($hits) { return $true }
    }
    return $false
}

function Install-MsiSilently([string]$url, [string]$friendlyName) {
    $tmp = Join-Path $env:TEMP "vme-$([Guid]::NewGuid()).msi"
    Write-Host "Downloading $friendlyName..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Write-Host "Installing $friendlyName..." -ForegroundColor Cyan
    $p = Start-Process msiexec.exe -ArgumentList "/i", "`"$tmp`"", "/qn", "/norestart" -Wait -PassThru
    Remove-Item $tmp -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) {
        throw "$friendlyName install failed with exit code $($p.ExitCode)."
    }
}

if (-not (Test-MsiInstalled "*URL Rewrite*")) {
    Install-MsiSilently `
        "https://download.microsoft.com/download/1/2/8/128E2E22-C1B9-44A4-BE2A-5859ED1D4592/rewrite_amd64_en-US.msi" `
        "URL Rewrite 2.1"
} else {
    Write-Host "URL Rewrite already installed." -ForegroundColor DarkGray
}

if (-not (Test-MsiInstalled "*Application Request Routing*")) {
    Install-MsiSilently `
        "https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/requestRouter_amd64.msi" `
        "Application Request Routing 3.0"
} else {
    Write-Host "Application Request Routing already installed." -ForegroundColor DarkGray
}

Import-Module WebAdministration -ErrorAction Stop

# ---- Resolve cert thumbprint -------------------------------------------------
function Find-Cert([string]$publicHost) {
    Get-ChildItem Cert:\LocalMachine\My | Where-Object {
        $_.HasPrivateKey -and (
            $_.Subject -match [regex]::Escape($publicHost) -or
            ($_.DnsNameList -and ($_.DnsNameList.Unicode -contains $publicHost))
        )
    }
}

if (-not $Thumbprint) {
    $now = Get-Date
    $allMatches = @(Find-Cert $PublicHost)
    $valid = @($allMatches | Where-Object { $_.NotBefore -le $now -and $_.NotAfter -gt $now })
    $expired = @($allMatches | Where-Object { $_.NotAfter -le $now })

    if ($expired.Count -gt 0) {
        Write-Host "Skipping expired cert(s):" -ForegroundColor DarkGray
        $expired | ForEach-Object {
            Write-Host "  $($_.Thumbprint)  $($_.Subject)  expired $($_.NotAfter.ToString('yyyy-MM-dd'))" -ForegroundColor DarkGray
        }
    }

    if ($valid.Count -eq 0) {
        Write-Error "No valid (non-expired) cert in LocalMachine\My matches '$PublicHost'. Pass -Thumbprint <SHA1>."
        exit 1
    }

    # Pick the cert with the latest NotAfter (longest remaining validity).
    $picked = $valid | Sort-Object NotAfter -Descending | Select-Object -First 1
    $Thumbprint = $picked.Thumbprint

    if ($valid.Count -gt 1) {
        Write-Host "Multiple valid certs match - picking longest-validity:" -ForegroundColor Cyan
        $valid | Sort-Object NotAfter -Descending | ForEach-Object {
            $marker = if ($_.Thumbprint -eq $Thumbprint) { "->" } else { "  " }
            Write-Host "  $marker $($_.Thumbprint)  expires $($_.NotAfter.ToString('yyyy-MM-dd'))"
        }
    } else {
        Write-Host "Using cert: $($picked.Subject) ($Thumbprint)" -ForegroundColor DarkGray
    }
}

$cert = Get-Item "Cert:\LocalMachine\My\$Thumbprint" -ErrorAction SilentlyContinue
if (-not $cert) {
    Write-Error "Cert with thumbprint $Thumbprint not found in LocalMachine\My."
    exit 1
}
Write-Host "Cert subject:    $($cert.Subject)" -ForegroundColor DarkGray
Write-Host "Cert expires:    $($cert.NotAfter)" -ForegroundColor DarkGray

# ---- ARR proxy: enable + disable response buffering --------------------------
Write-Host "Enabling ARR proxy and disabling response buffering..." -ForegroundColor Cyan
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
    -Filter "system.webServer/proxy" -Name "enabled" -Value "True"
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
    -Filter "system.webServer/proxy" -Name "preserveHostHeader" -Value "True"
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
    -Filter "system.webServer/proxy" -Name "reverseRewriteHostInResponseHeaders" -Value "True"
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
    -Filter "system.webServer/proxy" -Name "responseBufferLimit" -Value 0

# Upload read-ahead. IIS modules (auth, request filtering) want to
# inspect the first N bytes of every request body before handing it
# off to ARR. Default is 49,152 bytes (48 KB), which can produce
# mysterious mid-upload stalls on multi-hundred-MB video uploads when
# the module pipeline interacts with chunked transfer encoding or
# auth challenges - the browser's progress bar freezes at some non-100%
# percentage and no POST shows up in the backend's access log. Bumping
# this to 32 MiB covers the entire multipart header section of any
# realistic clip upload, after which ARR streams the rest.
#
# Set at applicationHost level rather than web.config because the
# `system.webServer/serverRuntime` section is locked by default and a
# web.config override yields HTTP 500.19 "This configuration section
# cannot be used at this path" (error 0x80070021).
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' `
    -Filter "system.webServer/serverRuntime" -Name "uploadReadAheadSize" -Value 33554432

# ---- Tear down legacy standalone 'MatchEditor' site --------------------------
$legacy = Get-Website -Name "MatchEditor" -ErrorAction SilentlyContinue
if ($legacy) {
    Write-Host "Removing legacy standalone 'MatchEditor' site..." -ForegroundColor Cyan
    Remove-Website -Name "MatchEditor" -ErrorAction SilentlyContinue
}

# ---- Verify parent site exists -----------------------------------------------
$site = Get-Website -Name $ParentSite -ErrorAction SilentlyContinue
if (-not $site) {
    Write-Error "Parent site '$ParentSite' does not exist. Either create it in IIS Manager or pass a different -ParentSite."
    exit 1
}
if ($site.State -ne "Started") {
    Write-Host "Starting parent site '$ParentSite'..." -ForegroundColor Cyan
    Start-Website -Name $ParentSite
}

# ---- Application pool --------------------------------------------------------
$poolName = "MatchEditor"
if (-not (Test-Path "IIS:\AppPools\$poolName")) {
    Write-Host "Creating app pool '$poolName'..." -ForegroundColor Cyan
    New-WebAppPool -Name $poolName | Out-Null
}
Set-ItemProperty "IIS:\AppPools\$poolName" -Name managedRuntimeVersion -Value ""
Set-ItemProperty "IIS:\AppPools\$poolName" -Name managedPipelineMode -Value Integrated
Set-ItemProperty "IIS:\AppPools\$poolName" -Name autoStart -Value $true
# Long idle timeout: the proxy itself does very little, but recycling the pool
# would close in-flight SSE streams to clients.
Set-ItemProperty "IIS:\AppPools\$poolName" -Name processModel.idleTimeout -Value ([TimeSpan]::FromHours(8))

# ---- Deploy web.config to a path IIS can actually read -----------------------
Write-Host "Deploying web.config to $deployedIisDir..." -ForegroundColor Cyan
Copy-Item -Path (Join-Path $srcIisDir "web.config") `
          -Destination (Join-Path $deployedIisDir "web.config") -Force

# Explicit ACL: grant the app pool's virtual identity AND the IIS_IUSRS group
# read+execute on the deployed dir. ProgramData's defaults already include
# Users:(RX) which covers IIS_IUSRS, but we be explicit for clarity and to
# survive future Set-Acl resets.
$icaclsArgs = @(
    "$deployedIisDir",
    "/grant", "IIS_IUSRS:(OI)(CI)RX",
    "/grant", "IIS APPPOOL\${poolName}:(OI)(CI)RX",
    "/T", "/C", "/Q"
)
$null = & icacls @icaclsArgs

# ---- Application at <ParentSite>/<UrlPrefix> ---------------------------------
$appPath = "IIS:\Sites\$ParentSite$UrlPrefix"
$existingApp = Get-WebApplication -Site $ParentSite -Name $UrlPrefix.TrimStart("/") -ErrorAction SilentlyContinue
if ($existingApp) {
    Write-Host "Updating existing application $ParentSite$UrlPrefix..." -ForegroundColor DarkGray
    Set-ItemProperty $appPath -Name physicalPath -Value $deployedIisDir
    Set-ItemProperty $appPath -Name applicationPool -Value $poolName
} else {
    Write-Host "Creating application $ParentSite$UrlPrefix..." -ForegroundColor Cyan
    New-WebApplication -Site $ParentSite -Name $UrlPrefix.TrimStart("/") `
        -PhysicalPath $deployedIisDir -ApplicationPool $poolName -Force | Out-Null
}

# ---- HTTPS binding for the public host on the parent site --------------------
$existingHttps = Get-WebBinding -Name $ParentSite -Protocol "https" -ErrorAction SilentlyContinue |
    Where-Object {
        $info = $_.bindingInformation
        ($info -split ":")[2] -eq $PublicHost -and ($info -split ":")[1] -eq "443"
    }
if (-not $existingHttps) {
    Write-Host "Adding HTTPS binding for $PublicHost on '$ParentSite'..." -ForegroundColor Cyan
    New-WebBinding -Name $ParentSite -Protocol "https" -Port 443 `
        -IPAddress "*" -HostHeader $PublicHost -SslFlags 1 | Out-Null  # 1 = SNI required
    $existingHttps = Get-WebBinding -Name $ParentSite -Protocol "https" |
        Where-Object {
            ($_.bindingInformation -split ":")[2] -eq $PublicHost -and
            ($_.bindingInformation -split ":")[1] -eq "443"
        }
} else {
    Write-Host "HTTPS binding for $PublicHost already exists on '$ParentSite'." -ForegroundColor DarkGray
}

# Bind the cert via netsh (works for SNI bindings; AddSslCertificate-style
# methods on $existingHttps are awkward for SNI host:port pairs).
Write-Host "Binding cert $Thumbprint to $PublicHost`:443..." -ForegroundColor Cyan
$appId = "{" + [Guid]::NewGuid().ToString() + "}"
& netsh http delete sslcert "hostnameport=$PublicHost`:443" 2>$null | Out-Null
$nshArgs = @(
    "http", "add", "sslcert",
    "hostnameport=$PublicHost`:443",
    "certhash=$Thumbprint",
    "certstorename=MY",
    "appid=$appId"
)
$result = & netsh @nshArgs
if ($LASTEXITCODE -ne 0) {
    Write-Warning "netsh add sslcert returned $LASTEXITCODE`: $result"
}

# ---- Make sure parent site has a plain HTTP binding for the redirect ---------
$existingHttp = Get-WebBinding -Name $ParentSite -Protocol "http" -ErrorAction SilentlyContinue |
    Where-Object { ($_.bindingInformation -split ":")[1] -eq "80" }
if (-not $existingHttp) {
    Write-Host "Adding HTTP binding (port 80) on '$ParentSite' for the redirect..." -ForegroundColor Cyan
    New-WebBinding -Name $ParentSite -Protocol "http" -Port 80 -IPAddress "*" -HostHeader "" | Out-Null
}

# ---- Restart the parent site so cert/binding changes take effect -------------
Restart-WebItem "IIS:\Sites\$ParentSite"

# ---- Persist proxy mode in secrets.env ---------------------------------------
Write-SecretsFile @{
    VME_BEHIND_PROXY = "1"
    VME_BACKEND_HOST = "127.0.0.1"
    VME_BACKEND_PORT = "$BackendPort"
    VME_PUBLIC_HOST  = $PublicHost
    VME_URL_PREFIX   = $UrlPrefix
}

Write-Host ""
Write-Host "IIS application configured." -ForegroundColor Green
Write-Host "  Parent site:   $ParentSite"
Write-Host "  Virtual path:  $UrlPrefix"
Write-Host "  Physical path: $deployedIisDir  (source of truth: $srcIisDir)"
Write-Host "  HTTPS binding: $PublicHost`:443 (cert $Thumbprint)"
Write-Host "  Proxies to:    http://127.0.0.1:$BackendPort"
Write-Host ""
Write-Host "Next: scripts/install-service.ps1 to install the backend service." -ForegroundColor Yellow
Write-Host "After that, open https://$PublicHost$UrlPrefix/" -ForegroundColor Yellow
