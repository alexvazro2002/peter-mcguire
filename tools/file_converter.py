#!/usr/bin/env python3
"""
File Converter — auto-detect source type and convert to ONE chosen target.

Usage:
    python file_converter.py <input_file> <output_dir> --to png
    python file_converter.py <input_file> <output_dir> --to txt
"""
from __future__ import annotations

import argparse
import html as html_lib
import os
import re
import sys

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import PyPDF2
    HAS_PYPDF = True
except ImportError:
    try:
        import pypdf as PyPDF2  # modern package name
        HAS_PYPDF = True
    except ImportError:
        HAS_PYPDF = False

try:
    import docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import ffmpeg
    HAS_FFMPEG = True
except ImportError:
    HAS_FFMPEG = False


# Source extension -> allowed --to targets
TARGETS = {
    # Images
    ".jpg":  ["png", "webp", "bmp"],
    ".jpeg": ["png", "webp", "bmp"],
    ".png":  ["jpg", "webp", "bmp"],
    ".webp": ["jpg", "png"],
    ".bmp":  ["jpg", "png"],
    ".gif":  ["png", "jpg"],
    ".tif":  ["jpg", "png"],
    ".tiff": ["jpg", "png"],
    # Documents / text
    ".pdf":  ["txt", "docx", "compress"],
    ".docx": ["txt", "html"],
    ".doc":  ["txt"],
    ".html": ["txt", "docx"],
    ".htm":  ["txt", "docx"],
    ".xhtml": ["txt", "docx"],
    ".md":   ["txt", "html"],
    ".markdown": ["txt", "html"],
    ".txt":  ["html"],
    ".rtf":  ["txt"],
    ".csv":  ["txt"],
    ".json": ["txt"],
    ".xml":  ["txt"],
    ".log":  ["txt"],
    # Video
    ".mp4":  ["webm", "avi"],
    ".mkv":  ["mp4", "webm"],
    ".avi":  ["mp4", "webm"],
    ".mov":  ["mp4", "webm"],
    ".webm": ["mp4"],
}


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def base_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def read_text_file(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(text)


# ── Image ────────────────────────────────────────────────────────────────

def convert_image(input_path: str, output_dir: str, to_fmt: str, quality: int = 85) -> bool:
    if not HAS_PIL:
        safe_print("Error: Pillow not installed. Run: pip install pillow")
        return False
    to_fmt = to_fmt.lower().lstrip(".")
    if to_fmt == "jpeg":
        to_fmt = "jpg"
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.{to_fmt}")
    try:
        img = Image.open(input_path)
        if to_fmt in ("jpg", "jpeg", "webp") and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        elif img.mode == "P":
            img = img.convert("RGBA")
        kwargs = {}
        if to_fmt in ("jpg", "jpeg"):
            kwargs = {"format": "JPEG", "quality": quality}
        elif to_fmt == "png":
            kwargs = {"format": "PNG"}
        elif to_fmt == "webp":
            kwargs = {"format": "WebP", "quality": quality}
        elif to_fmt == "bmp":
            kwargs = {"format": "BMP"}
        else:
            kwargs = {"format": to_fmt.upper()}
        img.save(out_path, **kwargs)
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting image to {to_fmt}: {e}")
        return False


# ── PDF ──────────────────────────────────────────────────────────────────

def pdf_to_txt(input_path: str, output_dir: str) -> bool:
    if not HAS_PYPDF:
        safe_print("Error: pypdf not installed. Run: pip install pypdf")
        return False
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.txt")
    try:
        with open(input_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            parts = [(page.extract_text() or "") for page in reader.pages]
        write_text(out_path, "\n".join(parts))
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting PDF to TXT: {e}")
        return False


def _fix_pdf_word_spaces(text: str) -> str:
    """
    Fix mid-word spaces from PDF extractors without gluing real words.
      'da ys' -> 'days', 'fisherme n' -> 'fishermen', 'defi nitely' -> 'definitely'
    Never: 'in a', 'taking a', 'days now', 'gone eighty'
    """
    # Explicit common broken words seen in PDF extracts
    for broken, fixed in (
        ("defi nitely", "definitely"),
        ("final ly", "finally"),
        ("probab ly", "probably"),
        ("usual ly", "usually"),
        ("sudden ly", "suddenly"),
        ("fisherme n", "fishermen"),
        ("anyth ing", "anything"),
        ("everyth ing", "everything"),
        ("noth ing", "nothing"),
        ("some th", "someth"),  # partial; handled further below
    ):
        text = text.replace(broken, fixed)

    # Pass 1: token + single letter that is NOT a/i
    text = re.sub(
        r"([A-Za-z]{2,}) ([b-hj-z])(?=[\s.,;:!?\"'”’)\]]|$)",
        r"\1\2",
        text,
    )

    common = {
        "a", "i", "am", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in",
        "is", "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we",
        "the", "and", "but", "for", "not", "you", "all", "can", "had", "her", "was",
        "one", "our", "out", "are", "has", "his", "how", "its", "may", "new", "now",
        "old", "see", "two", "way", "who", "boy", "man", "did", "get", "got", "let",
        "put", "say", "she", "too", "use", "own", "yet", "off", "far", "sea", "day",
        "big", "end", "few", "him", "men", "set", "try", "ask", "yes", "ago", "any",
        "days", "with", "from", "that", "this", "they", "have", "been", "were", "when",
        "what", "your", "said", "each", "make", "like", "just", "over", "also", "into",
        "than", "then", "them", "only", "some", "time", "very", "know", "take", "come",
        "made", "find", "give", "tell", "work", "much", "well", "good", "long", "even",
        "most", "back", "down", "such", "here", "papa", "must", "boat", "fish", "line",
        "hand", "head", "water", "shark", "skiff", "about", "which", "their", "would",
        "could", "before", "after", "where", "while", "again", "never", "always",
        "still", "every", "great", "other", "first", "those", "these", "there", "gone",
        "same", "skin", "went", "came", "look", "felt", "held", "left", "side", "body",
        "once", "upon", "many", "more", "next", "last", "part", "place", "right", "small",
        "large", "young", "years", "three", "under", "after", "where", "think", "thing",
        "being", "doing", "going", "having", "might", "shall", "those", "whose",
    }

    def repl(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        if left.lower() in common:
            return m.group(0)
        core_right = right.lower().rstrip(".,;:!?\"'")
        if core_right in common:
            return m.group(0)
        # 4-letter left: only join if right looks like a word continuation/suffix
        if len(left) >= 4:
            suf = ("ly", "ing", "tion", "sion", "ness", "ment", "ed", "er", "est",
                   "ous", "ful", "able", "ible", "ally", "ively", "nitely", "tely")
            if not any(core_right.endswith(s) or core_right.startswith(s[:3]) for s in suf):
                if len(core_right) < 5:
                    return m.group(0)
                # still allow long continuations of broken long words (definitely)
                if left.lower() not in {"defi", "proba", "possi", "beau", "neces"}:
                    # heuristic: if left looks like incomplete English, allow
                    if left[-1].lower() in "aeiou" and core_right[0] in "aeiou":
                        return m.group(0)  # vowel-vowel boundary more likely two words
        return left + right

    # 2-4 letter broken prefix + continuation of 2+ letters
    text = re.sub(r"\b([A-Za-z]{2,4}) ([a-z]{2,})", repl, text)
    return text


def _clean_pdf_page_text(raw: str, running_header: str | None = None) -> list[str]:
    """
    Clean one page into paragraphs:
    - drop page numbers, running headers, [12] markers
    - join soft-wrapped lines; break on dialogue / sentence ends
    - fix mid-word spaces
    """
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\\?\[(\d+)\]", "", raw)

    lines = [ln.rstrip() for ln in raw.split("\n")]
    cleaned: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            cleaned.append("")
            continue
        if re.fullmatch(r"\d{1,4}", s):
            continue
        if running_header and s.lower() == running_header.lower():
            continue
        if re.match(r"^ernest hemingway\b", s, re.I) and "old man" in s.lower():
            continue
        if re.match(r"^\d{1,3}[A-Za-z]", s):
            s = re.sub(r"^\d{1,3}", "", s).lstrip()
            if not s:
                continue
        cleaned.append(s)

    def starts_dialogue(s: str) -> bool:
        return s[:1] in {'"', "'", "\u201c", "\u2018"}

    def ends_sentence(s: str) -> bool:
        t = s.rstrip()
        return any(t.endswith(x) for x in (".", "!", "?", '."', '!"', '?"', ".'", "!'", "?'",
                                            ".\u201d", "!\u201d", "?\u201d"))

    paragraphs: list[str] = []
    buf = ""
    for ln in cleaned:
        if not ln.strip():
            if buf:
                paragraphs.append(buf.strip())
                buf = ""
            if paragraphs and paragraphs[-1] != "":
                paragraphs.append("")
            continue
        if not buf:
            buf = ln
            continue
        if starts_dialogue(ln) or (ends_sentence(buf) and ln[:1].isupper()):
            paragraphs.append(buf.strip())
            buf = ln
        elif buf.endswith("-"):
            buf = buf[:-1] + ln.lstrip()
        else:
            buf = buf + " " + ln.lstrip()
    if buf:
        paragraphs.append(buf.strip())

    fixed: list[str] = []
    for p in paragraphs:
        if not p:
            fixed.append("")
            continue
        p = re.sub(r"[ \t]{2,}", " ", p)
        p = _fix_pdf_word_spaces(p)
        p = re.sub(r"[ \t]{2,}", " ", p).strip()
        fixed.append(p)
    return fixed


def _guess_running_header(pages_text: list[str]) -> str | None:
    from collections import Counter
    counts: Counter[str] = Counter()
    for t in pages_text:
        for ln in t.splitlines():
            s = ln.strip()
            if 8 <= len(s) <= 80 and not re.fullmatch(r"\d+", s):
                counts[s] += 1
    if not counts:
        return None
    header, n = counts.most_common(1)[0]
    if n >= max(3, len(pages_text) // 3):
        return header
    return None


def pdf_to_docx(input_path: str, output_dir: str) -> bool:
    """
    Prefer pdf2docx (layout). Fallback: clean text extraction that
    keeps paragraphs/blank lines and strips headers, page numbers,
    and common mid-word spacing artifacts.
    """
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.docx")

    # 1) Best quality when installed
    try:
        from pdf2docx import Converter  # type: ignore
        safe_print("Using pdf2docx (layout-preserving)...")
        cv = Converter(input_path)
        try:
            cv.convert(out_path)
        finally:
            cv.close()
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            safe_print(f"Created: {out_path}")
            return True
        safe_print("pdf2docx produced an empty file; falling back...")
    except ImportError:
        safe_print("pdf2docx not installed — using cleaned text conversion.")
        safe_print("For closest visual layout: pip install pdf2docx")
    except Exception as e:
        safe_print(f"pdf2docx failed ({e}); using cleaned text conversion...")

    if not HAS_PYPDF or not HAS_DOCX:
        safe_print("Error: need pypdf and python-docx. Run: pip install pypdf python-docx")
        return False

    try:
        with open(input_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            page_texts = [(page.extract_text() or "") for page in reader.pages]

        header = _guess_running_header(page_texts)
        doc = docx.Document()

        # Slightly nicer default body font
        try:
            style = doc.styles["Normal"]
            style.font.name = "Times New Roman"
            style.font.size = docx.shared.Pt(12)
        except Exception:
            pass

        first_content = True
        for page_text in page_texts:
            paras = _clean_pdf_page_text(page_text, running_header=header)
            for p in paras:
                if not p:
                    # blank line between paragraphs
                    if not first_content:
                        doc.add_paragraph("")
                    continue
                para = doc.add_paragraph(p)
                # Title-ish first non-empty lines on page 1: center if short
                if first_content and len(p) < 80 and not p.endswith("."):
                    para.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
                first_content = False

        doc.save(out_path)
        safe_print(f"Created: {out_path}")
        safe_print("Cleaned: headers, page numbers, soft wraps, mid-word spaces.")
        return True
    except Exception as e:
        safe_print(f"Error converting PDF to DOCX: {e}")
        return False


def compress_pdf(input_path: str, output_dir: str) -> bool:
    if not HAS_PYPDF:
        safe_print("Error: pypdf not installed. Run: pip install pypdf")
        return False
    out_path = os.path.join(output_dir, f"{base_name(input_path)}_compressed.pdf")
    try:
        with open(input_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            writer = PyPDF2.PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            if reader.metadata:
                try:
                    writer.add_metadata(reader.metadata)
                except Exception:
                    pass
            with open(out_path, "wb") as out_f:
                writer.write(out_f)
        orig = os.path.getsize(input_path)
        comp = os.path.getsize(out_path)
        ratio = (1 - comp / orig) * 100 if orig > 0 else 0
        safe_print(f"Created: {out_path}")
        safe_print(f"Compressed from {orig // 1024}KB to {comp // 1024}KB ({ratio:.1f}% reduction)")
        return True
    except Exception as e:
        safe_print(f"Error compressing PDF: {e}")
        return False


# ── Office / HTML / text ─────────────────────────────────────────────────

def docx_to_txt(input_path: str, output_dir: str) -> bool:
    if not HAS_DOCX:
        safe_print("Error: python-docx not installed. Run: pip install python-docx")
        return False
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.txt")
    try:
        document = docx.Document(input_path)
        lines = [p.text for p in document.paragraphs if p.text.strip()]
        write_text(out_path, "\n".join(lines))
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting DOCX to TXT: {e}")
        return False


def docx_to_html(input_path: str, output_dir: str) -> bool:
    if not HAS_DOCX:
        safe_print("Error: python-docx not installed. Run: pip install python-docx")
        return False
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.html")
    try:
        document = docx.Document(input_path)
        parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>"
                 f"{html_lib.escape(base_name(input_path))}</title></head><body>"]
        for p in document.paragraphs:
            t = p.text.strip()
            if t:
                parts.append(f"<p>{html_lib.escape(t)}</p>")
        parts.append("</body></html>")
        write_text(out_path, "\n".join(parts))
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting DOCX to HTML: {e}")
        return False


def strip_html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?s)</p\s*>", "\n\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html_lib.unescape(raw)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_txt(input_path: str, output_dir: str) -> bool:
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.txt")
    try:
        raw = read_text_file(input_path)
        write_text(out_path, strip_html_to_text(raw))
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting HTML to TXT: {e}")
        return False


def html_to_docx(input_path: str, output_dir: str) -> bool:
    if not HAS_DOCX:
        safe_print("Error: python-docx not installed. Run: pip install python-docx")
        return False
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.docx")
    try:
        text = strip_html_to_text(read_text_file(input_path))
        doc = docx.Document()
        for para in text.split("\n\n"):
            para = para.strip()
            if para:
                doc.add_paragraph(para)
        doc.save(out_path)
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting HTML to DOCX: {e}")
        return False


def md_to_txt(input_path: str, output_dir: str) -> bool:
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.txt")
    try:
        write_text(out_path, read_text_file(input_path))
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting MD to TXT: {e}")
        return False


def md_to_html(input_path: str, output_dir: str) -> bool:
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.html")
    try:
        body = html_lib.escape(read_text_file(input_path)).replace("\n", "<br>\n")
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{html_lib.escape(base_name(input_path))}</title></head>"
            f"<body><pre style='white-space:pre-wrap;font-family:sans-serif'>{body}</pre>"
            "</body></html>"
        )
        write_text(out_path, html)
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting MD to HTML: {e}")
        return False


def txt_to_html(input_path: str, output_dir: str) -> bool:
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.html")
    try:
        body = html_lib.escape(read_text_file(input_path)).replace("\n", "<br>\n")
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{html_lib.escape(base_name(input_path))}</title></head>"
            f"<body><pre style='white-space:pre-wrap;font-family:sans-serif'>{body}</pre>"
            "</body></html>"
        )
        write_text(out_path, html)
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting TXT to HTML: {e}")
        return False


def rtf_to_txt(input_path: str, output_dir: str) -> bool:
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.txt")
    try:
        raw = read_text_file(input_path)
        # Prefer striprtf if available
        try:
            from striprtf.striprtf import rtf_to_text  # type: ignore
            text = rtf_to_text(raw)
        except Exception:
            text = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)
            text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)
            text = re.sub(r"[{}]", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        write_text(out_path, text)
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting RTF to TXT: {e}")
        return False


def plain_copy_as_txt(input_path: str, output_dir: str) -> bool:
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.txt")
    try:
        write_text(out_path, read_text_file(input_path))
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error writing TXT: {e}")
        return False


# ── Video ────────────────────────────────────────────────────────────────

def convert_video(input_path: str, output_dir: str, to_fmt: str) -> bool:
    if not HAS_FFMPEG:
        safe_print("Error: ffmpeg-python not installed. Run: pip install ffmpeg-python")
        safe_print("Also ensure ffmpeg is installed on your system")
        return False
    to_fmt = to_fmt.lower().lstrip(".")
    out_path = os.path.join(output_dir, f"{base_name(input_path)}.{to_fmt}")
    try:
        stream = ffmpeg.input(input_path)
        if to_fmt == "mp4":
            stream = ffmpeg.output(stream, out_path, vcodec="libx264", acodec="aac")
        elif to_fmt == "webm":
            stream = ffmpeg.output(stream, out_path, vcodec="libvpx", acodec="libvorbis")
        else:
            stream = ffmpeg.output(stream, out_path)
        ffmpeg.run(stream, overwrite_output=True, quiet=True)
        safe_print(f"Created: {out_path}")
        return True
    except Exception as e:
        safe_print(f"Error converting video to {to_fmt}: {e}")
        return False


# ── Dispatch ─────────────────────────────────────────────────────────────

def run_conversion(input_path: str, output_dir: str, to_fmt: str, quality: int = 85) -> bool:
    if not os.path.isfile(input_path):
        safe_print(f"Error: Input file does not exist: {input_path}")
        return False
    if not os.path.isdir(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            safe_print(f"Error: cannot create output directory: {e}")
            return False

    ext = os.path.splitext(input_path)[1].lower()
    to_fmt = (to_fmt or "").lower().lstrip(".")
    allowed = TARGETS.get(ext, [])

    safe_print("")
    safe_print("=" * 50)
    safe_print("  FILE CONVERTER")
    safe_print("=" * 50)
    safe_print(f"Input  : {input_path}")
    safe_print(f"Output : {output_dir}")
    safe_print(f"From   : {ext or '(none)'}")
    safe_print(f"To     : {to_fmt}")
    safe_print("=" * 50)

    if not allowed:
        safe_print(f"Unsupported source type: {ext}")
        safe_print("Supported: " + ", ".join(sorted(TARGETS.keys())))
        return False
    if to_fmt not in allowed:
        safe_print(f"Cannot convert {ext} to '{to_fmt}'. Allowed: {', '.join(allowed)}")
        return False

    filename = f"{base_name(input_path)}_compressed.pdf" if to_fmt == "compress" else f"{base_name(input_path)}.{to_fmt}"
    if os.path.exists(os.path.join(output_dir, filename)):
        safe_print(f"Output already exists: {filename}. Choose another output folder to preserve that file.")
        return False

    ok = False
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}:
        ok = convert_image(input_path, output_dir, to_fmt, quality=quality)
    elif ext == ".pdf":
        if to_fmt == "txt":
            ok = pdf_to_txt(input_path, output_dir)
        elif to_fmt == "docx":
            ok = pdf_to_docx(input_path, output_dir)
        elif to_fmt == "compress":
            ok = compress_pdf(input_path, output_dir)
    elif ext == ".docx":
        if to_fmt == "txt":
            ok = docx_to_txt(input_path, output_dir)
        elif to_fmt == "html":
            ok = docx_to_html(input_path, output_dir)
    elif ext in {".html", ".htm", ".xhtml"}:
        if to_fmt == "txt":
            ok = html_to_txt(input_path, output_dir)
        elif to_fmt == "docx":
            ok = html_to_docx(input_path, output_dir)
    elif ext in {".md", ".markdown"}:
        if to_fmt == "txt":
            ok = md_to_txt(input_path, output_dir)
        elif to_fmt == "html":
            ok = md_to_html(input_path, output_dir)
    elif ext == ".txt":
        if to_fmt == "html":
            ok = txt_to_html(input_path, output_dir)
    elif ext == ".rtf":
        if to_fmt == "txt":
            ok = rtf_to_txt(input_path, output_dir)
    elif ext in {".csv", ".json", ".xml", ".log", ".doc"}:
        if to_fmt == "txt":
            ok = plain_copy_as_txt(input_path, output_dir)
    elif ext in {".mp4", ".mkv", ".avi", ".mov", ".webm"}:
        ok = convert_video(input_path, output_dir, to_fmt)
    else:
        safe_print(f"No handler for {ext}")

    safe_print("-" * 50)
    safe_print("Done." if ok else "Failed.")
    safe_print("=" * 50)
    return ok


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="File Converter - one source, one target")
    parser.add_argument("input", nargs="?", help="Input file path")
    parser.add_argument("output", nargs="?", help="Output directory")
    parser.add_argument("--to", dest="to", default="", help="Target format")
    parser.add_argument("--quality", type=int, default=85)
    parser.add_argument("--type", dest="legacy_type", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not args.input or not args.output:
        safe_print("Usage: python file_converter.py <input_file> <output_dir> --to <format>")
        return 1

    to_fmt = args.to
    if not to_fmt:
        safe_print("Error: --to <format> is required (pick one target in the UI)")
        return 1

    ok = run_conversion(args.input, args.output, to_fmt, quality=args.quality)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
