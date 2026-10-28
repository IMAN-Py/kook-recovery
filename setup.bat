@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title djvu-recover - setup

echo ================================================================
echo   djvu-recover - one-time setup
echo ================================================================
echo.
echo This installs numpy and Pillow into a private folder (.venv)
echo next to the tool. Nothing else on your computer is changed.
echo.

rem ---------------------------------------------------------------- 1. Python
set "PY="
py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    python3 -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python3"
)
if not defined PY goto :no_python

set "PYVER=unknown"
for /f "delims=" %%V in ('%PY% --version 2^>^&1') do set "PYVER=%%V"
echo   Using %PYVER%
echo.

rem ---------------------------------------------------------------- 2. .venv
if exist ".venv\Scripts\python.exe" (
    echo   Reusing the existing .venv
) else (
    echo   Creating .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :venv_failed
)
set "VPY=.venv\Scripts\python.exe"

rem ---------------------------------------------------------------- 3. libraries
echo   Installing numpy and Pillow - needs internet, may take a minute ...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
if errorlevel 1 goto :pip_failed
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto :pip_failed
echo.

rem ---------------------------------------------------------------- 4. check
echo   Checking the installation ...
"%VPY%" recover.py --check
if errorlevel 1 goto :check_failed
echo.

"%VPY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto :no_tk
echo   App window ready - double-click  app.bat  to open it.
goto :after_tk
:no_tk
echo   Note: tkinter is missing, so app.bat cannot open a window.
echo   The command line (run.bat) still works.
:after_tk
echo.

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo   Optional: ffmpeg is not installed. It is only needed for sound in
    echo   rebuilt videos - everything else works without it.
) else (
    echo   ffmpeg found - rebuilt videos keep their sound.
)
echo.
echo ================================================================
echo   Setup complete.
echo     app.bat    - open the app window (recommended)
echo     run.bat    - command line
echo ================================================================
echo.
pause
exit /b 0

rem ---------------------------------------------------------------- errors
:no_python
echo   Python 3.11 or newer was not found on this computer.
echo.
where winget >nul 2>&1
if errorlevel 1 goto :python_manual
set "YN="
set /p "YN=  Install it now with winget? [Y/n] "
if /i "%YN%"=="n" goto :python_manual
echo.
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :python_manual
echo.
echo   Python is installed. Close this window and double-click setup.bat again
echo   (a new PATH is only picked up by a fresh window).
echo.
pause
exit /b 1

:python_manual
echo.
echo   1. Open https://www.python.org/downloads/ and install Python 3.11 or newer.
echo   2. On the first screen TICK "Add python.exe to PATH".
echo   3. Then double-click setup.bat again.
echo.
start "" https://www.python.org/downloads/
pause
exit /b 1

:venv_failed
echo.
echo   Could not create the private environment (.venv).
echo   Try it by hand in this folder:
echo       %PY% -m venv .venv
echo.
pause
exit /b 1

:pip_failed
echo.
echo   Could not download the libraries.
echo   - Check the internet connection, proxy or antivirus, then run setup.bat again.
echo   - Or install them by hand:
echo       .venv\Scripts\python.exe -m pip install numpy Pillow
echo.
pause
exit /b 1

:check_failed
echo.
echo   The libraries installed but the self-check failed - see the message above.
echo.
pause
exit /b 1
