from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".ini", ".cfg", ".conf",
    ".env", ".properties", ".sql", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".css", ".scss", ".less", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".cmd", ".ps1",
    ".yaml", ".yml", ".toml", ".tex", ".csv", ".tsv",
}

OFFICE_EXTENSIONS = {
    ".docx", ".docm", ".xlsx", ".xlsm", ".xltx", ".xltm",
    ".pptx", ".pptm", ".potx", ".potm",
}

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
}
EMAIL_EXTENSIONS = {".eml", ".msg"}
ARCHIVE_EXTENSIONS = {".zip"}  # ZIP itself is handled specially.

# Files that are usually presentation/cache/temporary material and not useful
# as independent corpus documents.
SKIP_NAMES = {
    "thumbs.db",
    "desktop.ini",
    ".ds_store",
}
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    ".idea",
    ".vscode",
}

# Extremely repetitive page furniture is often present in PDFs/HTML. We only
# remove it when it occurs many times, reducing the risk of losing real data.
REPEATED_LINE_MIN_COUNT = 4


def optional_import(module: str):
    try:
        return __import__(module)
    except ImportError:
        return None


def compact_ws(text: str) -> str:
    """Normalize whitespace without destroying line-oriented meaning."""
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_text(text: str) -> str:
    """More aggressive normalization for ordinary prose."""
    text = compact_ws(text)

    # Join lines that are clearly a wrapped paragraph, but retain headings,
    # bullets, tables and code-like lines.
    lines = text.splitlines()
    out: list[str] = []

    def looks_structural(line: str) -> bool:
        s = line.strip()
        return (
            not s
            or re.match(r"^(?:[-*+•]|\d+[.)]|[A-Z][A-Z0-9 _-]{2,}:)", s) is not None
            or s.startswith("#")
            or "\t" in line
            or re.match(r"^[^ ]+\s{2,}[^ ]+", line) is not None
        )

    paragraph = ""
    for line in lines:
        s = line.strip()
        if not s:
            if paragraph:
                out.append(paragraph)
                paragraph = ""
            if out and out[-1] != "":
                out.append("")
            continue

        if looks_structural(line):
            if paragraph:
                out.append(paragraph)
                paragraph = ""
            out.append(s)
        else:
            if paragraph:
                paragraph += " " + s
            else:
                paragraph = s

    if paragraph:
        out.append(paragraph)

    return "\n".join(out).strip()


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" .")
    return name or "unnamed"


def unique_output_path(output: Path, stem: str, suffix: str = ".txt") -> Path:
    base = safe_name(stem)
    candidate = output / (base + suffix)
    n = 2
    while candidate.exists():
        candidate = output / f"{base}__{n}{suffix}"
        n += 1
    return candidate


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def extract_plain_text(path: Path) -> str:
    raw = path.read_bytes()

    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass

    return raw.decode("utf-8", errors="replace")


def extract_json(path: Path) -> str:
    raw = extract_plain_text(path)
    try:
        obj = json.loads(raw)
        # Compact JSON while preserving exact structure/data.
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return compact_ws(raw)


def strip_html(path: Path) -> str:
    bs4 = optional_import("bs4")
    raw = extract_plain_text(path)

    if bs4:
        soup = bs4.BeautifulSoup(raw, "html.parser")

        for tag in soup([
            "script", "style", "noscript", "template", "svg", "canvas",
            "iframe", "nav", "footer", "form",
        ]):
            tag.decompose()

        # Preserve useful links as text + URL.
        for a in soup.find_all("a"):
            label = " ".join(a.stripped_strings)
            href = a.get("href")
            if href and label:
                a.replace_with(f"{label} [{href}]")
            elif href:
                a.replace_with(href)

        return compact_text(html.unescape(soup.get_text("\n")))

    # Fallback parser without external dependency.
    raw = re.sub(r"(?is)<(script|style|noscript|svg|canvas|iframe).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", "\n", raw)
    return compact_text(html.unescape(raw))


def extract_docx(path: Path) -> str:
    docx = optional_import("docx")
    if not docx:
        return "[UNAVAILABLE: python-docx not installed]\n"

    document = docx.Document(str(path))
    chunks: list[str] = []

    for p in document.paragraphs:
        text = compact_ws(p.text)
        if text:
            style = getattr(p.style, "name", "") or ""
            if style.lower().startswith("heading"):
                chunks.append(f"{style}: {text}")
            else:
                chunks.append(text)

    for ti, table in enumerate(document.tables, 1):
        rows = []
        for row in table.rows:
            cells = [compact_ws(cell.text) for cell in row.cells]
            rows.append("\t".join(cells))
        if rows:
            chunks.append(f"[TABLE {ti}]\n" + "\n".join(rows))

    return "\n".join(chunks).strip()


def extract_xlsx(path: Path) -> str:
    openpyxl = optional_import("openpyxl")
    if not openpyxl:
        return "[UNAVAILABLE: openpyxl not installed]\n"

    wb = openpyxl.load_workbook(
        str(path),
        read_only=True,
        data_only=False,
        keep_links=True,
    )

    chunks: list[str] = []

    for ws in wb.worksheets:
        rows: list[str] = []
        for row in ws.iter_rows(values_only=False):
            values = []
            has_value = False
            for cell in row:
                value = cell.value
                if value is None:
                    values.append("")
                else:
                    has_value = True
                    if isinstance(value, str):
                        value = compact_ws(value)
                    values.append(str(value))
            if has_value:
                # Remove trailing empty cells only.
                while values and values[-1] == "":
                    values.pop()
                rows.append("\t".join(values))

        if rows:
            chunks.append(f"[SHEET: {ws.title}]\n" + "\n".join(rows))

    wb.close()
    return "\n\n".join(chunks).strip()


def extract_pptx(path: Path) -> str:
    pptx = optional_import("pptx")
    if not pptx:
        return "[UNAVAILABLE: python-pptx not installed]\n"

    prs = pptx.Presentation(str(path))
    chunks: list[str] = []

    for i, slide in enumerate(prs.slides, 1):
        slide_parts: list[str] = []
        for shape in slide.shapes:
            if hasattr(shape, "text"):
                t = compact_ws(shape.text)
                if t:
                    slide_parts.append(t)

            if getattr(shape, "has_table", False):
                table = shape.table
                rows = []
                for row in table.rows:
                    rows.append(
                        "\t".join(compact_ws(cell.text) for cell in row.cells)
                    )
                if rows:
                    slide_parts.append("[TABLE]\n" + "\n".join(rows))

        if slide_parts:
            chunks.append(f"[SLIDE {i}]\n" + "\n".join(slide_parts))

    # Speaker notes
    for i, slide in enumerate(prs.slides, 1):
        try:
            notes = slide.notes_slide.notes_text_frame.text
        except Exception:
            notes = ""
        notes = compact_ws(notes)
        if notes:
            chunks.append(f"[SLIDE {i} NOTES]\n{notes}")

    return "\n\n".join(chunks).strip()


def extract_pdf(path: Path) -> str:
    pypdf = optional_import("pypdf")
    if not pypdf:
        return "[UNAVAILABLE: pypdf not installed]\n"

    reader = pypdf.PdfReader(str(path))
    pages: list[str] = []

    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = f"[PDF TEXT EXTRACTION ERROR: {exc}]"
        text = compact_text(text)
        if text:
            pages.append(f"[PAGE {i}]\n{text}")

    return "\n\n".join(pages).strip()


def extract_email(path: Path) -> str:
    if path.suffix.lower() == ".eml":
        from email import policy
        from email.parser import BytesParser

        with path.open("rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)

        lines = []
        for key in ("From", "To", "Cc", "Date", "Subject", "Reply-To"):
            value = msg.get(key)
            if value:
                lines.append(f"{key.upper()}: {compact_ws(str(value))}")

        bodies = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                if part.get_content_type() == "text/plain":
                    try:
                        bodies.append(part.get_content())
                    except Exception:
                        pass
                elif part.get_content_type() == "text/html" and not bodies:
                    try:
                        bodies.append(part.get_content())
                    except Exception:
                        pass
        else:
            try:
                bodies.append(msg.get_content())
            except Exception:
                pass

        body = compact_text("\n".join(bodies))
        if body:
            lines.append("BODY:\n" + body)

        attachments = []
        for part in msg.walk():
            filename = part.get_filename()
            if filename:
                attachments.append(filename)
        if attachments:
            lines.append("ATTACHMENTS: " + " | ".join(attachments))

        return "\n\n".join(lines).strip()

    # .msg support through extract-msg if installed.
    extract_msg = optional_import("extract_msg")
    if not extract_msg:
        return "[UNAVAILABLE: extract-msg not installed for .msg]\n"

    msg = extract_msg.Message(str(path))
    lines = []
    for label, value in (
        ("FROM", msg.sender),
        ("TO", msg.to),
        ("CC", msg.cc),
        ("DATE", msg.date),
        ("SUBJECT", msg.subject),
    ):
        if value:
            lines.append(f"{label}: {compact_ws(str(value))}")
    if msg.body:
        lines.append("BODY:\n" + compact_text(msg.body))
    try:
        attachments = [a.longFilename or a.shortFilename for a in msg.attachments]
        attachments = [x for x in attachments if x]
        if attachments:
            lines.append("ATTACHMENTS: " + " | ".join(attachments))
    except Exception:
        pass
    return "\n\n".join(lines).strip()


def extract_rtf(path: Path) -> str:
    striprtf = optional_import("striprtf")
    if striprtf:
        try:
            return compact_text(striprtf.rtf_to_text(path.read_text(errors="replace")))
        except Exception:
            pass

    raw = extract_plain_text(path)
    # Conservative fallback. RTF control words are presentation syntax, but
    # blindly removing all braces can damage unusual documents.
    raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", raw)
    raw = re.sub(r"[{}]", " ", raw)
    return compact_text(raw)


def extract_image(path: Path) -> str:
    pytesseract = optional_import("pytesseract")
    PIL = optional_import("PIL")

    if not pytesseract or not PIL:
        return "[IMAGE: OCR unavailable. Install pytesseract and Pillow, plus Tesseract OCR.]\n"

    try:
        image = PIL.Image.open(path)
        text = pytesseract.image_to_string(image)
        return compact_text(text)
    except Exception as exc:
        return f"[IMAGE OCR ERROR: {exc}]"


def extract_zip(path: Path) -> str:
    # Do not recursively dump binary ZIP internals. List members and extract
    # readable text files only.
    chunks = []
    try:
        with zipfile.ZipFile(path) as z:
            members = z.infolist()
            chunks.append(
                "[ZIP MEMBERS]\n" +
                "\n".join(m.filename for m in members if not m.is_dir())
            )

            for member in members:
                if member.is_dir():
                    continue
                ext = Path(member.filename).suffix.lower()
                if ext in TEXT_EXTENSIONS or ext in {".json", ".xml", ".html", ".htm"}:
                    try:
                        raw = z.read(member).decode("utf-8", errors="replace")
                        if ext in {".json"}:
                            try:
                                raw = json.dumps(
                                    json.loads(raw),
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                            except Exception:
                                raw = compact_text(raw)
                        elif ext in {".html", ".htm"}:
                            # Basic tag stripping.
                            raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
                            raw = re.sub(r"(?s)<[^>]+>", "\n", raw)
                            raw = compact_text(html.unescape(raw))
                        else:
                            raw = compact_text(raw)

                        if raw:
                            chunks.append(f"[FILE: {member.filename}]\n{raw}")
                    except Exception:
                        pass
    except Exception as exc:
        return f"[ZIP ERROR: {exc}]"

    return "\n\n".join(chunks).strip()


def remove_repeated_page_furniture(text: str) -> str:
    """
    Remove lines repeated many times. This is intentionally conservative.
    Useful for PDF headers/footers without requiring document-specific rules.
    """
    lines = text.splitlines()
    counts = Counter(
        line.strip()
        for line in lines
        if line.strip() and len(line.strip()) <= 180
    )

    repeated = {
        line for line, count in counts.items()
        if count >= REPEATED_LINE_MIN_COUNT
    }

    if not repeated:
        return text

    # Only remove repeated lines when they look like page furniture:
    # short, non-sentence lines, or obvious page numbering.
    filtered = []
    for line in lines:
        s = line.strip()
        if s in repeated:
            if (
                len(s) < 120
                and not re.search(r"[.!?]\s*$", s)
                or re.fullmatch(r"(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?", s, re.I)
            ):
                continue
        filtered.append(line)

    return "\n".join(filtered)


def extract(path: Path) -> str:
    ext = path.suffix.lower()

    if ext in {".json"}:
        return extract_json(path)

    if ext in {".html", ".htm", ".xhtml"}:
        return strip_html(path)

    if ext in OFFICE_EXTENSIONS:
        if ext in {".docx", ".docm"}:
            return extract_docx(path)
        if ext in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
            return extract_xlsx(path)
        if ext in {".pptx", ".pptm", ".potx", ".potm"}:
            return extract_pptx(path)

    if ext in PDF_EXTENSIONS:
        return extract_pdf(path)

    if ext in EMAIL_EXTENSIONS:
        return extract_email(path)

    if ext == ".rtf":
        return extract_rtf(path)

    if ext in IMAGE_EXTENSIONS:
        return extract_image(path)

    if ext in {".zip"}:
        return extract_zip(path)

    if ext in TEXT_EXTENSIONS or ext == "":
        return compact_text(extract_plain_text(path))

    # Try text extraction for unknown files only if they look textual.
    try:
        raw = path.read_bytes()
        sample = raw[:8192]
        if b"\x00" not in sample:
            return compact_text(raw.decode("utf-8", errors="replace"))
    except Exception:
        pass

    return (
        f"[UNSUPPORTED BINARY FILE]\n"
        f"NAME: {path.name}\n"
        f"TYPE: {mimetypes.guess_type(path.name)[0] or 'unknown'}\n"
        f"SIZE: {path.stat().st_size}\n"
        f"SHA256: {file_sha256(path)}"
    )


def make_requirements(path: Path) -> None:
    path.write_text(
        """python-docx
openpyxl
python-pptx
pypdf
beautifulsoup4
striprtf
extract-msg
Pillow
pytesseract
""",
        encoding="utf-8",
    )


def discover_files(source: Path, output: Path) -> list[Path]:
    files = []
    for root, dirs, filenames in os.walk(source):
        root_path = Path(root)

        dirs[:] = [
            d for d in dirs
            if d.lower() not in SKIP_DIR_NAMES
        ]

        for name in filenames:
            if name.lower() in SKIP_NAMES:
                continue
            p = root_path / name
            try:
                if p.is_file():
                    files.append(p)
            except OSError:
                pass

    return files


def write_document(
    output: Path,
    source_root: Path,
    source_file: Path,
    content: str,
    original_hash: str,
) -> Path:
    # Keep relative path in a small source header, but flatten the actual file.
    relative = source_file.relative_to(source_root)
    relative_str = str(relative).replace("\\", "/")

    content = remove_repeated_page_furniture(content)
    content = compact_ws(content)

    header = (
        f"SOURCE: {relative_str}\n"
        f"TYPE: {source_file.suffix.lower() or '[none]'}\n"
        f"SHA256: {original_hash}\n"
        f"---\n"
    )

    # Use original basename as the primary name. If duplicates exist, append
    # a sequential number, making references stable while avoiding collisions.
    stem = source_file.stem
    candidate = output / f"{safe_name(stem)}.txt"
    n = 2
    while candidate.exists():
        candidate = output / f"{safe_name(stem)}{n}.txt"
        n += 1

    candidate.write_text(header + content + "\n", encoding="utf-8", errors="replace")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flatten and compact a mixed file corpus for AI/code-agent use."
    )
    parser.add_argument("--source", type=Path, required=True,
                        help="Source folder to compact")
    parser.add_argument("--output", type=Path, required=True,
                        help="Output folder for compacted files")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Compatibility flag; output files are never deleted.",
    )
    parser.add_argument(
        "--write-requirements",
        action="store_true",
        help="Write requirements.txt next to this script and exit.",
    )
    args = parser.parse_args()

    if args.write_requirements:
        target = Path(__file__).with_name("requirements.txt")
        make_requirements(target)
        print(f"Wrote {target}")
        return 0

    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()

    if not source.exists() or not source.is_dir():
        print(f"ERROR: Source folder does not exist: {source}")
        return 2

    if source == output:
        print("ERROR: Output folder cannot be the source folder.")
        return 2

    # Output folder can exist - we paste into it without modifying the folder itself.
    output.mkdir(parents=True, exist_ok=True)

    source_files = discover_files(source, output)

    manifest_lines = [
        "COMPACT CORPUS INDEX",
        f"SOURCE: {source}",
        f"OUTPUT: {output}",
        f"FILES DISCOVERED: {len(source_files)}",
        "",
    ]

    failures = []
    total_in = 0
    total_out = 0
    seen_hashes: dict[str, Path] = {}
    duplicate_groups: dict[str, list[str]] = {}

    for i, path in enumerate(source_files, 1):
        print(f"[{i}/{len(source_files)}] {path}")

        try:
            size = path.stat().st_size
            total_in += size
            digest = file_sha256(path)

            if digest in seen_hashes:
                duplicate_groups.setdefault(digest, []).append(str(path))
                manifest_lines.append(
                    f"DUPLICATE: {path.relative_to(source)} == "
                    f"{seen_hashes[digest].relative_to(source)}"
                )
                continue

            seen_hashes[digest] = path

            content = extract(path)
            destination = write_document(
                output, source, path, content, digest
            )
            out_size = destination.stat().st_size
            total_out += out_size

            manifest_lines.append(
                f"{path.relative_to(source)} -> {destination.name} "
                f"({size} -> {out_size} bytes)"
            )

        except Exception as exc:
            failures.append((str(path), repr(exc)))
            manifest_lines.append(
                f"ERROR: {path.relative_to(source)} :: {type(exc).__name__}: {exc}"
            )

    # Preserve both former compaction workflows: readable summary and numbered outputs.
    manifest_lines.extend([
        "",
        f"UNIQUE FILES WRITTEN: {len(seen_hashes)}",
        f"DUPLICATE GROUPS: {len(duplicate_groups)}",
        f"FAILURES: {len(failures)}",
        f"INPUT BYTES: {total_in}",
        f"OUTPUT BYTES: {total_out}",
        f"COMPRESSION RATIO: {(total_out / total_in):.4f}" if total_in else "COMPRESSION RATIO: n/a",
    ])

    index_path = output / "_INDEX.txt"
    counter = 2
    while index_path.exists():
        index_path = output / f"_INDEX{counter}.txt"
        counter += 1
    index_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    if failures:
        with (output / "_ERRORS.txt").open("w", encoding="utf-8") as f:
            for filename, error in failures:
                f.write(f"{filename}\n{error}\n\n")

    print()
    print("DONE")
    print(f"Input : {source}")
    print(f"Output: {output}")
    print(f"Files : {len(source_files)}")
    print(f"Errors: {len(failures)}")
    print(f"Input bytes : {total_in:,}")
    print(f"Output bytes: {total_out:,}")
    if total_in:
        print(f"Output/input: {total_out / total_in:.2%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
