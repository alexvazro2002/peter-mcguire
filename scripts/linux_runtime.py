"""Check app-private native libraries using the same environment as the launcher."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def library_environment(root, environ=None):
    env = dict(os.environ if environ is None else environ)
    directory = Path(root) / '.runtime/lib'
    if directory.is_dir():
        previous = env.get('LD_LIBRARY_PATH', '')
        env['LD_LIBRARY_PATH'] = str(directory) + (os.pathsep + previous if previous else '')
    return env


def check_shared_library(path, root):
    # glibc reads LD_LIBRARY_PATH when a process starts, so updating os.environ
    # before ctypes.CDLL in this interpreter cannot discover transitive libraries.
    result = subprocess.run(
        [sys.executable, '-c',
         'import ctypes,sys; ctypes.CDLL(sys.argv[1])', str(path)],
        env=library_environment(root), capture_output=True, text=True,
        timeout=15,
    )
    if result.returncode:
        message = result.stderr.strip().splitlines()
        raise RuntimeError('Flet native library could not load: ' +
                           (message[-1] if message else 'native loader failed'))
