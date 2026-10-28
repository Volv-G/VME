@echo off
rem VME streamer - deploy to a phone. The work is in kit\deploy.cmd
rem (Volv-G/android-kit), which takes the package and logcat tags from
rem kit.properties:
rem   deploy.cmd  |  deploy.cmd pair CODE  |  deploy.cmd connect  |  deploy.cmd log  |  deploy.cmd apk
if not exist "%~dp0kit\deploy.cmd" (
    echo   android\kit is empty. Run:  git submodule update --init
    exit /b 1
)
call "%~dp0kit\deploy.cmd" %*
