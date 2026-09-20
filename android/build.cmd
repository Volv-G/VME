@echo off
setlocal
rem VME streamer spike - build entry point.
rem
rem Wraps gradlew with the toolchain that android\scripts\bootstrap.ps1
rem installs, so nothing has to be on PATH and no JAVA_HOME has to be set
rem machine-wide.
rem
rem   build.cmd              assemble the debug APK
rem   build.cmd install      assemble and push to the connected phone
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
if /i "%TASK%"=="install" set "TASK=installDebug"

call gradlew.bat %TASK%
set "RC=%ERRORLEVEL%"

if %RC%==0 if exist "app\build\outputs\apk\debug\app-debug.apk" (
    echo.
    echo   APK: %~dp0app\build\outputs\apk\debug\app-debug.apk
)

exit /b %RC%
