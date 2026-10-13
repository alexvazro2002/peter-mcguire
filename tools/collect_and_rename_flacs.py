"""Copy FLACs into a new playlist, naming only new copies."""
import argparse
import re
import shutil
from pathlib import Path


def clean_text(text):
    return re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(text))).strip().rstrip(".")


def fallback_from_filename(filename):
    name = re.sub(r"^\s*\d{1,3}(?:[-_.]\d{1,3})?\s*[-_.]?\s*", "", Path(filename).stem)
    name = re.sub(r"\b(?:CD|Disc)[-_. ]?\d+\b", "", name, flags=re.I).replace("_", " ")
    if " - " in name:
        artist, title = name.split(" - ", 1)
        return clean_text(title), clean_text(artist)
    return clean_text(name), "Unknown Artist"


def unique_path(path):
    original, i = path, 2
    while path.exists():
        path = original.with_name(f"{original.stem} ({i}){original.suffix}")
        i += 1
    return path


def main():
    from mutagen.flac import FLAC
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("Source directory does not exist.")
    destination = (args.destination or Path.home() / "Desktop" / "Playlist").expanduser().resolve()
    if destination == source or source in destination.parents:
        raise ValueError("Choose an output outside the source tree.")
    destination = unique_path(destination)
    files = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".flac")
    destination.mkdir(parents=True)
    failures = 0
    for i, path in enumerate(files, 1):
        title, artist = fallback_from_filename(path.name)
        try:
            audio = FLAC(path)
            title = clean_text(audio.get("title", [title])[0]) or title
            artist = clean_text(audio.get("artist", [artist])[0]) or artist
        except Exception:
            print(f"Metadata unavailable for {path.name}; using filename fallback.")
        try:
            target = unique_path(destination / f"{title[:100]} - {artist[:80]}.flac")
            shutil.copy2(path, target)
            print(f"[{i}/{len(files)}] {target.name}")
        except OSError as error:
            failures += 1
            print(f"Copy failed: {path.name}: {error}")
    print(f"Playlist: {destination}\nCopied {len(files) - failures}/{len(files)}. Original files untouched.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
