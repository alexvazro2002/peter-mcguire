#!/usr/bin/env python3
"""Explicit, revision-pinned installation of Peter's local British stock voice."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO = "csukuangfj/kokoro-int8-multi-lang-v1_0"
REVISION = "2a360693d79b88b49b88e29aec2b53577f41f206"
HASHES = {"model.int8.onnx": "4b86207ef680e394d8343bee22dfc4c512e5c707c6d9578e3f35ab09bffd6b36",
          "voices.bin": "1c5a5b983d3d50d8586d437a51f3faa2da7919ce76a013c081e65671a3447c29"}


def verify(root):
    for name, expected in HASHES.items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return False
    return all((root / name).is_file() for name in ("tokens.txt", "LICENSE", "espeak-ng-data/phontab",
               "espeak-ng-data/phonindex", "espeak-ng-data/phondata", "espeak-ng-data/en_dict",
               "espeak-ng-data/intonations", "espeak-ng-data/lang/gmw/en"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.output.expanduser().resolve()
    import sherpa_onnx
    if not hasattr(sherpa_onnx, "OfflineTtsKokoroModelConfig"):
        raise RuntimeError("Install sherpa-onnx 1.13.6 in the isolated VoiceStudio runtime first.")
    if not verify(root):
        from huggingface_hub import snapshot_download
        print("Installing British stock voice (~153 MiB).", flush=True)
        snapshot_download(repo_id=REPO, revision=REVISION, local_dir=root, token=False,
                          allow_patterns=["model.int8.onnx", "voices.bin", "tokens.txt", "LICENSE", "espeak-ng-data/*"])
    if not verify(root):
        raise RuntimeError("British voice integrity checks failed. No voice was selected.")
    (root / "peter-provenance.json").write_text(json.dumps(dict(repo_id=REPO, revision=REVISION,
        sha256=HASHES, voice="bm_george", speaker_id=26, language="en-gb"), indent=2) + "\n", encoding="utf-8")
    print("British male voice installed and verified.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
