"""Read-only by default; optional cleanup quarantines candidate MP3s."""
import argparse
import json
import re
import shutil
import time
from pathlib import Path


def clean_filename(filename):
    return re.sub(r"[^a-z0-9]", "", Path(filename).stem.lower())


def main():
    from rapidfuzz import fuzz
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--cleanup-mp3", action="store_true")
    parser.add_argument("--threshold", type=int, default=91)
    args = parser.parse_args()
    root = args.source.expanduser().resolve()
    if not root.is_dir() or not 0 <= args.threshold <= 100:
        raise ValueError("Invalid source or similarity threshold.")
    files = sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
                   and p.suffix.lower() in {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac"}
                   and ".duplicate-quarantine" not in p.relative_to(root).parts)
    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    report_path = desktop / f"duplicate_candidates-{time.time_ns()}.txt"
    quarantine = root / ".duplicate-quarantine" / str(time.time_ns())
    manifest, moved = [], set()
    print(f"Comparing {len(files)} music files. Filename similarity is not proof of identical audio.")
    cleaned = {p: clean_filename(p.name) for p in files}
    with report_path.open("w", encoding="utf-8") as report:
        report.write("FUZZY DUPLICATE CANDIDATES — review before removal\n\n")
        for i, a in enumerate(files):
            if i % 100 == 0:
                print(f"Comparing {i}/{len(files)}…")
            for b in files[i + 1:]:
                if a.parent == b.parent:
                    continue
                score = fuzz.ratio(cleaned[a], cleaned[b])
                if score < args.threshold:
                    continue
                report.write(f"{score:.1f}%\n{a}\n{b}\n")
                if args.cleanup_mp3 and {a.suffix.lower(), b.suffix.lower()} == {".flac", ".mp3"}:
                    mp3 = a if a.suffix.lower() == ".mp3" else b
                    if mp3 not in moved:
                        dest = quarantine / mp3.relative_to(root)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(mp3), str(dest))
                        moved.add(mp3)
                        manifest.append({"original": str(mp3), "quarantined": str(dest)})
                        (quarantine / "restore-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                        report.write(f"QUARANTINED (recoverable): {dest}\n")
                report.write("\n")
    print(f"Report: {report_path}\nQuarantined: {len(moved)}. No files permanently deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
