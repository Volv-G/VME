@echo off
setlocal EnableDelayedExpansion
rem VME streamer spike - deploy to a phone.
rem
rem   deploy.cmd                    build, install, launch, then tail the log
rem   deploy.cmd pair HOST:PORT CODE   wireless debugging, one time per phone
rem   deploy.cmd connect HOST:PORT     wireless debugging, each reboot
rem   deploy.cmd log                tail the spike log only
rem   deploy.cmd devices            what adb can see
rem   deploy.cmd apk                just print the APK path (for a manual copy)
rem
rem WIRELESS IS THE POINT HERE. This app is driven by an HDMI capture
rem adapter in the phone's only USB port, so for the interesting half of
rem the testing the cable cannot be plugged in at the same time. Pair over
rem Wi-Fi once and you can rebuild, reinstall and read logcat while the
rem adapter stays connected.

if not defined VME_ANDROID_TOOLCHAIN set "VME_ANDROID_TOOLCHAIN=%LOCALAPPDATA%\vme-android"
set "TOOLCHAIN=%VME_ANDROID_TOOLCHAIN%"
set "ADB=%TOOLCHAIN%\sdk\platform-tools\adb.exe"
set "PKG=works.vme.streamer"
set "APK=%~dp0app\build\outputs\apk\debug\app-debug.apk"

if not exist "%ADB%" (
    echo.
    echo   No toolchain at %TOOLCHAIN%
    echo   Run: powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\bootstrap.ps1
    echo.
    exit /b 1
)

cd /d "%~dp0"

if /i "%~1"=="pair" (
    if "%~3"=="" echo   usage: deploy.cmd pair HOST:PORT CODE  ^(both from the phone's Wireless debugging screen^) & exit /b 1
    "%ADB%" pair %2 %3
    exit /b %ERRORLEVEL%
)

if /i "%~1"=="connect" (
    if "%~2"=="" echo   usage: deploy.cmd connect HOST:PORT & exit /b 1
    "%ADB%" connect %2
    exit /b %ERRORLEVEL%
)

if /i "%~1"=="devices" (
    "%ADB%" devices -l
    exit /b %ERRORLEVEL%
)

if /i "%~1"=="apk" (
    if not exist "%APK%" call :build || exit /b 1
    echo %APK%
    exit /b 0
)

if /i "%~1"=="log" goto :tail

rem ---- default: build, install, launch, tail -----------------------------

call :build || exit /b 1

echo.
echo === checking for a device
"%ADB%" get-state >nul 2>&1
if errorlevel 1 (
    echo.
    echo   No device. Either:
    echo.
    echo     USB cable   - enable Developer options then USB debugging,
    echo                   plug in, and accept the RSA prompt on the phone.
    echo.
    echo     Wireless    - Developer options ^> Wireless debugging ^>
    echo                   Pair device with pairing code, then:
    echo                     deploy.cmd pair  IP:PAIRPORT CODE
    echo                     deploy.cmd connect IP:PORT
    echo                   ^(the two ports are different; the pairing one
    echo                    is on the popup, the other on the main screen^)
    echo.
    echo     Sideload    - copy this file to the phone and tap it:
    echo                     %APK%
    echo.
    exit /b 1
)

echo.
echo === installing
rem -r reinstall, -t allow test builds, -g pre-grant runtime permissions so
rem the harness does not stop to ask for the microphone.
"%ADB%" install -r -t -g "%APK%"
if errorlevel 1 (
    echo.
    echo   Install failed. If it says INSTALL_FAILED_UPDATE_INCOMPATIBLE the
    echo   phone has a copy signed with a different debug key:
    echo     "%ADB%" uninstall %PKG%
    echo.
    exit /b 1
)

echo.
echo === launching
"%ADB%" shell monkey -p %PKG% -c android.intent.category.LAUNCHER 1 >nul 2>&1

:tail
echo.
echo === logcat ^(Ctrl+C to stop^)
"%ADB%" logcat -c
"%ADB%" logcat -s VMESpike:V UVCCamera:V libUVCCamera:V UVCPreview:V RootEncoder:V AndroidRuntime:E
exit /b %ERRORLEVEL%

:build
echo === building
call build.cmd assembleDebug
exit /b %ERRORLEVEL%
