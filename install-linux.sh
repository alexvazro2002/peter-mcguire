#!/usr/bin/env bash
# Creates only this project's environment; never installs system packages as root.
set -euo pipefail
PETER_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PETER_ROOT"
PETER_PYTHON="${PETER_PYTHON:-}"
if [[ -z "$PETER_PYTHON" ]]; then
    for candidate in python3.12 python3.11; do
        if command -v "$candidate" >/dev/null; then PETER_PYTHON="$(command -v "$candidate")"; break; fi
    done
fi
if [[ -z "$PETER_PYTHON" ]]; then
    for candidate in "$HOME"/.local/share/uv/python/cpython-3.12-*/bin/python3.12 \
                     "$HOME"/.local/share/uv/python/cpython-3.11-*/bin/python3.11; do
        if [[ -x "$candidate" ]]; then PETER_PYTHON="$candidate"; break; fi
    done
fi
if [[ -z "$PETER_PYTHON" ]]; then
    printf 'Python 3.11 or 3.12 is required for the pinned Flet desktop.\nInstall it with Fedora dnf or uv, then set PETER_PYTHON to its executable.\n' >&2
    exit 1
fi
"$PETER_PYTHON" -c 'import sys; assert (3,11) <= sys.version_info[:2] <= (3,12), "Use Python 3.11 or 3.12"'
PETER_RUNTIME="$("$PETER_PYTHON" -c 'import sys; print(sys.version_info[:2]); print(sys.base_prefix)')"
if [[ -d .venv ]]; then
    PETER_EXISTING_RUNTIME="$(.venv/bin/python -c 'import sys; print(sys.version_info[:2]); print(sys.base_prefix)' 2>/dev/null || true)"
    # venv preserves existing interpreter symlinks; mixing Python versions can
    # leave bin/python running the old interpreter with the new pyvenv.cfg.
    if [[ "$PETER_EXISTING_RUNTIME" != "$PETER_RUNTIME" ]]; then
        mv .venv ".venv-previous-$(date +%s)-$$"
    fi
fi
"$PETER_PYTHON" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/prepare_linux_libs.py --download
.venv/bin/python scripts/install_desktop.py
printf '\nPeter is ready to launch with ./start-linux.sh or your desktop shortcut.\nVoiceStudio setup is described in LINUX_PETER.md.\n'
