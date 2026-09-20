@echo off
setlocal
rem VME streamer spike - build entry point.
rem
rem Wraps gradlew with the toolchain that android\scripts\bootstrap.ps1
rem installs, so nothing has to be on PATH and no JAVA_HOME has to be set
rem machine-wide.
rem
rem   build.cmd              assemble the debug APK
rem   build.cmd install      hand off to deploy.cmd (build + install + run)
rem   build.cmd clean        wipe build outputs
rem   build.cmd <task...>    any other gradle task, passed straight through

rem Override with VME_ANDROID_TOOLCHAIN to match bootstrap.ps1 -ToolchainDir.
if not defined VME_ANDROID_TOOLCHAIN set "VME_ANDROID_TOOLCHAIN=%LOCALAPPDATA%\vme-android"
set "TOOLCHAIN=%VME_ANDROID_TOOLCHAIN%"
set "JAVA_HOME=%TOOLCHAIN%\jdk-17"
set "ANDROID_HOME=%TOOLCHAIN%\sdk"
set "ANDROID_SDK_ROOT=%ANDROID_HOME%"

if not exist "%JAVA_HOME%\bin\java.exe" (
    echo.
    echo   No toolchain found at %TOOLCHAIN%
    echo   Run this once:
    echo.
    echo     powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\bootstrap.ps1
    echo.
    exit /b 1
)

cd /d "%~dp0"

set "TASK=%*"
if "%TASK%"=="" set "TASK=assembleDebug"

rem Hand "install" to deploy.cmd rather than running gradle's installDebug.
rem They do the same job, but installDebug's failure mode with nothing
rem plugged in is a bare "DeviceException: No connected devices!" wrapped in
rem a gradle stack trace, which says nothing about wireless debugging - and
rem on this project the USB port is usually occupied by the capture adapter,
rem so "no device" is the normal state rather than an error.
if /i "%TASK%"=="install" (
    call "%~dp0deploy.cmd"
    exit /b %ERRORLEVEL%
)
if /i "%TASK%"=="installDebug" (
    call "%~dp0deploy.cmd"
    exit /b %ERRORLEVEL%
)

call gradlew.bat %TASK%
set "RC=%ERRORLEVEL%"

if %RC%==0 if exist "app\build\outputs\apk\debug\app-debug.apk" (
    echo.
    echo   APK: %~dp0app\build\outputs\apk\debug\app-debug.apk
)

exit /b %RC%
