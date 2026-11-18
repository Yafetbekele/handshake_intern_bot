@echo off
setlocal
title Make resumes for my list
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

echo Tailors a resume with Claude for each posting on your apply-yourself list,
echo best fit first, with the posting's keywords added for you to check.
echo About a minute each, and it uses your Claude usage. Keep the computer awake.
echo.
set "TOP="
set /p "TOP=How many of the best postings? Press Enter for all of them: "
echo.

if defined TOP (
    %PY% list_resumes.py --top %TOP%
) else (
    %PY% list_resumes.py
)
echo.
echo A copy of this is in "Internships to apply to yourself\list_resumes_log.txt".
echo Press any key to close this window.
pause >nul
