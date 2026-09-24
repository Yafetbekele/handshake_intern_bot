@echo off
setlocal
title Make a cover letter
cd /d "%~dp0.."

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3 is not installed on this computer.
    pause
    exit /b 1
)

%PY% make_documents.py letter
echo.
echo Press any key to close this window.
pause >nul
