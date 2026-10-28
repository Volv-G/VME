@echo off
rem VME streamer - build. The work is in kit\build.cmd (Volv-G/android-kit):
rem   build.cmd  |  build.cmd clean  |  build.cmd testDebugUnitTest  |  build.cmd <task...>
if not exist "%~dp0kit\build.cmd" (
    echo   android\kit is empty. Run:  git submodule update --init
    exit /b 1
)
call "%~dp0kit\build.cmd" %*
