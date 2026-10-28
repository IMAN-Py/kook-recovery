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

".venv\Scripts\python.exe" recover.py %*
echo.
pause
