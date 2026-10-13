"""Resolve a library root, a knowledge base, extracted text, or an index directly."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LibraryPaths:
    source: Path
    kb: Path
    text: Path
    index: Path

    @property
    def indexed(self) -> bool:
        return ((self.index / "library.faiss").is_file() and
                any((self.index / name).is_file() for name in ("metadata.json", "metadata.pkl")))


def resolve_source(value: str | Path) -> LibraryPaths:
    if not str(value).strip():
        raise ValueError("Choose a source directory first.")
    source = Path(str(value).strip().strip('"')).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Directory not found: {source}")
    if (source / "library.faiss").is_file():
        kb = source.parent
        return LibraryPaths(source, kb, kb / "extracted", source)
    if source.name == "_KNOWLEDGE_BASE":
        kb = source
    elif source.name == "extracted" and source.parent.name == "_KNOWLEDGE_BASE":
        kb = source.parent
    else:
        kb = source / "_KNOWLEDGE_BASE"
    extracted = kb / "extracted"
    text = extracted if extracted.is_dir() else source
    return LibraryPaths(source, kb, text, kb / "index")
