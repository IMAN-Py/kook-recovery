"""Small PyInstaller entry point that runs the bundled recovery worker."""
# Explicit imports keep dynamically loaded worker dependencies in the bundle.
import argparse
import collections
import contextlib
import datetime
import errno
import hashlib
import io
import json
import mmap
import os
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import uuid
import runpy
import sys
from pathlib import Path

# Import these directly so PyInstaller includes the image libraries used by the
# worker, even though the worker itself is loaded from the app's Resources folder.
import numpy  # noqa: F401
from PIL import Image  # noqa: F401

args = sys.argv[1:]
if args[:1] == ["-u"]:
    args = args[1:]
if len(args) < 1:
    raise SystemExit("Expected a path to app_backend.py")
worker = Path(args[0]).resolve()
sys.path.insert(0, str(worker.parent))
sys.argv = [str(worker), *args[1:]]
runpy.run_path(str(worker), run_name="__main__")
