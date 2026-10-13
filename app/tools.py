"""Single tool registry and validated command construction."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
import flet as ft

from app.config import BASE_DIR


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    description: str
    category: str
    icon: str
    script: str
    fields: tuple[str, ...] = ()
    warning: str = ""


TOOLS = [
    Tool("metube", "MeTube", "One click: start the local service and open your browser.", "Media", ft.Icons.VIDEO_LIBRARY_OUTLINED,
         "metube.py"),
    Tool("stirling_pdf", "Stirling-PDF", "The full local PDF workbench: merge, split, convert, edit, and OCR.", "Files", ft.Icons.PICTURE_AS_PDF_OUTLINED,
         "stirling_pdf.py"),
    Tool("flac", "FLAC Rename", "Build a playlist with clean metadata-based names.", "Media", ft.Icons.MUSIC_NOTE_ROUNDED,
         "collect_and_rename_flacs.py", ("source", "destination")),
    Tool("converter", "File Converter", "Convert documents, images, and video.", "Files", ft.Icons.SWAP_HORIZ_ROUNDED,
         "file_converter.py", ("file", "output", "to"), "Existing output files are preserved; choose a new output folder if a name already exists."),
    Tool("compact_info", "Compact Info", "Turn a document corpus into clean AI-ready text.", "Files", ft.Icons.INVENTORY_2_OUTLINED,
         "compact_sop.py", ("source", "output")),
    Tool("duplicates", "Find Duplicates", "Review similar music filenames before cleanup.", "Media", ft.Icons.CONTENT_COPY_ROUNDED,
         "find_duplicates.py", ("source",), "Scanning is read-only. Cleanup moves candidate MP3s to a recoverable folder only when enabled."),
    Tool("path_map", "Path Map", "Export a readable directory tree to your Desktop.", "Files", ft.Icons.ACCOUNT_TREE_OUTLINED,
         "path_map.py", ("source",)),
    Tool("disk_heaviest", "Disk Heaviest", "Find the largest files and folders. Read-only.", "Files", ft.Icons.STORAGE_ROUNDED,
         "disk_heaviest.py", ("source",)),
    Tool("strip_all", "Strip All", "Copy nested files into one flat Desktop folder.", "Files", ft.Icons.DRIVE_FILE_MOVE_OUTLINED,
         "strip_all.py", ("source",)),
    Tool("mews", "Mews Segments", "Open your reservation-segment automation.", "Automation", ft.Icons.AUTO_FIX_HIGH_ROUNDED,
         "segments.py", warning="This controls your mouse and keyboard. Verify the Mews window and coordinates before starting."),
    Tool("afk", "Activity Script", "Your keyboard / mouse activity generator.", "Automation", ft.Icons.KEYBOARD_ROUNDED,
         "pythonafk.py", warning="Controls your keyboard and mouse. Press Q to stop; use only where permitted."),
]

CONVERSION = {
    ".jpg": ["png", "webp", "bmp"], ".jpeg": ["png", "webp", "bmp"],
    ".png": ["jpg", "webp", "bmp"], ".webp": ["jpg", "png"], ".bmp": ["jpg", "png"],
    ".gif": ["png", "jpg"], ".tif": ["jpg", "png"], ".tiff": ["jpg", "png"],
    ".pdf": ["txt", "docx", "compress"], ".docx": ["txt", "html"], ".doc": ["txt"],
    ".html": ["txt", "docx"], ".htm": ["txt", "docx"], ".xhtml": ["txt", "docx"],
    ".md": ["txt", "html"], ".markdown": ["txt", "html"], ".txt": ["html"],
    **{suffix: ["txt"] for suffix in (".rtf", ".csv", ".json", ".xml", ".log")},
    ".mp4": ["webm", "avi"], ".mkv": ["mp4", "webm"], ".avi": ["mp4", "webm"],
    ".mov": ["mp4", "webm"], ".webm": ["mp4"],
}


def tool_command(tool: Tool, values: dict) -> list[str]:
    script = BASE_DIR / "tools" / tool.script
    if not script.is_file():
        raise ValueError(f"Tool script is missing: {script.name}")
    command = [sys.executable, "-u", str(script)]
    for key in tool.fields:
        value = str(values.get(key, "")).strip().strip('"')
        if not value and key not in {"destination"}:
            raise ValueError(f"Enter {key.replace('_', ' ')} first.")
        if key == "source" and not Path(value).expanduser().is_dir():
            raise ValueError("Source directory does not exist.")
        if key == "file" and not Path(value).expanduser().is_file():
            raise ValueError("Input file does not exist.")
        if key == "to" and value not in CONVERSION.get(Path(values.get("file", "")).suffix.lower(), []):
            raise ValueError("Choose a supported target format for this file.")
        if value:
            if key in {"source", "file", "output", "destination"}:
                value = str(Path(value).expanduser().resolve())
            if tool.key == "converter":
                command += ["--to", value] if key == "to" else [value]
            else:
                command += ["--" + key, value]
    if tool.key == "duplicates" and values.get("cleanup"):
        command.append("--cleanup-mp3")
    return command
