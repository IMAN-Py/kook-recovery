#!/bin/sh
# One-time setup for macOS / Linux.  Windows: use setup.bat instead.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11 or newer is required: https://www.python.org/downloads/"
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "Python $(python3 -V 2>&1 | cut -d' ' -f2) is too old - 3.11 or newer is required."
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "Creating .venv ..."
    python3 -m venv .venv
fi

echo "Installing numpy and Pillow ..."
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
.venv/bin/python -m pip install -r requirements.txt --quiet --disable-pip-version-check

echo "Checking the installation ..."
.venv/bin/python recover.py --check

if .venv/bin/python -c "import tkinter" >/dev/null 2>&1; then
    echo "App window ready - start it with:  ./.venv/bin/python app.py"
else
    echo "Note: tkinter is missing, so the app window cannot open (the command line still works)."
    echo "      Debian/Ubuntu: sudo apt install python3-tk   |   macOS: brew install python-tk"
fi

if command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg found - rebuilt videos keep their sound."
else
    echo "Optional: ffmpeg not found (only needed for sound in rebuilt videos)."
fi
echo
echo "Setup complete.  Start the tool with:  ./.venv/bin/python recover.py"
