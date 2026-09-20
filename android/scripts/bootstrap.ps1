<#
.SYNOPSIS
  Installs a self-contained Android toolchain for this repo. No admin rights.

.DESCRIPTION
  Everything lands under one directory outside the repo (default
  %LOCALAPPDATA%\vme-android), so nothing is installed machine-wide, nothing
  touches the registry, and deleting that one folder undoes all of it.

  Deliberately does NOT use winget or Android Studio: the machine-scope
  installers want elevation, and this box does not have it. The pieces are
  just archives, so we unpack them ourselves.

    - Temurin JDK 17  (Gradle 8.9 + AGP 8.7 require 17)
    - Android SDK command-line tools, then via sdkmanager:
        platform-tools     (adb, for deploying to the phone)
        platforms;android-35
        build-tools;35.0.0
    - Gradle 8.9, used once to generate the wrapper jar that is gitignored

  Re-running is cheap: each step is skipped if its output already exists.
  Pass -Force to redo everything.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\bootstrap.ps1
#>
[CmdletBinding()]
param(
    [string]$ToolchainDir = (Join-Path $env:LOCALAPPDATA 'vme-android'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # a progress bar makes downloads ~10x slower

$repoRoot    = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$androidDir  = Join-Path $repoRoot 'android'
$jdkDir      = Join-Path $ToolchainDir 'jdk-17'
$sdkDir      = Join-Path $ToolchainDir 'sdk'
$gradleDir   = Join-Path $ToolchainDir 'gradle-8.9'
$downloadDir = Join-Path $ToolchainDir 'downloads'

$JDK_URL    = 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse'
$CMDLINE_URL = 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip'
$GRADLE_URL = 'https://services.gradle.org/distributions/gradle-8.9-bin.zip'

function Write-Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }
function Write-Skip($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }

function Get-File($url, $dest) {
    if ((Test-Path $dest) -and -not $Force) {
        Write-Skip "already downloaded: $(Split-Path -Leaf $dest)"
        return
    }
    Write-Host "    downloading $(Split-Path -Leaf $dest) ..."
    # curl.exe beats Invoke-WebRequest here: it streams to disk and follows
    # the Adoptium redirect without fuss.
    & curl.exe -sSL --fail -o $dest $url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $url" }
    $mb = [math]::Round((Get-Item $dest).Length / 1MB, 1)
    Write-Host "    got $mb MB"
}

function Expand-To($zip, $target, $strip) {
    # $strip = name of the single top-level folder inside the archive to
    # flatten away, or $null to extract as-is.
    if ((Test-Path $target) -and -not $Force) {
        Write-Skip "already extracted: $(Split-Path -Leaf $target)"
        return
    }
    if (Test-Path $target) { Remove-Item -Recurse -Force $target }
    $tmp = "$target.tmp"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Write-Host "    extracting $(Split-Path -Leaf $zip) ..."
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    if ($strip) {
        $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
        Move-Item $inner.FullName $target
        Remove-Item -Recurse -Force $tmp
    } else {
        Move-Item $tmp $target
    }
}

New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

# ---------------------------------------------------------------- JDK
Write-Step "JDK 17"
$jdkZip = Join-Path $downloadDir 'temurin17.zip'
Get-File $JDK_URL $jdkZip
Expand-To $jdkZip $jdkDir $true
$env:JAVA_HOME = $jdkDir
$env:PATH = "$jdkDir\bin;$env:PATH"
# java -version prints to stderr, and with ErrorActionPreference=Stop a
# redirected native stderr becomes a terminating error. cmd /c keeps it as
# plain text.
& cmd.exe /c "`"$jdkDir\bin\java.exe`" -version 2>&1" | ForEach-Object { Write-Host "    $_" }

# ---------------------------------------------------------- Android SDK
Write-Step "Android SDK command-line tools"
$cmdZip = Join-Path $downloadDir 'cmdline-tools.zip'
Get-File $CMDLINE_URL $cmdZip

# sdkmanager insists on living at cmdline-tools/<version>/bin, not at the
# archive's own cmdline-tools/bin. Getting this wrong produces a baffling
# "Could not determine SDK root" much later, so lay it out correctly now.
$cmdlineTarget = Join-Path $sdkDir 'cmdline-tools\latest'
if ((Test-Path (Join-Path $cmdlineTarget 'bin\sdkmanager.bat')) -and -not $Force) {
    Write-Skip "already extracted: cmdline-tools\latest"
} else {
    $tmp = Join-Path $ToolchainDir '_cmdline_tmp'
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path $cmdZip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path (Split-Path $cmdlineTarget) | Out-Null
    if (Test-Path $cmdlineTarget) { Remove-Item -Recurse -Force $cmdlineTarget }
    Move-Item (Join-Path $tmp 'cmdline-tools') $cmdlineTarget
    Remove-Item -Recurse -Force $tmp
}

$env:ANDROID_HOME = $sdkDir
$env:ANDROID_SDK_ROOT = $sdkDir

# Accept licenses by writing the hashes directly. `sdkmanager --licenses`
# is an interactive prompt loop that is painful to drive from a script and
# hangs outright with no console; these hash files are exactly what it
# writes when you answer yes, and they are stable across SDK releases.
Write-Step "SDK licenses"
$licenseDir = Join-Path $sdkDir 'licenses'
New-Item -ItemType Directory -Force -Path $licenseDir | Out-Null
@{
    'android-sdk-license'         = @('8933bad161af4178b1185d1a37fbf41ea5269c55',
                                      'd56f5187479451eabf01fb78af6dfcb131a6481e',
                                      '24333f8a63b6825ea9c5514f83c2829b004d1fee')
    'android-sdk-preview-license' = @('84831b9409646a918e30573bab4c9c91346d8abd')
}.GetEnumerator() | ForEach-Object {
    $path = Join-Path $licenseDir $_.Key
    ($_.Value -join "`n") | Set-Content -Path $path -NoNewline -Encoding ascii
    Write-Host "    accepted $($_.Key)"
}

Write-Step "SDK packages"
$sdkmanager = Join-Path $cmdlineTarget 'bin\sdkmanager.bat'
$packages = @('platform-tools', 'platforms;android-35', 'build-tools;35.0.0')
foreach ($pkg in $packages) {
    Write-Host "    installing $pkg ..."
    & cmd.exe /c "`"$sdkmanager`" --sdk_root=`"$sdkDir`" `"$pkg`"" 2>&1 |
        Where-Object { $_ -notmatch '^\[=*\s*\]' -and $_.Trim() } |
        ForEach-Object { Write-Host "      $_" }
    if ($LASTEXITCODE -ne 0) { throw "sdkmanager failed for $pkg" }
}

# --------------------------------------------------------------- Gradle
# Only needed to generate the wrapper jar (gitignored, and we cannot commit
# a binary blob we did not build). After this, gradlew.bat is self-sufficient
# and will fetch its own distribution if ever deleted.
Write-Step "Gradle 8.9 (to generate the wrapper)"
$wrapperJar = Join-Path $androidDir 'gradle\wrapper\gradle-wrapper.jar'
if ((Test-Path $wrapperJar) -and -not $Force) {
    Write-Skip "wrapper jar already present"
} else {
    $gradleZip = Join-Path $downloadDir 'gradle-8.9-bin.zip'
    Get-File $GRADLE_URL $gradleZip
    Expand-To $gradleZip $gradleDir $true
    Push-Location $androidDir
    try {
        & "$gradleDir\bin\gradle.bat" wrapper --gradle-version 8.9 --no-daemon -q
        if ($LASTEXITCODE -ne 0) { throw "gradle wrapper generation failed" }
    } finally { Pop-Location }
    Write-Host "    wrapper generated"
}

# ------------------------------------------------------ local.properties
# AGP finds the SDK through this file. It is gitignored because the path is
# per-machine.
Write-Step "local.properties"
$localProps = Join-Path $androidDir 'local.properties'
"sdk.dir=$($sdkDir -replace '\\', '\\')" | Set-Content -Path $localProps -Encoding ascii
Write-Host "    sdk.dir -> $sdkDir"

Write-Step "Done"
Write-Host @"
Toolchain installed under:
  $ToolchainDir

Build with:
  android\build.cmd            (debug APK)
  android\build.cmd install    (debug APK + push to a connected phone)

Nothing was added to your PATH or registry. To remove all of it:
  Remove-Item -Recurse -Force "$ToolchainDir"
"@ -ForegroundColor Green
