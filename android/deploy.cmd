@echo off
setlocal EnableDelayedExpansion
rem VME streamer spike - deploy to a phone.
rem
rem   deploy.cmd                    build, install, launch, then tail the log
rem   deploy.cmd find               show phones advertising wireless debugging
rem   deploy.cmd pair CODE          pair, discovering the address over mDNS
rem   deploy.cmd pair HOST:PORT CODE    pair with an address typed by hand
rem   deploy.cmd connect            connect, discovering the address
rem   deploy.cmd connect HOST:PORT  connect to an address typed by hand
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

if /i "%~1"=="pair"    goto :do_pair
if /i "%~1"=="connect" goto :do_connect
if /i "%~1"=="find"    goto :do_find
if /i "%~1"=="log"     goto :tail

if /i "%~1"=="devices" (
    "%ADB%" devices -l
    exit /b %ERRORLEVEL%
)

if /i "%~1"=="apk" (
    if not exist "%APK%" call :build || exit /b 1
    echo %APK%
    exit /b 0
)

rem ---- default: build, install, launch, tail -----------------------------

call :build || exit /b 1

echo.
echo === checking for a device
call :pick_device
if defined SERIAL echo   using !SERIAL!
if not defined SERIAL (
    if defined UNAUTHORIZED (
        echo.
        echo   Found !UNAUTHORIZED! but it has not authorised this PC.
        echo   Unlock the phone and accept the 'Allow USB debugging' prompt.
        echo.
        exit /b 1
    )
    echo.
    echo   No device. Either:
    echo.
    echo     Wireless    - Developer options ^> Wireless debugging ^> on.
    echo                   Open 'Pair device with pairing code', then:
    echo                     deploy.cmd pair 123456      ^(the phone's code^)
    echo                     deploy.cmd connect
    echo                   Addresses are discovered for you. Pairing is once
    echo                   per phone; connect again after a reboot.
    echo.
    echo     USB cable   - enable Developer options then USB debugging,
    echo                   plug in, and accept the RSA prompt on the phone.
    echo                   ^(Remember the capture adapter wants that port.^)
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
"%ADB%" -s !SERIAL! install -r -t -g "%APK%"
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
"%ADB%" -s !SERIAL! shell monkey -p %PKG% -c android.intent.category.LAUNCHER 1 >nul 2>&1
goto :tail_with_serial

:tail
call :pick_device
if not defined SERIAL (
    echo   No device to read logs from. Run: deploy.cmd connect
    exit /b 1
)

:tail_with_serial
echo.
echo === logcat ^(Ctrl+C to stop^)
"%ADB%" -s !SERIAL! logcat -c
"%ADB%" -s !SERIAL! logcat -s VMESpike:V UVCCamera:V libUVCCamera:V UVCPreview:V RootEncoder:V AndroidRuntime:E
exit /b %ERRORLEVEL%

:build
echo === building
call build.cmd assembleDebug
exit /b %ERRORLEVEL%

rem ---- wireless debugging ------------------------------------------------
rem
rem Phones advertise two mDNS services and they are NOT interchangeable:
rem
rem   _adb-tls-pairing  present only while the pairing popup is open -> pair
rem   _adb-tls-connect  present whenever wireless debugging is on    -> connect
rem
rem They sit on different ports, and handing adb the wrong one fails with
rem "protocol fault (couldn't read status message)", which names neither the
rem cause nor the fix. So the address is discovered rather than typed, and
rem using the wrong service is detected and explained instead of being
rem documented and hoped about.

:do_find
echo.
echo === phones advertising wireless debugging
"%ADB%" mdns services
call :lookup pairing PAIRADDR
call :lookup connect CONNADDR
echo.
if defined PAIRADDR (
    echo   pairing popup open at !PAIRADDR!
    echo     run:  deploy.cmd pair CODE      ^(the 6 digits on the popup^)
) else (
    echo   No pairing service. That is normal unless the
    echo   'Pair device with pairing code' popup is open right now.
)
if defined CONNADDR (
    echo   wireless debugging at !CONNADDR!
    echo     run:  deploy.cmd connect        ^(only works once paired^)
)
if not defined CONNADDR if not defined PAIRADDR (
    echo   Nothing found. The phone must be on the SAME Wi-Fi network with
    echo   Wireless debugging switched on. Some networks block mDNS between
    echo   clients; if so, read the address off the phone and pass it in.
)
echo.
exit /b 0

:do_pair
set "ARG2=%~2"
set "CODE=%~3"

rem One argument is the code; find the address ourselves.
if "%CODE%"=="" (
    set "CODE=%ARG2%"
    set "ARG2="
)
if "%CODE%"=="" (
    echo.
    echo   usage: deploy.cmd pair CODE
    echo   The code is the 6 digits shown by 'Pair device with pairing code'.
    echo.
    exit /b 1
)

call :lookup pairing PAIRADDR
call :lookup connect CONNADDR

if not "%ARG2%"=="" (
    rem Catch the common mistake: the address off the main Wireless
    rem debugging screen, which is the connect service, not pairing.
    if "%ARG2%"=="!CONNADDR!" (
        echo.
        echo   !ARG2! is the CONNECT service, not the pairing one.
        echo   They are different ports. Pairing is advertised only while the
        echo   'Pair device with pairing code' popup is open.
        if defined PAIRADDR echo   Pairing is currently at !PAIRADDR! - just run: deploy.cmd pair %CODE%
        echo.
        exit /b 1
    )
    set "PAIRADDR=%ARG2%"
)

if not defined PAIRADDR (
    echo.
    echo   No pairing service found.
    echo   On the phone: Developer options ^> Wireless debugging ^>
    echo   'Pair device with pairing code'. Leave that popup ON SCREEN -
    echo   the pairing service exists only while it is open - then re-run:
    echo     deploy.cmd pair %CODE%
    echo.
    exit /b 1
)

echo   pairing with !PAIRADDR!
"%ADB%" pair !PAIRADDR! %CODE%
set "RC=!ERRORLEVEL!"
if not "!RC!"=="0" (
    echo.
    echo   Pairing failed. Usual causes, in order:
    echo     - the popup was dismissed ^(the code and port both expire with it^)
    echo     - the code was mistyped, or came from a previous popup
    echo     - PC and phone are on different networks or VLANs
    echo.
    exit /b !RC!
)
echo.
echo   Paired. Now run:  deploy.cmd connect
exit /b 0

:do_connect
set "CONNADDR=%~2"
if "%CONNADDR%"=="" call :lookup connect CONNADDR
if not defined CONNADDR (
    echo.
    echo   No device advertising wireless debugging.
    echo   Turn on Developer options ^> Wireless debugging, or pass the
    echo   address from that screen:  deploy.cmd connect HOST:PORT
    echo.
    exit /b 1
)
echo   connecting to !CONNADDR!
"%ADB%" connect !CONNADDR!
rem adb connect exits 0 even when it prints "failed to connect", so ask adb
rem what it actually has. Not get-state though: adb usually ALSO auto-
rem connects to the same phone over mDNS, so the normal state after a
rem successful connect is two transports to one handset, and get-state
rem answers "more than one device/emulator" - failure-shaped output for a
rem working setup.
call :pick_device
if not defined SERIAL (
    echo.
    echo   Not connected. If this phone has never been paired with this PC,
    echo   pair first - connect alone is not enough:
    echo     deploy.cmd pair CODE
    echo.
    exit /b 1
)
echo   connected as !SERIAL!. Now run:  deploy.cmd
exit /b 0

rem :pick_device - set SERIAL to the first usable device, or UNAUTHORIZED to
rem a device that is refusing us. `adb devices` prints "<serial> <state>",
rem with a header line that tokenises harmlessly.
rem
rem One phone can legitimately appear more than once - an explicit
rem connect and adb's own mDNS auto-connect are separate transports to the
rem same handset - so every later adb call is pinned with -s rather than
rem left to guess.
:pick_device
set "SERIAL="
set "UNAUTHORIZED="
for /f "tokens=1,2" %%a in ('"%ADB%" devices 2^>nul') do (
    if "%%b"=="device" if not defined SERIAL set "SERIAL=%%a"
    if "%%b"=="unauthorized" if not defined UNAUTHORIZED set "UNAUTHORIZED=%%a"
)
exit /b 0

rem :lookup <pairing^|connect> <varname>
rem Reads `adb mdns services`, whose columns are:
rem   <instance>  _adb-tls-<kind>._tcp  <host:port>
:lookup
set "_want=_adb-tls-%~1._tcp"
set "%~2="
for /f "tokens=1,2,3" %%a in ('"%ADB%" mdns services 2^>nul') do (
    if "%%b"=="!_want!" if not defined %~2 set "%~2=%%c"
)
exit /b 0
