#Requires -Version 5.1
<#
.SYNOPSIS
    Generate a self-signed certificate for HTTPS on localhost, the machine name,
    every non-loopback LAN IPv4 address, and any extra DNS names you pass in.

.DESCRIPTION
    Uses New-SelfSignedCertificate (Windows 10+/Server 2016+).
    Exports cert.pem and key.pem to <project>/certs/.

    If you already have a real cert (Let's Encrypt etc.), DON'T run this; instead
    use scripts/set-cert.ps1 to point the server at it.

.PARAMETER Domains
    Extra DNS names to add as Subject Alternative Names. Useful if you also want
    the cert to cover a public hostname.

.EXAMPLE
    scripts/generate-cert.ps1
    scripts/generate-cert.ps1 -Domains satis2.duckdns.org,vme.local
#>
param(
    [string[]]$Domains = @()
)

. "$PSScriptRoot\_common.ps1"

$root = Get-VmeRoot
$certDir = Join-Path $root "certs"
New-Item -ItemType Directory -Force -Path $certDir | Out-Null

$certFile = Join-Path $certDir "cert.pem"
$keyFile = Join-Path $certDir "key.pem"
$pfxFile = Join-Path $certDir "vme.pfx"

$hostname = $env:COMPUTERNAME

# Collect every non-loopback IPv4 address on the machine. We use these as both
# DNS and IP SANs so browsers can match either form.
$lanIps = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.IPAddress -ne "127.0.0.1" -and
        $_.PrefixOrigin -ne "WellKnown"
    } | Select-Object -ExpandProperty IPAddress -Unique)

$names = @("localhost", "127.0.0.1", $hostname) + $lanIps + $Domains | Where-Object { $_ } | Sort-Object -Unique

Write-Host "Generating self-signed cert for: $($names -join ', ')" -ForegroundColor Cyan

$cert = New-SelfSignedCertificate `
    -DnsName $names `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -FriendlyName "VME Match Editor" `
    -NotAfter (Get-Date).AddYears(5) `
    -KeyExportPolicy Exportable `
    -KeyAlgorithm RSA `
    -KeyLength 2048 `
    -Provider "Microsoft Enhanced RSA and AES Cryptographic Provider"

$pwd = ConvertTo-SecureString -String "vme-temp-pwd" -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $pfxFile -Password $pwd | Out-Null

Add-Type -AssemblyName System.Security
$collection = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2Collection
$collection.Import($pfxFile, "vme-temp-pwd", "Exportable,PersistKeySet")
$loaded = $collection[0]

$certPem = "-----BEGIN CERTIFICATE-----`n"
$certPem += [Convert]::ToBase64String($loaded.RawData, "InsertLineBreaks") + "`n"
$certPem += "-----END CERTIFICATE-----`n"
[IO.File]::WriteAllText($certFile, $certPem)

$rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($loaded)
$keyBytes = $rsa.ExportPkcs8PrivateKey()
$keyPem = "-----BEGIN PRIVATE KEY-----`n"
$keyPem += [Convert]::ToBase64String($keyBytes, "InsertLineBreaks") + "`n"
$keyPem += "-----END PRIVATE KEY-----`n"
[IO.File]::WriteAllText($keyFile, $keyPem)

Remove-Item ("Cert:\CurrentUser\My\$($cert.Thumbprint)") -ErrorAction SilentlyContinue

Write-Host "Wrote $certFile and $keyFile" -ForegroundColor Green
Write-Host "Note: a self-signed cert will trigger a browser warning. For a trusted cert" -ForegroundColor Yellow
Write-Host "      (Let's Encrypt etc.), use scripts/set-cert.ps1 instead." -ForegroundColor Yellow
