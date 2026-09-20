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
        platforms;android-36
        build-tools;36.0.0
    - Gradle, at whatever version gradle-wrapper.properties names, used
      once to generate the wrapper jar that is gitignored

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
$downloadDir = Join-Path $ToolchainDir 'downloads'

$JDK_URL    = 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse'

# Must be new enough to understand SDK XML v4. The older 11076708 (rev 12.0)
# only understands v3 and warns on every invocation once any v4 metadata is
# in the local repo, which - see Invoke-Native - used to abort this script
# on its second run.
$CMDLINE_BUILD = '13114758'
$CMDLINE_URL = "https://dl.google.com/android/repository/commandlinetools-win-${CMDLINE_BUILD}_latest.zip"


function Write-Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }
function Write-Skip($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }

<#
  Run a native executable and show its output, without PowerShell deciding
  that anything on stderr is a terminating error.

  This is not a style preference. With ErrorActionPreference = 'Stop', a
  native command whose stderr is redirected into the pipeline (`cmd 2>&1 |`)
  has each stderr line turned into an ErrorRecord, and the first one kills
  the script. Both java -version and sdkmanager write perfectly ordinary
  progress and warnings to stderr, so the script died on output that meant
  nothing was wrong.

  Letting cmd.exe merge the two streams before PowerShell sees them keeps
  them as plain strings. Exit code is still checked properly.
#>
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [string]$Indent = '      ',
        [switch]$Quiet
    )
    $quoted = ($Arguments | ForEach-Object { "`"$_`"" }) -join ' '
    & cmd.exe /c "`"$Exe`" $quoted 2>&1" | ForEach-Object {
        # sdkmanager redraws a progress bar with \r; keep the log readable.
        $line = ($_ -replace '\[[=\s]*\]\s*\d*%?', '').Trim()
        if ($line -and -not $Quiet) { Write-Host "$Indent$line" }
    }
    return $LASTEXITCODE
}

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
if ((Invoke-Native -Exe "$jdkDir\bin\java.exe" -Arguments @('-version') -Indent '    ') -ne 0) {
    throw "the JDK at $jdkDir does not run"
}

# ---------------------------------------------------------- Android SDK
Write-Step "Android SDK command-line tools"
# The build number has to be in the cached filename. With a fixed name, a
# bump to $CMDLINE_BUILD re-used the stale zip, re-extracted the old tools
# and then stamped them with the new number - so the stamp claimed an
# upgrade that had not happened, and the warning it was meant to fix
# stayed put.
$cmdZip = Join-Path $downloadDir "cmdline-tools-$CMDLINE_BUILD.zip"
Get-File $CMDLINE_URL $cmdZip

# sdkmanager insists on living at cmdline-tools/<version>/bin, not at the
# archive's own cmdline-tools/bin. Getting this wrong produces a baffling
# "Could not determine SDK root" much later, so lay it out correctly now.
$cmdlineTarget = Join-Path $sdkDir 'cmdline-tools\latest'
$cmdlineStamp = Join-Path $cmdlineTarget '.vme-build'
# Identify the install by the archive it came from, not merely by the build
# number we intended: a stamp that records intent can outlive a failed
# upgrade and then vouch for tools that were never replaced. Size is enough
# to distinguish Google's release archives and costs nothing.
# ${} around the name: "$CMDLINE_BUILD:..." parses the colon as a drive
# qualifier and fails at parse time.
$cmdIdentity = "${CMDLINE_BUILD}:$((Get-Item $cmdZip).Length)"
$haveCurrent = (Test-Path (Join-Path $cmdlineTarget 'bin\sdkmanager.bat')) -and
               (Test-Path $cmdlineStamp) -and
               ((Get-Content $cmdlineStamp -Raw).Trim() -eq $cmdIdentity)
if ($haveCurrent -and -not $Force) {
    Write-Skip "already extracted: cmdline-tools\latest ($CMDLINE_BUILD)"
} else {
    $tmp = Join-Path $ToolchainDir '_cmdline_tmp'
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path $cmdZip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path (Split-Path $cmdlineTarget) | Out-Null
    if (Test-Path $cmdlineTarget) { Remove-Item -Recurse -Force $cmdlineTarget }
    Move-Item (Join-Path $tmp 'cmdline-tools') $cmdlineTarget
    Remove-Item -Recurse -Force $tmp
    Set-Content -Path $cmdlineStamp -Value $cmdIdentity -Encoding ascii
    $rev = (Select-String -Path (Join-Path $cmdlineTarget 'source.properties') `
                          -Pattern '^Pkg.Revision=(.+)$').Matches.Groups[1].Value
    Write-Host "    cmdline-tools rev $rev (build $CMDLINE_BUILD)"
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
# API 36 is not a choice: RootEncoder 2.7.5 is compiled against it and
# refuses to be consumed by a lower compileSdk. See android/build.gradle.kts.
$packages = @('platform-tools', 'platforms;android-36', 'build-tools;36.0.0')
foreach ($pkg in $packages) {
    Write-Host "    installing $pkg ..."
    $rc = Invoke-Native -Exe $sdkmanager -Arguments @("--sdk_root=$sdkDir", $pkg)
    if ($rc -ne 0) { throw "sdkmanager failed for $pkg (exit $rc)" }
}

# --------------------------------------------------------------- Gradle
# Only needed to produce the wrapper jar, which is gitignored: it is a
# binary we did not build, so it is generated per machine rather than
# committed. Afterwards gradlew.bat is self-sufficient and fetches its own
# distribution.
#
# The version comes from gradle-wrapper.properties rather than being pinned
# here, so there is exactly one place to change it.
$wrapperProps = Join-Path $androidDir 'gradle\wrapper\gradle-wrapper.properties'
$gradleVersion = ([regex]::Match(
    (Get-Content $wrapperProps -Raw), 'gradle-([\d.]+)-bin\.zip')).Groups[1].Value
if (-not $gradleVersion) { throw "could not read the Gradle version from $wrapperProps" }

Write-Step "Gradle $gradleVersion (to generate the wrapper)"
$wrapperJar = Join-Path $androidDir 'gradle\wrapper\gradle-wrapper.jar'
if ((Test-Path $wrapperJar) -and -not $Force) {
    Write-Skip "wrapper jar already present"
} else {
    $gradleDir = Join-Path $ToolchainDir "gradle-$gradleVersion"
    $gradleZip = Join-Path $downloadDir "gradle-$gradleVersion-bin.zip"
    Get-File "https://services.gradle.org/distributions/gradle-$gradleVersion-bin.zip" $gradleZip
    Expand-To $gradleZip $gradleDir $true

    # Generate in an empty scratch directory, NOT in android/. `gradle
    # wrapper` configures the project it runs in, and this project cannot be
    # configured by an arbitrary Gradle: AGP 8.13 refuses anything below
    # Gradle 8.13 with an obscure "missing parameter of type
    # InternalProblems" failure. Generating the jar somewhere with no build
    # script sidesteps the chicken-and-egg entirely, and keeps working when
    # AGP raises its floor again.
    $scratch = Join-Path $ToolchainDir '_wrapper_gen'
    if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch }
    New-Item -ItemType Directory -Force -Path $scratch | Out-Null
    # Gradle 8.13 refuses to run any task in a directory that is not a build
    # at all, so give it the emptiest possible one: a settings file naming a
    # project with no plugins and no subprojects.
    Set-Content -Path (Join-Path $scratch 'settings.gradle.kts') `
        -Value 'rootProject.name = "wrapper-gen"' -Encoding ascii
    Push-Location $scratch
    try {
        $rc = Invoke-Native -Exe "$gradleDir\bin\gradle.bat" `
            -Arguments @('wrapper', '--gradle-version', $gradleVersion, '--no-daemon', '-q')
        if ($rc -ne 0) { throw "gradle wrapper generation failed (exit $rc)" }
    } finally { Pop-Location }

    Copy-Item (Join-Path $scratch 'gradle\wrapper\gradle-wrapper.jar') $wrapperJar -Force
    # gradlew / gradlew.bat are committed, but restore them if missing so a
    # partial checkout still works.
    foreach ($script in @('gradlew', 'gradlew.bat')) {
        $dest = Join-Path $androidDir $script
        if (-not (Test-Path $dest)) { Copy-Item (Join-Path $scratch $script) $dest }
    }
    Remove-Item -Recurse -Force $scratch
    Write-Host "    wrapper jar generated"
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
  android\deploy.cmd           (build, install on the phone, launch, tail log)

The phone is best paired over wireless debugging, not USB - the HDMI
  capture adapter takes the only port. deploy.cmd explains how if it
  finds no device.

Nothing was added to your PATH or registry. To remove all of it:
  Remove-Item -Recurse -Force "$ToolchainDir"
"@ -ForegroundColor Green
