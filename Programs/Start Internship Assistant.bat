@echo off
setlocal
title Handshake Internship Assistant
cd /d "%~dp0.."

rem Prefer the official Python launcher, then a python.exe on PATH.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3 is not installed on this computer.
    echo.
    echo Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup, then try again.
    echo.
    pause
    exit /b 1
)

%PY% launcher.py %*
set "CODE=%ERRORLEVEL%"

rem Keep the window open after a normal double-click so results stay readable.
if "%~1"=="" (
    echo.
    echo Finished. Press any key to close this window.
    pause >nul
)
exit /b %CODE%
