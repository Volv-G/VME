#Requires -Version 5.1
<#
.SYNOPSIS
    Configure the server to use an existing TLS cert (e.g. Let's Encrypt).

.DESCRIPTION
    Saves the cert/key paths and the public hostname into <root>/secrets.env.
    Sources, in order of preference:
      - A cert already in Cert:\LocalMachine\My (by -Thumbprint, or auto-found
        by -PublicHost) - exported to PEM under <root>/certs/.
      - A PFX file (auto-converted to cert.pem + key.pem in <root>/certs/).
      - A pair of PEM files (cert + key) - used in place.

    Also updates the installed Windows service environment if present.

.PARAMETER PublicHost
    The public hostname users will hit (e.g. satis2.duckdns.org). Stored as
    VME_PUBLIC_HOST and used by the HTTP redirect to send clients to HTTPS on
    the right name.

.PARAMETER Thumbprint
    SHA-1 thumbprint of a cert in Cert:\LocalMachine\My to use. The matching
    private key MUST be exportable.

.PARAMETER FromStore
    Auto-find a cert in Cert:\LocalMachine\My whose subject or DNS names match
    -PublicHost. Errors out if zero or multiple match - use -Thumbprint then.

.PARAMETER CertFile
    Path to a PEM-encoded certificate (full chain).

.PARAMETER KeyFile
    Path to a PEM-encoded private key matching CertFile.

.PARAMETER PfxFile
    A PFX/P12 bundle (cert + key + chain). Will be converted to PEM in
    <root>/certs/.

.PARAMETER PfxPassword
    Password for the PFX file (prompts if omitted).

.EXAMPLE
    scripts/set-cert.ps1 -PublicHost satis2.duckdns.org -FromStore

.EXAMPLE
    scripts/set-cert.ps1 -PublicHost satis2.duckdns.org -Thumbprint A1B2C3...

.EXAMPLE
    scripts/set-cert.ps1 -PublicHost satis2.duckdns.org `
        -CertFile C:\certs\fullchain.pem -KeyFile C:\certs\privkey.pem

.EXAMPLE
    scripts/set-cert.ps1 -PublicHost satis2.duckdns.org `
        -PfxFile C:\win-acme\satis2.duckdns.org.pfx
#>
param(
    [Parameter(Mandatory=$true)][string]$PublicHost,
    [string]$Thumbprint,
    [switch]$FromStore,
    [string]$CertFile,
    [string]$KeyFile,
    [string]$PfxFile,
    [System.Security.SecureString]$PfxPassword
)

. "$PSScriptRoot\_common.ps1"

$root = Get-VmeRoot
$certDir = Join-Path $root "certs"
New-Item -ItemType Directory -Force -Path $certDir | Out-Null

$destCert = Join-Path $certDir "cert.pem"
$destKey = Join-Path $certDir "key.pem"

function Convert-PfxToPem([string]$pfx, [System.Security.SecureString]$password, [string]$outCert, [string]$outKey) {
    Add-Type -AssemblyName System.Security
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        $coll = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2Collection
        $coll.Import($pfx, $plain, "Exportable,PersistKeySet")
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    if ($coll.Count -lt 1) { throw "PFX did not yield any certificates." }

    # Pick the cert that has a private key as the leaf.
    $leaf = $coll | Where-Object { $_.HasPrivateKey } | Select-Object -First 1
    if (-not $leaf) { throw "PFX contains no cert with a private key." }

    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($leaf)
    if (-not $rsa) { throw "Leaf cert has no RSA private key." }

    $sb = New-Object System.Text.StringBuilder
    foreach ($c in $coll) {
        [void]$sb.AppendLine("-----BEGIN CERTIFICATE-----")
        [void]$sb.AppendLine([Convert]::ToBase64String($c.RawData, "InsertLineBreaks"))
        [void]$sb.AppendLine("-----END CERTIFICATE-----")
    }
    [IO.File]::WriteAllText($outCert, $sb.ToString())

    $keyBytes = $rsa.ExportPkcs8PrivateKey()
    $keyPem = "-----BEGIN PRIVATE KEY-----`n"
    $keyPem += [Convert]::ToBase64String($keyBytes, "InsertLineBreaks") + "`n"
    $keyPem += "-----END PRIVATE KEY-----`n"
    [IO.File]::WriteAllText($outKey, $keyPem)
}

function Find-StoreCert([string]$publicHost) {
    Get-ChildItem Cert:\LocalMachine\My | Where-Object {
        $_.HasPrivateKey -and (
            $_.Subject -match [regex]::Escape($publicHost) -or
            ($_.DnsNameList -and ($_.DnsNameList.Unicode -contains $publicHost))
        )
    }
}

function Export-StoreCertToPem(
    [System.Security.Cryptography.X509Certificates.X509Certificate2]$cert,
    [string]$outCert, [string]$outKey
) {
    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
    if (-not $rsa) { throw "Cert $($cert.Thumbprint) has no RSA private key." }
    try {
        $keyBytes = $rsa.ExportPkcs8PrivateKey()
    } catch [System.Security.Cryptography.CryptographicException] {
        throw "Private key for $($cert.Thumbprint) is marked non-exportable. Re-import the cert with -KeyExportPolicy Exportable, or use -PfxFile / -CertFile."
    }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("-----BEGIN CERTIFICATE-----")
    [void]$sb.AppendLine([Convert]::ToBase64String($cert.RawData, "InsertLineBreaks"))
    [void]$sb.AppendLine("-----END CERTIFICATE-----")
    # Append the chain (intermediates) so Hypercorn serves a complete bundle.
    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    $chain.ChainPolicy.RevocationMode = "NoCheck"
    [void]$chain.Build($cert)
    foreach ($el in $chain.ChainElements) {
        if ($el.Certificate.Thumbprint -eq $cert.Thumbprint) { continue }
        [void]$sb.AppendLine("-----BEGIN CERTIFICATE-----")
        [void]$sb.AppendLine([Convert]::ToBase64String($el.Certificate.RawData, "InsertLineBreaks"))
        [void]$sb.AppendLine("-----END CERTIFICATE-----")
    }
    [IO.File]::WriteAllText($outCert, $sb.ToString())

    $keyPem = "-----BEGIN PRIVATE KEY-----`n"
    $keyPem += [Convert]::ToBase64String($keyBytes, "InsertLineBreaks") + "`n"
    $keyPem += "-----END PRIVATE KEY-----`n"
    [IO.File]::WriteAllText($outKey, $keyPem)
}

if ($Thumbprint -or $FromStore) {
    if (-not $Thumbprint) {
        $now = Get-Date
        $allMatches = @(Find-StoreCert $PublicHost)
        $valid = @($allMatches | Where-Object { $_.NotBefore -le $now -and $_.NotAfter -gt $now })
        $expired = @($allMatches | Where-Object { $_.NotAfter -le $now })

        if ($expired.Count -gt 0) {
            Write-Host "Skipping expired cert(s):" -ForegroundColor DarkGray
            $expired | ForEach-Object {
                Write-Host "  $($_.Thumbprint)  $($_.Subject)  expired $($_.NotAfter.ToString('yyyy-MM-dd'))" -ForegroundColor DarkGray
            }
        }

        if ($valid.Count -eq 0) {
            throw "No valid (non-expired) cert in LocalMachine\My matches '$PublicHost'. Pass -Thumbprint <SHA1>."
        }

        $picked = $valid | Sort-Object NotAfter -Descending | Select-Object -First 1
        $Thumbprint = $picked.Thumbprint

        if ($valid.Count -gt 1) {
            Write-Host "Multiple valid certs match - picking longest-validity:" -ForegroundColor Cyan
            $valid | Sort-Object NotAfter -Descending | ForEach-Object {
                $marker = if ($_.Thumbprint -eq $Thumbprint) { "->" } else { "  " }
                Write-Host "  $marker $($_.Thumbprint)  expires $($_.NotAfter.ToString('yyyy-MM-dd'))"
            }
        }
    }
    $cert = Get-Item "Cert:\LocalMachine\My\$Thumbprint" -ErrorAction SilentlyContinue
    if (-not $cert) { throw "Cert with thumbprint $Thumbprint not found in LocalMachine\My." }
    Write-Host "Exporting cert $Thumbprint ($($cert.Subject)) to $destCert / $destKey" -ForegroundColor Cyan
    Export-StoreCertToPem -cert $cert -outCert $destCert -outKey $destKey
    $finalCert = $destCert
    $finalKey = $destKey
}
elseif ($PfxFile) {
    if (-not (Test-Path $PfxFile)) { throw "PFX not found: $PfxFile" }
    if (-not $PfxPassword) {
        $PfxPassword = Read-Host -AsSecureString "PFX password"
    }
    Write-Host "Converting $PfxFile -> $destCert / $destKey" -ForegroundColor Cyan
    Convert-PfxToPem -pfx $PfxFile -password $PfxPassword -outCert $destCert -outKey $destKey
    $finalCert = $destCert
    $finalKey = $destKey
}
elseif ($CertFile -and $KeyFile) {
    if (-not (Test-Path $CertFile)) { throw "Cert not found: $CertFile" }
    if (-not (Test-Path $KeyFile))  { throw "Key not found: $KeyFile" }
    $finalCert = (Resolve-Path $CertFile).Path
    $finalKey  = (Resolve-Path $KeyFile).Path
}
else {
    throw "Specify one of: -Thumbprint, -FromStore, -PfxFile, or both -CertFile + -KeyFile."
}

Write-SecretsFile @{
    VME_CERT_FILE   = $finalCert
    VME_KEY_FILE    = $finalKey
    VME_PUBLIC_HOST = $PublicHost
}

Write-Host "Saved to $(Get-SecretsFile):" -ForegroundColor Green
Write-Host "  VME_CERT_FILE   = $finalCert"
Write-Host "  VME_KEY_FILE    = $finalKey"
Write-Host "  VME_PUBLIC_HOST = $PublicHost"

# Restart service if installed so it picks up the new cert paths.
$serviceName = "MatchEditor"
$nssmExe = Join-Path $root "tools\nssm.exe"
$svc = Get-Service $serviceName -ErrorAction SilentlyContinue
if ($svc -and (Test-Path $nssmExe)) {
    $secrets = Read-SecretsFile
    $envPairs = @()
    foreach ($key in @("VME_USERNAME", "VME_PASSWORD_HASH", "VME_AUTH_REQUIRED",
                       "VME_CERT_FILE", "VME_KEY_FILE", "VME_HTTP_PORT", "VME_HTTPS_PORT",
                       "VME_BIND_HOST", "VME_PUBLIC_HOST")) {
        if ($secrets.ContainsKey($key) -and $secrets[$key]) { $envPairs += "$key=$($secrets[$key])" }
    }
    if ($envPairs.Count -gt 0) {
        & $nssmExe set $serviceName AppEnvironmentExtra $envPairs | Out-Null
    }
    Write-Host "Restarting $serviceName..." -ForegroundColor Cyan
    & $nssmExe restart $serviceName | Out-Null
}
