#!/usr/bin/env python3
"""
Strip All — flatten every file from a nested folder tree into a new
Desktop directory (copy only; originals are never modified or deleted).

Large files (>100 MB) prompt before copy with:
  Skip / Skip all / Allow / Allow all
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path


SIZE_LIMIT = 100 * 1024 * 1024  # 100 MB
COPY_BUF = 1024 * 1024


def human_mb(n: int) -> str:
    mb = n / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"


def safe_print(msg: str) -> None:
    """Print safely on Windows consoles that choke on Unicode."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def safe_slug(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        elif ch == " ":
            keep.append("_")
    slug = "".join(keep).strip("._") or "folder"
    return slug[:50]


def unique_dest_dir(desktop: Path, source: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"Stripped_{safe_slug(source.name)}_{stamp}"
    candidate = desktop / base
    n = 2
    while candidate.exists():
        candidate = desktop / f"{base}_{n}"
        n += 1
    return candidate


def make_unique(path: Path) -> Path:
    if not path.exists():
        return path
    directory = path.parent
    stem = path.stem
    suffix = path.suffix
    n = 2
    while True:
        candidate = directory / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def copy_file_verified(src: Path, dst: Path) -> None:
    """
    Full binary copy + size check.
    Streams in 1 MiB chunks, then verifies dest size matches source.
    """
    src_size = src.stat().st_size

    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            chunk = fsrc.read(COPY_BUF)
            if not chunk:
                break
            fdst.write(chunk)
        fdst.flush()
        try:
            os.fsync(fdst.fileno())
        except OSError:
            pass

    try:
        shutil.copystat(src, dst, follow_symlinks=True)
    except Exception:
        pass

    dst_size = dst.stat().st_size
    if dst_size != src_size:
        try:
            dst.unlink(missing_ok=True)
        except Exception:
            pass
        raise OSError(
            f"Incomplete copy: source {src_size} bytes, dest {dst_size} bytes"
        )


def ask_large_file(filename: str, size_bytes: int) -> str:
    """Modal dialog. Returns one of: skip, skip_all, allow, allow_all."""
    result = {"choice": "skip"}

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    win = tk.Toplevel(root)
    win.title("Large file")
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass
    win.resizable(False, False)
    win.configure(bg="#f0f0f0")

    msg = f"{filename}\nis {human_mb(size_bytes)}"
    tk.Label(
        win,
        text=msg,
        font=("Segoe UI", 11),
        bg="#f0f0f0",
        justify="center",
        padx=20,
        pady=16,
    ).pack()

    btn_row = tk.Frame(win, bg="#f0f0f0")
    btn_row.pack(pady=(0, 16), padx=12)

    def choose(val: str):
        result["choice"] = val
        win.destroy()
        root.destroy()

    styles = [
        ("Skip", "skip", "#95a5a6"),
        ("Skip all", "skip_all", "#7f8c8d"),
        ("Allow", "allow", "#27ae60"),
        ("Allow all", "allow_all", "#1e8449"),
    ]
    for text, val, color in styles:
        tk.Button(
            btn_row,
            text=text,
            command=lambda v=val: choose(v),
            bg=color,
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=12,
            pady=6,
            cursor="hand2",
        ).pack(side="left", padx=4)

    win.protocol("WM_DELETE_WINDOW", lambda: choose("skip"))
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
    win.grab_set()
    win.focus_force()
    root.mainloop()
    return result["choice"]


def write_path_txt(root: Path, output_file: Path) -> None:
    """Write every folder and file path below root to Path.txt."""
    lines = [str(root)]

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames.sort(key=str.lower)

        for dirname in dirnames:
            lines.append(str(current / dirname))

        for filename in sorted(filenames, key=str.lower):
            lines.append(str(current / filename))

    output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    # Prefer UTF-8 console output when the host supports it
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Flatten all files from a folder tree into a new Desktop directory (copy only)"
    )
    parser.add_argument(
        "--source",
        "-s",
        required=True,
        help="Source folder to flatten",
    )
    parser.add_argument(
        "--path-txt",
        action="store_true",
        help="Create Path.txt inside the stripped output folder after the run",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        print(f"ERROR: Path does not exist: {source}", file=sys.stderr)
        return 1
    if not source.is_dir():
        print(f"ERROR: Not a directory: {source}", file=sys.stderr)
        return 1

    desktop = (Path.home() / "Desktop").resolve()
    desktop.mkdir(parents=True, exist_ok=True)
    dest = unique_dest_dir(desktop, source)
    dest.mkdir(parents=True, exist_ok=True)

    safe_print("=" * 60)
    safe_print("  STRIP ALL  (copy only - originals untouched)")
    safe_print("=" * 60)
    safe_print(f"Source : {source}")
    safe_print(f"Output : {dest}")
    safe_print(f"Limit  : files > {human_mb(SIZE_LIMIT)} will ask before copy")
    safe_print("=" * 60)
    safe_print("")

    skip_all = False
    allow_all = False
    copied = 0
    skipped = 0
    errors = 0
    total_bytes = 0

    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {".git", "node_modules", "__pycache__", ".cache"}
            and not d.startswith("$")
        ]
        for name in filenames:
            src_path = Path(dirpath) / name
            try:
                size = src_path.stat().st_size
            except (OSError, PermissionError) as e:
                safe_print(f"SKIP (unreadable): {src_path}  ({e})")
                errors += 1
                continue

            if size > SIZE_LIMIT:
                if skip_all:
                    safe_print(f"SKIP (skip all): {name}  ({human_mb(size)})")
                    skipped += 1
                    continue
                if not allow_all:
                    choice = ask_large_file(name, size)
                    if choice == "skip":
                        safe_print(f"SKIP: {name}  ({human_mb(size)})")
                        skipped += 1
                        continue
                    if choice == "skip_all":
                        skip_all = True
                        safe_print(f"SKIP ALL from now on  (skipped {name})")
                        skipped += 1
                        continue
                    if choice == "allow_all":
                        allow_all = True
                        safe_print("ALLOW ALL large files from now on")

            out_path = make_unique(dest / name)
            try:
                copy_file_verified(src_path, out_path)
            except Exception as e:
                safe_print(f"ERROR copying {src_path}: {e}")
                errors += 1
                try:
                    if out_path.exists():
                        out_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue

            # Copy succeeded — never delete dest because of a print failure
            try:
                final_size = out_path.stat().st_size
            except Exception:
                final_size = size
            copied += 1
            total_bytes += final_size
            safe_print(f"[{copied}] {name}  ->  {out_path.name}  ({human_mb(final_size)})")

    if args.path_txt:
        path_txt = dest / "Path.txt"
        try:
            write_path_txt(source, path_txt)
            safe_print(f"Path map : {path_txt}")
        except Exception as e:
            safe_print(f"ERROR creating Path.txt: {e}")

    safe_print("")
    safe_print("=" * 60)
    safe_print("  DONE")
    safe_print("=" * 60)
    safe_print(f"Copied  : {copied}")
    safe_print(f"Skipped : {skipped}")
    safe_print(f"Errors  : {errors}")
    safe_print(f"Total   : {human_mb(total_bytes)}")
    safe_print(f"Folder  : {dest}")
    safe_print("")
    safe_print("Original files were NOT modified or deleted.")
    safe_print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
