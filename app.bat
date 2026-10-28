@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title djvu-recover

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   First run - setting things up ...
    call setup.bat
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo   Setup did not finish - see the messages above.
        pause
        exit /b 1
    )
)

start "" ".venv\Scripts\python.exe" "%~dp0app.py"
exit /b 0
