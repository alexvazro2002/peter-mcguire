#!/usr/bin/env python3
"""
Path Map — build a directory tree for a chosen folder, save it to the Desktop
as a uniquely named text file, and open that file automatically.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def print_tree(path: Path, lines: list, prefix: str = "") -> None:
    try:
        items = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        lines.append(prefix + "[permission denied]")
        return
    except OSError as e:
        lines.append(prefix + f"[error: {e}]")
        return

    for i, item in enumerate(items):
        is_last = i == len(items) - 1
        connector = "└── " if is_last else "├── "
        lines.append(prefix + connector + item.name)
        if item.is_dir():
            extension = "    " if is_last else "│   "
            print_tree(item, lines, prefix + extension)


def safe_slug(name: str) -> str:
    """Filename-safe slug from a folder name."""
    keep = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        elif ch == " ":
            keep.append("_")
    slug = "".join(keep).strip("._") or "folder"
    return slug[:60]


def unique_output_path(desktop: Path, source: Path) -> Path:
    """
    Desktop/path_map_<folder>_<YYYYMMDD_HHMMSS>.txt
    If that exists, append _2, _3, ... so nothing is overwritten.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"path_map_{safe_slug(source.name)}_{stamp}"
    candidate = desktop / f"{base}.txt"
    n = 2
    while candidate.exists():
        candidate = desktop / f"{base}_{n}.txt"
        n += 1
    return candidate


def open_file(path: Path) -> None:
    """Open the file with the OS default app."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        print(f"Could not open file automatically: {e}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Map a directory tree to a Desktop text file and open it"
    )
    parser.add_argument(
        "--source", "-s",
        default="",
        help="Folder to map (required from the control panel)",
    )
    args = parser.parse_args()

    if args.source:
        root = Path(args.source).expanduser().resolve()
    else:
        root = Path.cwd().resolve()

    if not root.exists():
        print(f"Path does not exist: {root}", file=sys.stderr)
        return 1
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    desktop = (Path.home() / "Desktop").resolve()
    desktop.mkdir(parents=True, exist_ok=True)

    out_path = unique_output_path(desktop, root)

    lines = [
        f"Path map: {root}",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    print_tree(root, lines)

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote: {out_path}")
    open_file(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
