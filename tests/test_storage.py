import pickle
from pathlib import Path
import pytest

from app.storage import Checklist, Vault, links, transform_links
from library.indexing import load_metadata, make_chunks
from library.paths import resolve_source
from library.server import validate_url


def test_rename_preserves_links_aliases_and_backup(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Old", "# Old\n[[Old]]")
    vault.create("Related", "[[Old|Alias]] and [[Old#Section]] and [[Other]]")
    vault.rename("Old", "New")
    assert vault.read("Related") == "[[New|Alias]] and [[New#Section]] and [[Other]]"
    assert vault.read("New") == "# Old\n[[New]]"
    assert vault.backlinks("New") == ["New", "Related"]
    assert list((tmp_path / ".history").rglob("Old.md"))
    assert "Old" not in vault.names()


@pytest.mark.parametrize("name", ["../secret", "A/B", "A\\B", "CON", "NUL.txt", "..", "", "bad?"])
def test_invalid_note_names_cannot_escape_vault(tmp_path, name):
    with pytest.raises(ValueError):
        Vault(tmp_path).create(name)


def test_empty_note_and_trash(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Blank", "")
    assert vault.read("Blank") == ""
    vault.write("Blank", "body")
    trash = vault.trash("Blank")
    assert trash.read_text() == "body" and not vault.names()


def test_wiki_examples_inside_code_are_not_links():
    body = "`[[Example]]`\n```md\n[[Code]]\n```\n[[Real|label]]"
    assert links(body) == ["Real"]
    assert transform_links(body, lambda m: "LINK") == "`[[Example]]`\n```md\n[[Code]]\n```\nLINK"


def test_checklist_keeps_duplicate_titles_comments_and_uppercase_x(tmp_path):
    path = tmp_path / "Tasks.md"
    path.write_text("# Today\n\nNotes before list\n- [X] Same\n- [ ] Same\n\n## Later\n")
    book = Checklist(path)
    assert [t.done for t in book.items()] == [True, False]
    book.update(4, text="Renamed", done=True)
    assert path.read_text() == "# Today\n\nNotes before list\n- [X] Same\n- [x] Renamed\n\n## Later\n"
    book.update(3, delete=True)
    assert "## Later" in path.read_text() and len(book.items()) == 1
    assert list((tmp_path / ".history").glob("*.md"))


def test_chunking_keeps_short_paragraphs_and_bounds_long_text():
    text = "Short important fact.\n\n" + "verylongword" * 1000
    chunks = make_chunks(text)
    assert chunks[0].startswith("Short important fact.")
    assert all(0 < len(chunk) <= 1800 for chunk in chunks)
    assert make_chunks("Tiny fact") == ["Tiny fact"]


def test_all_library_path_forms(tmp_path):
    kb = tmp_path / "_KNOWLEDGE_BASE"
    index, extracted = kb / "index", kb / "extracted"
    index.mkdir(parents=True)
    extracted.mkdir()
    (index / "library.faiss").write_bytes(b"stub")
    (index / "metadata.pkl").write_bytes(b"stub")
    for value in (tmp_path, kb, index, extracted):
        paths = resolve_source(value)
        assert paths.index == index and paths.text == extracted and paths.indexed
    raw = tmp_path / "texts"
    raw.mkdir()
    assert resolve_source(raw).text == raw
    with pytest.raises(ValueError):
        resolve_source("")


def test_legacy_metadata_compatibility_and_reject_executable_pickle(tmp_path):
    metadata = [{"source": "book", "chunk": 0, "text": "body"}]
    (tmp_path / "metadata.pkl").write_bytes(pickle.dumps(metadata))
    assert load_metadata(tmp_path) == metadata
    (tmp_path / "metadata.pkl").write_bytes(pickle.dumps(Path("unsafe")))
    with pytest.raises(ValueError):
        load_metadata(tmp_path)


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://192.168.1.3/v1", "http://localhost:8081/api", "http://user:pass@localhost/v1"])
def test_only_local_model_endpoints(url):
    with pytest.raises(ValueError):
        validate_url(url)
