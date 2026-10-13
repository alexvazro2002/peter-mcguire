#!/usr/bin/env bash
# Reproduce Peter's isolated VoiceStudio installation; no root/system mutations.
# Official guide: https://github.com/debpalash/VoiceStudio/blob/v0.5.6/docs/install/linux.md
set -euo pipefail

VOICE_VERSION=0.5.6
VOICE_COMMIT=3915a62cb482bb43117aaebab57d22745bd1cc21
APPIMAGE_SHA=b44d286aa862a658374846599f9692a3a1ebe56f13e4bfca6d94885aab28a90f
PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
VOICE_DATA_ROOT=${NEXUS_DATA_DIR:-"$PROJECT_DIR"}
VOICE_RUNTIME=${VOICESTUDIO_RUNTIME:-"$PROJECT_DIR/.runtime/VoiceStudio"}
VOICE_INSTALL_DIR=${VOICESTUDIO_INSTALL_DIR:-"${XDG_BIN_HOME:-$HOME/.local/bin}"}
TORCH_VARIANT=cpu

usage() {
  cat <<'HELP'
Usage: ./install-voice.sh [--runtime DIR] [--app-dir DIR] [--torch cpu|cuda]

Installs checksummed VoiceStudio Electron 0.5.6 and its pinned Python backend.
The default CPU runtime avoids CUDA packages on machines without CUDA drivers.
Source, Python, dependencies and audio/model caches remain under .runtime/.
This explicit setup downloads missing Peter voice weights (~80 MB Apache-2.0
KittenTTS and ~150 MB MIT Whisper base) through VoiceStudio's own local API.
Existing VoiceStudio settings and models are preserved.
HELP
}
while (($#)); do
  case "$1" in
    --runtime) VOICE_RUNTIME=${2:?Missing runtime directory}; shift 2 ;;
    --app-dir) VOICE_INSTALL_DIR=${2:?Missing app installation directory}; shift 2 ;;
    --torch) TORCH_VARIANT=${2:?Missing torch variant}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { printf 'VoiceStudio 0.5.6 AppImage requires Linux x86_64.\n' >&2; exit 1; }
[[ "$TORCH_VARIANT" == cpu || "$TORCH_VARIANT" == cuda ]] || { printf 'Use --torch cpu or --torch cuda.\n' >&2; exit 2; }
for program in curl sha256sum git awk sort mktemp realpath install; do
  command -v "$program" >/dev/null || { printf 'Required executable is missing: %s\n' "$program" >&2; exit 1; }
done
if [[ ! -f "$PROJECT_DIR/scripts/install_voice_models.py" ]]; then
  printf 'Peter\x27s voice model installer is missing from this checkout.\n' >&2
  exit 1
fi
mkdir -p -- "$PROJECT_DIR/.runtime" "$VOICE_INSTALL_DIR" "$(dirname -- "$VOICE_RUNTIME")"
VOICE_RUNTIME=$(realpath -m -- "$VOICE_RUNTIME")
VOICE_INSTALL_DIR=$(realpath -m -- "$VOICE_INSTALL_DIR")
VOICE_DATA_ROOT=$(realpath -m -- "$VOICE_DATA_ROOT")
SCRATCH_DIR=$(mktemp -d "$PROJECT_DIR/.runtime/voice-install.XXXXXX")
trap 'rm -rf -- "$SCRATCH_DIR"' EXIT
ASSET="VoiceStudio-Electron-$VOICE_VERSION-linux-x64.AppImage"
RELEASE_URL="https://github.com/debpalash/VoiceStudio/releases/download/v$VOICE_VERSION"
VOICE_APP="$VOICE_INSTALL_DIR/VoiceStudio"

download() { curl --fail --location --retry 3 --connect-timeout 15 --output "$2" "$1"; }
checksum() { sha256sum -- "$1" | awk '{print $1}'; }
download "$RELEASE_URL/SHA256SUMS.txt" "$SCRATCH_DIR/SHA256SUMS.txt"
PUBLISHED_SHA=$(awk -v asset="$ASSET" '$2 == asset {print $1}' "$SCRATCH_DIR/SHA256SUMS.txt")
[[ "$PUBLISHED_SHA" == "$APPIMAGE_SHA" ]] || { printf 'Published VoiceStudio checksum does not match the verified pinned release.\n' >&2; exit 1; }
if [[ ! -f "$VOICE_APP" || $(checksum "$VOICE_APP") != "$APPIMAGE_SHA" ]]; then
  if command -v pgrep >/dev/null && { pgrep -x VoiceStudio >/dev/null || pgrep -x voicestudio-electron >/dev/null; }; then
    printf 'Quit VoiceStudio before replacing its AppImage.\n' >&2
    exit 1
  fi
  download "$RELEASE_URL/$ASSET" "$SCRATCH_DIR/$ASSET"
  [[ $(checksum "$SCRATCH_DIR/$ASSET") == "$APPIMAGE_SHA" ]] || { printf 'VoiceStudio AppImage checksum failed.\n' >&2; exit 1; }
  # Stage in the destination filesystem so the final replacement is atomic.
  install -m 755 -- "$SCRATCH_DIR/$ASSET" "$VOICE_INSTALL_DIR/.VoiceStudio.installing"
  mv -- "$VOICE_INSTALL_DIR/.VoiceStudio.installing" "$VOICE_APP"
fi
chmod u+x -- "$VOICE_APP"

if [[ -e "$VOICE_RUNTIME/backend/main.py" ]]; then
  if [[ -f "$VOICE_RUNTIME/.peter-voicestudio-commit" ]]; then
    [[ $(cat "$VOICE_RUNTIME/.peter-voicestudio-commit") == "$VOICE_COMMIT" ]] || { printf 'Existing runtime has a different pinned version; use another --runtime directory.\n' >&2; exit 1; }
  elif [[ -d "$VOICE_RUNTIME/.git" ]] && [[ $(git -C "$VOICE_RUNTIME" rev-parse HEAD) == "$VOICE_COMMIT" ]]; then
    printf '%s\n' "$VOICE_COMMIT" > "$VOICE_RUNTIME/.peter-voicestudio-commit"
  else
    printf 'Existing source at %s has no verified commit marker; use an empty --runtime directory. It was preserved.\n' "$VOICE_RUNTIME" >&2
    exit 1
  fi
else
  [[ ! -e "$VOICE_RUNTIME" || -d "$VOICE_RUNTIME/.git" ]] || { printf 'Runtime destination already exists; select an empty directory.\n' >&2; exit 1; }
  git init --quiet "$VOICE_RUNTIME"
  if ! git -C "$VOICE_RUNTIME" remote get-url origin >/dev/null 2>&1; then
    git -C "$VOICE_RUNTIME" remote add origin https://github.com/debpalash/VoiceStudio.git
  fi
  git -C "$VOICE_RUNTIME" fetch --depth 1 origin "$VOICE_COMMIT"
  git -C "$VOICE_RUNTIME" checkout --detach "$VOICE_COMMIT"
  [[ $(git -C "$VOICE_RUNTIME" rev-parse HEAD) == "$VOICE_COMMIT" ]] || { printf 'VoiceStudio source commit verification failed.\n' >&2; exit 1; }
  printf '%s\n' "$VOICE_COMMIT" > "$VOICE_RUNTIME/.peter-voicestudio-commit"
fi

# Reuse the official release's bundled uv rather than installing another toolchain.
if [[ -n ${VOICESTUDIO_UV_BIN:-} ]]; then
  UV_BIN=$VOICESTUDIO_UV_BIN
elif command -v uv >/dev/null; then
  UV_BIN=$(command -v uv)
else
  mkdir -p "$SCRATCH_DIR/extract" "$PROJECT_DIR/.runtime/tools"
  (cd "$SCRATCH_DIR/extract" && "$VOICE_APP" --appimage-extract 'resources/tools/uv')
  install -m 755 -- "$SCRATCH_DIR/extract/squashfs-root/resources/tools/uv" "$PROJECT_DIR/.runtime/tools/uv"
  UV_BIN="$PROJECT_DIR/.runtime/tools/uv"
fi
[[ -x "$UV_BIN" ]] || { printf 'The selected uv executable is unavailable: %s\n' "$UV_BIN" >&2; exit 1; }
export UV_PROJECT_ENVIRONMENT="$VOICE_RUNTIME/.venv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_DIR/.runtime/python"
export UV_CACHE_DIR="$PROJECT_DIR/.runtime/uv-cache"
export PATH="$(dirname -- "$UV_BIN"):$PATH"
export OMNIVOICE_TORCH_VARIANT="$TORCH_VARIANT"

SYNC_ARGS=(sync --frozen --no-dev --managed-python --python 3.11)
if [[ "$TORCH_VARIANT" == cpu ]]; then
  # Preserve the upstream lock. Omit its CUDA-only wheels, then substitute
  # matching CPU PyTorch versions from PyTorch's official wheel index.
  while IFS= read -r package; do
    SYNC_ARGS+=(--no-install-package "$package")
  done < <(awk -F '"' '/^name = / && ($2 ~ /^nvidia-.*-cu12$/ || $2 ~ /^(torch|torchaudio|torchvision|triton)$/) {print $2}' "$VOICE_RUNTIME/uv.lock" | sort -u)
fi
(cd "$VOICE_RUNTIME" && "$UV_BIN" "${SYNC_ARGS[@]}")
VOICE_PYTHON="$VOICE_RUNTIME/.venv/bin/python"
if [[ "$TORCH_VARIANT" == cpu ]]; then
  "$UV_BIN" pip install --python "$VOICE_PYTHON" --no-sources --no-deps \
    --index-url https://download.pytorch.org/whl/cpu \
    'torch==2.8.0+cpu' 'torchaudio==2.8.0+cpu' 'torchvision==0.23.0+cpu'
fi
# Fedora 44 rejects the executable-stack flag in the upstream CT2 4.4.0
# wheel. The 4.6.0 wheel fixes the native flag and remains compatible with
# the frozen faster-whisper package. Keep this override explicit/reproducible.
"$UV_BIN" pip install --python "$VOICE_PYTHON" --no-deps 'ctranslate2==4.6.0'
(cd "$VOICE_RUNTIME" && "$VOICE_PYTHON" scripts/setup.py)
"$VOICE_PYTHON" - <<'PY'
import json
import sys
import fastapi, uvicorn, kittentts, faster_whisper, ctranslate2
import torch, torchaudio, torchvision
print(json.dumps({"python": sys.version.split()[0], "torch": torch.__version__,
                  "cuda_available": torch.cuda.is_available(), "ctranslate2": ctranslate2.__version__}))
PY
"$VOICE_PYTHON" "$PROJECT_DIR/scripts/install_voice_models.py" --runtime "$VOICE_RUNTIME" \
  --data-dir "$VOICE_DATA_ROOT/.runtime/voice-data" --cache-dir "$VOICE_DATA_ROOT/.runtime/voice-cache"
printf 'VoiceStudio Electron %s: %s\nBackend commit: %s\nRuntime: %s\n' "$VOICE_VERSION" "$VOICE_APP" "$VOICE_COMMIT" "$VOICE_RUNTIME"
printf 'Run the Peter launcher to start the verified local voice backend automatically.\n'
