"""Copy old panel data into the unified workspace; models are referenced, never moved."""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import BASE_DIR, load_config, save_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True, help="Old PersonalControlPanel directory")
    parser.add_argument("--library", type=Path, required=True, help="Old Universal_Library_AI or processed-file directory")
    args = parser.parse_args()
    panel_root, library_root = args.panel.expanduser().resolve(), args.library.expanduser().resolve()
    if not panel_root.is_dir() or not library_root.is_dir():
        raise ValueError("Both existing directories must be valid.")
    if panel_root == BASE_DIR:
        raise ValueError("Select the old panel directory, not this new workspace.")
    backup = BASE_DIR / "migration-backups" / str(time.time_ns())
    copied = 0
    for folder in ("notes", "tasks"):
        original = panel_root / folder
        if not original.is_dir():
            continue
        for source in original.rglob("*.md"):
            relative = source.relative_to(panel_root)
            if source.is_symlink() or any(p.startswith(".") for p in source.relative_to(original).parts):
                continue
            target = BASE_DIR / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            shutil.copy2(source, target)
            copied += 1
    config = load_config()
    source_cfg = panel_root / "config.json"
    if source_cfg.exists():
        old = json.loads(source_cfg.read_text(encoding="utf-8-sig"))
        for key in ("usage", "notes_order", "notes_pinned", "notes_sort", "notes_last_used", "notes_graph_pos"):
            if key in old:
                config[key] = old[key]
    target_cfg = BASE_DIR / "config.json"
    if target_cfg.exists():
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target_cfg, backup / "config.json")
    config["library"]["default_source"] = str(library_root)
    models = list((library_root / "ai_models").glob("**/Qwen*.gguf"))
    if models:
        config["library"]["model_path"] = str(models[0])
    save_config(config)
    print(f"Copied {copied} Markdown files. Originals and AI models stayed in place.")
    print(f"Replaced workspace files were backed up to {backup}.")
    print("Launch START.bat. Review Settings > AI Library, then use Launch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
