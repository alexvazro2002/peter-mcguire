#!/usr/bin/env bash
set -euo pipefail
PETER_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PETER_ROOT"
export PATH="$HOME/.local/bin:$HOME/.lmstudio/bin:$PATH"
export PYTHONUNBUFFERED=1
if [[ -d "$PETER_ROOT/.runtime/lib" ]]; then
    export LD_LIBRARY_PATH="$PETER_ROOT/.runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
if [[ ! -x "$PETER_ROOT/.venv/bin/python" ]]; then
    printf 'Run ./install-linux.sh once to prepare Peter.\n' >&2
    command -v notify-send >/dev/null && notify-send 'Peter needs setup' 'Run install-linux.sh in NexusDesk first.' || true
    exit 1
fi
exec "$PETER_ROOT/.venv/bin/python" "$PETER_ROOT/scripts/launch_linux.py" "$@"
