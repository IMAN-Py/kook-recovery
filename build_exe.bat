@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title djvu-recover - build standalone exe

echo ================================================================
echo   Building dist\djvu-recover.exe (standalone, no Python needed)
echo ================================================================
echo.

if not exist ".venv\Scripts\python.exe" call setup.bat
if not exist ".venv\Scripts\python.exe" (
    echo   Setup did not finish - fix that first.
    pause
    exit /b 1
)

echo   Installing PyInstaller ...
".venv\Scripts\python.exe" -m pip install pyinstaller --quiet --disable-pip-version-check
if errorlevel 1 goto :pip_failed

echo   Building ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
    --name djvu-recover ^
    --paths vendor ^
    --hidden-import photophoenix ^
    --hidden-import photophoenix.fastjpeg ^
    --hidden-import photophoenix.colour ^
    --hidden-import photophoenix.video ^
    --hidden-import photophoenix.video_index ^
    --add-data "vendor;vendor" ^
    app.py
if errorlevel 1 goto :build_failed

echo.
echo ================================================================
echo   Done:  %CD%\dist\djvu-recover.exe
echo   It carries its own Python - copy it to any Windows computer
echo   and double-click it. Nothing needs to be installed there.
echo ================================================================
start "" explorer "%CD%\dist"
pause
exit /b 0

:pip_failed
echo.
echo   Could not install PyInstaller - check the internet, then run this again.
echo.
pause
exit /b 1

:build_failed
echo.
echo   The build failed - see the messages above.
echo.
pause
exit /b 1
