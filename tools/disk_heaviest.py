#!/usr/bin/env python3
"""
Disk Heaviest — scan a folder and list the largest files (and folders)
so you can see what is using the most disk space.

Usage:
    python disk_heaviest.py --source "D:\\Some\\Folder"
    python disk_heaviest.py --source "D:\\Some\\Folder" --top 40 --min-mb 5
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def human_size(n: int) -> str:
    n = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def scan(root: Path, min_bytes: int = 0) -> tuple[list[tuple[int, str]], dict[str, int], int, int]:
    """
    Walk root. Returns:
      files: list of (size, path) for files >= min_bytes
      dir_sizes: cumulative size per directory path
      total_bytes, file_count
    """
    files: list[tuple[int, str]] = []
    dir_sizes: dict[str, int] = {}
    total = 0
    count = 0
    errors = 0

    root = root.resolve()
    print(f"Scanning: {root}")
    print("… this may take a while on large drives …\n")

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=None):
        # Skip common noise / permission traps on Windows
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {".git", "node_modules", "__pycache__", ".cache"}
            and not d.startswith("$")
        ]
        dp = Path(dirpath)
        folder_sum = 0
        for name in filenames:
            fp = dp / name
            try:
                size = fp.stat().st_size
            except (OSError, PermissionError):
                errors += 1
                continue
            count += 1
            total += size
            folder_sum += size
            if size >= min_bytes:
                files.append((size, str(fp)))

        # Bubble sizes up the path chain
        try:
            cur = dp.resolve()
        except OSError:
            cur = dp
        while True:
            key = str(cur)
            dir_sizes[key] = dir_sizes.get(key, 0) + folder_sum
            if cur == root or cur.parent == cur:
                break
            cur = cur.parent

    if errors:
        print(f"(skipped {errors} unreadable file(s))\n")
    return files, dir_sizes, total, count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find the heaviest files and folders under a directory"
    )
    parser.add_argument(
        "--source",
        "-s",
        required=True,
        help="Root folder to scan",
    )
    parser.add_argument(
        "--top",
        "-n",
        type=int,
        default=30,
        help="How many top files/folders to show (default 30)",
    )
    parser.add_argument(
        "--min-mb",
        type=float,
        default=1.0,
        help="Ignore files smaller than this many MB in the top-files list (default 1)",
    )
    args = parser.parse_args()

    root = Path(args.source).expanduser()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    min_bytes = int(max(0.0, args.min_mb) * 1024 * 1024)
    top_n = max(1, args.top)

    files, dir_sizes, total, count = scan(root, min_bytes=min_bytes)

    files.sort(key=lambda t: t[0], reverse=True)
    folders = sorted(dir_sizes.items(), key=lambda t: t[1], reverse=True)

    print("=" * 72)
    print(f"  TOTAL SCANNED : {human_size(total)}  ({count:,} files)")
    print(f"  ROOT        : {root}")
    print("=" * 72)

    print(f"\n## Heaviest files  (min {args.min_mb:g} MB, top {top_n})\n")
    if not files:
        print("  (none matched the minimum size)")
    else:
        width = len(str(min(top_n, len(files))))
        for i, (size, path) in enumerate(files[:top_n], 1):
            print(f"  {i:>{width}}.  {human_size(size):>10}   {path}")

    print(f"\n## Heaviest folders  (top {top_n})\n")
    # Skip the root itself as #1 always — still show it first as context
    width = len(str(min(top_n, len(folders))))
    shown = 0
    for path, size in folders:
        shown += 1
        mark = "  (root)" if path == str(root.resolve()) else ""
        print(f"  {shown:>{width}}.  {human_size(size):>10}   {path}{mark}")
        if shown >= top_n:
            break

    print("\n" + "=" * 72)
    print("Tip: delete or move the largest files/folders you no longer need.")
    print("     Empty Recycle Bin / Trash after deleting to free space.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
