"""Lossless link and permanent-identity regressions; all vaults are synthetic."""
import json
import uuid

import pytest

from app.note_links import extract_metadata, make_link, parse_links, remove_link, replace_link
from app.storage import Vault


def identity():
    return str(uuid.uuid4())


@pytest.mark.parametrize("words", ["a different label", "emoji 🐈 before 🦉 after", "[square] \\ slash", "first\nsecond", "**bold** and _italic_"])
def test_selected_words_survive_create_reopen_and_remove(words):
    target = identity()
    before = "🐈 prefix: " + words + "\nUntouched suffix\n"
    start, end = before.index(words), before.index(words) + len(words)
    linked = replace_link(before, start, end, target)
    result, = parse_links(linked)
    assert result["words"] == words
    assert result["target_id"] == target
    assert linked[result["start"]:result["end"]] == make_link(words, target)
    assert remove_link(linked, result["label_start"], result["label_end"]) == before


def test_literal_wiki_like_label_round_trips_when_encoded_as_an_id_link():
    words = "[[looks like a wiki link]]"
    linked = make_link(words, identity())
    result, = parse_links(linked)
    assert result["words"] == words
    assert remove_link(linked, result["label_start"], result["label_end"]) == words


def test_source_ranges_use_codepoints_and_preserve_multiline_excerpt():
    body = "🐈 start\n" + make_link("chosen 🦉\nsecond [line]", identity()) + " after\n"
    link, = parse_links(body)
    assert link["start"] == len("🐈 start\n")
    assert link["line"] == 2
    assert link["words"] == "chosen 🦉\nsecond [line]"
    assert link["words"] in link["excerpt"]


def test_link_change_has_one_destination_and_keeps_selected_phrase():
    old, new = identity(), identity()
    body = "Before " + make_link("arbitrary 🦉 label", old) + " after"
    occurrence, = parse_links(body)
    changed = replace_link(body, occurrence["label_start"] + 1, occurrence["label_end"] - 2, new)
    result, = parse_links(changed)
    assert result["target_id"] == new and result["words"] == "arbitrary 🦉 label"
    assert old not in changed
    assert changed.startswith("Before ") and changed.endswith(" after")


def test_legacy_link_can_be_changed_to_id_without_changing_alias():
    body = "One [[Destination#Section|alias words]] two"
    occurrence, = parse_links(body)
    target = identity()
    converted = replace_link(body, occurrence["label_start"], occurrence["label_end"], target)
    assert converted == f"One [alias words](nexus-note:{target}) two"
    assert remove_link(converted, 5, 9) == "One alias words two"


def test_partial_overlap_or_multiple_links_cannot_ambiguously_rewrite_text():
    body = "prefix " + make_link("words", identity()) + " tail " + make_link("more", identity())
    links = parse_links(body)
    with pytest.raises(ValueError, match="just the linked text"):
        replace_link(body, 0, links[0]["label_end"], identity())
    with pytest.raises(ValueError, match="one link at a time"):
        replace_link(body, links[0]["start"], len(body), identity())
    with pytest.raises(ValueError, match="selected text changed"):
        replace_link(body, 4, 4, identity())


def test_code_frontmatter_images_escaped_and_invalid_links():
    target = identity()
    link = make_link("valid", target)
    body = f"---\nproject: [[Not a link]]\n---\n`{link}`\n```\n[[Code]]\n```\n\\[[Escaped]]\n!{link}\n{link}\n[label](nexus-note:not-a-uuid)"
    parsed = parse_links(body)
    assert [(p["words"], p["target_id"]) for p in parsed] == [("valid", target), ("label", None)]


def test_permanent_id_survives_rename_restart_and_preserves_original_bytes(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Destination", "# Destination\n🐈 untouched\n")
    target = vault.note_id("Destination")
    content = "Start " + make_link("different words", target) + " end\n"
    vault.create("Source", content)
    source = vault.note_id("Source")
    vault.rename("Destination", "New destination")
    reopened = Vault(tmp_path)
    assert reopened.note_id("New destination") == target
    assert reopened.note_id("Source") == source
    assert reopened.read("Source") == content
    assert reopened.resolve_id(target) == "New destination"
    assert reopened.link_occurrences("Source")[0]["broken"] is False
    backlink, = reopened.backlinks_details("New destination")
    assert backlink["source_id"] == source
    assert backlink["words"] == "different words" and "different words" in backlink["excerpt"]


def test_deleted_destination_is_broken_and_new_same_title_does_not_steal_id(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Destination", "body")
    target = vault.note_id("Destination")
    content = make_link("preserve these words", target)
    vault.create("Source", content)
    trashed = vault.trash("Destination")
    assert trashed.read_text() == "body"
    assert vault.read("Source") == content
    assert vault.link_occurrences("Source")[0]["broken"] is True
    vault.create("Destination", "body")
    assert vault.note_id("Destination") != target
    assert vault.resolve_id(target) is None
    assert vault.link_occurrences("Source")[0]["words"] == "preserve these words"
    assert vault.link_occurrences("Source")[0]["broken"] is True


def test_external_file_rename_is_reconciled_without_rewriting_text(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Original", "Unique document 🦉\n")
    original_id = vault.note_id("Original")
    (tmp_path / "Original.md").rename(tmp_path / "External rename.md")
    assert Vault(tmp_path).note_id("External rename") == original_id
    assert (tmp_path / "External rename.md").read_text(encoding="utf-8") == "Unique document 🦉\n"


def test_wiki_links_remain_compatible_with_id_backlinks_and_graph_deduplication(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Target", "# Target\n")
    target = vault.note_id("Target")
    vault.create("Source", "[[Target|legacy alias]]\n" + make_link("first", target) + " " + make_link("second", target))
    assert len(vault.backlinks_details("Target")) == 3
    nodes, edges = vault.directed_graph()
    assert len(nodes) == 2 and len(edges) == 1
    assert edges[0]["target_id"] == target
    vault.rename("Target", "Renamed")
    assert "[[Renamed|legacy alias]]" in vault.read("Source")
    assert all(not link["broken"] for link in vault.link_occurrences("Source"))


def test_selector_fuzzy_context_recent_frequency_and_exclusion(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Architecture", "---\nproject: Nexus\ntags: [design, planning]\n---\n")
    vault.create("Unrelated", "Other")
    target = vault.note_id("Architecture")
    vault.create("Source", make_link("first", target) + make_link("second", target))
    vault.mark_opened("Architecture")
    assert vault.search_notes("archtecture")[0]["id"] == target
    assert vault.search_notes("planning")[0]["id"] == target
    assert vault.search_notes()[0]["id"] == target
    assert vault.search_notes()[0]["link_count"] == 2
    assert all(note["id"] != target for note in vault.search_notes(exclude=target))
    assert extract_metadata("---\nproject: sample\ntags:\n - one\n - two\n---\n") == {"project": "sample", "tags": ["one", "two"]}


def test_corrupt_identity_store_is_never_silently_replaced(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Keep", "Original content")
    vault.note_id("Keep")
    index = tmp_path / ".nexus" / "notes.json"
    index.write_text("corrupt-index", encoding="utf-8")
    with pytest.raises(ValueError, match="preserved"):
        Vault(tmp_path).note_metadata()
    assert index.read_text(encoding="utf-8") == "corrupt-index"
    assert vault.read("Keep") == "Original content"


def test_identity_index_only_contains_local_metadata(tmp_path):
    vault = Vault(tmp_path)
    vault.create("Content", "Private content stays in Markdown")
    vault.note_id("Content")
    data = json.loads((tmp_path / ".nexus" / "notes.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "Private content" not in json.dumps(data)
