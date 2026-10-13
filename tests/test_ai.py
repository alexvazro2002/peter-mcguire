import json
import numpy as np
import pytest

from library import indexing, retrieval
from library.indexing import build_index, load_metadata
from library.paths import resolve_source
from library.retrieval import Librarian


class TestEmbedder:
    """Deterministic test double; real FAISS files/search are used, no model downloads."""
    words = ["astronomy", "stars", "cooking", "pasta", "garden", "plants", "music", "hotel"]
    def encode(self, texts, **kwargs):
        rows = []
        for text in texts:
            vector = np.array([text.lower().count(word) for word in self.words], dtype="float32")
            if not vector.any():
                vector[-1] = 1
            rows.append(vector / np.linalg.norm(vector))
        return np.array(rows)
    def get_sentence_embedding_dimension(self):
        return len(self.words)


def test_real_faiss_build_search_and_rebuild_preserves_originals(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing, "embedding_model", lambda *a: TestEmbedder())
    monkeypatch.setattr(retrieval, "embedding_model", lambda *a: TestEmbedder())
    (tmp_path / "stars.txt").write_text("Astronomy studies stars and distant stars.")
    (tmp_path / "pasta.txt").write_text("Cooking pasta is an art.")
    paths, settings = resolve_source(tmp_path), {"embedding_model": "test-local"}
    build_index(paths, settings, log=lambda _: None)
    librarian = Librarian(paths, settings)
    assert librarian.search("astronomy stars")[0]["source"] == "stars.txt"
    assert librarian.search("cooking pasta")[0]["source"] == "pasta.txt"
    before = (tmp_path / "stars.txt").read_bytes()
    build_index(paths, settings, log=lambda _: None)
    assert (tmp_path / "stars.txt").read_bytes() == before
    assert list(paths.kb.glob("index.backup-*"))
    assert len(load_metadata(paths.index)) == 2


def test_failed_build_preserves_previous_index(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing, "embedding_model", lambda *a: TestEmbedder())
    (tmp_path / "text.txt").write_text("Astronomy stars")
    paths = resolve_source(tmp_path)
    build_index(paths, {"embedding_model": "test-local"}, log=lambda _: None)
    before = (paths.index / "library.faiss").read_bytes()
    def fail(*args):
        raise RuntimeError("failed to load model")
    monkeypatch.setattr(indexing, "embedding_model", fail)
    with pytest.raises(RuntimeError):
        build_index(paths, {}, log=lambda _: None)
    assert (paths.index / "library.faiss").read_bytes() == before


def test_source_diversity_and_deduplication():
    candidates = [{"source": "A", "chunk": i, "score": 1 - i / 100} for i in range(30)]
    candidates += [{"source": "B", "chunk": 0, "score": .7}, {"source": "B", "chunk": 0, "score": .6}]
    selected = retrieval.select_results(candidates)
    assert sum(i["source"] == "A" for i in selected) == 3
    assert sum(i["source"] == "B" for i in selected) == 1


def test_chat_payload_and_evidence(monkeypatch):
    import requests
    librarian = object.__new__(Librarian)
    librarian.settings = {"server_url": "http://127.0.0.1:8081/v1", "max_tokens": 500}
    librarian.search = lambda q: [{"source": "stars.txt", "chunk": 0, "score": .9, "text": "Stars emit light."}]
    captured = {}
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": "<think>private reasoning</think>Stars emit light [1]."}}]}
    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()
    monkeypatch.setattr(requests, "post", post)
    answer, sources = librarian.ask("What do stars do?")
    assert answer == "Stars emit light [1]."
    assert sources[0]["source"] == "stars.txt"
    assert captured["url"].endswith("/v1/chat/completions")
    assert "Stars emit light." in captured["json"]["messages"][1]["content"]
    assert "procrastination" not in captured["json"]["messages"][1]["content"]


def test_embedding_progress_batches_and_changed_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing, "embedding_model", lambda *a: TestEmbedder())
    for i in range(75):
        (tmp_path / f"text-{i:02}.txt").write_text("Astronomy stars")
    paths, events = resolve_source(tmp_path), []
    build_index(paths, {"embedding_model": "test"}, log=lambda _: None, progress=lambda **e: events.append(e))
    embedding = [e for e in events if e["phase"] == "embedding"]
    assert [e["completed"] for e in embedding] == [0, 32, 64, 75]
    assert all(e["total"] == 75 for e in embedding)
    assert events[-1]["phase"] == "indexed"
    assert not indexing.index_changed(paths)
    (tmp_path / "new.txt").write_text("A new document")
    assert indexing.index_changed(paths)


def test_hybrid_search_exact_name_source_filter_and_cache(tmp_path, monkeypatch):
    model = TestEmbedder()
    calls = []
    original_encode = model.encode
    def encode(texts, **kwargs):
        calls.append(list(texts))
        return original_encode(texts, **kwargs)
    model.encode = encode
    monkeypatch.setattr(indexing, "embedding_model", lambda *a: model)
    monkeypatch.setattr(retrieval, "embedding_model", lambda *a: model)
    for i in range(12):
        (tmp_path / f"other-{i}.txt").write_text("General hotel procedures")
    (tmp_path / "rare.txt").write_text("Hotel reference ZX-471 refers to the Zephyr Suite.")
    paths = resolve_source(tmp_path)
    build_index(paths, {"embedding_model": "test"}, log=lambda _: None)
    librarian = Librarian(paths, {})
    result = librarian.search("ZX-471 Zephyr Suite")
    assert result[0]["source"] == "rare.txt"
    assert "keyword" in result[0]["match"]
    before = len(calls)
    assert librarian.search("ZX-471 Zephyr Suite") == result
    assert len(calls) == before
    assert all(r["source"] == "other-1.txt" for r in librarian.search("hotel", "other-1.txt"))
    with pytest.raises(ValueError):
        librarian.search("hotel", "missing.txt")


def test_worker_progress_and_direct_search_protocol(tmp_path, monkeypatch, capsys):
    import io
    import sys
    from library import worker
    (tmp_path / "source.txt").write_text("Astronomy studies stars. Reference Zephyr.")
    monkeypatch.setattr(indexing, "embedding_model", lambda *a: TestEmbedder())
    monkeypatch.setattr(retrieval, "embedding_model", lambda *a: TestEmbedder())
    monkeypatch.setattr(worker, "load_config", lambda: {"library": {"embedding_model": "test"}})
    class Server:
        def __init__(self, *a): pass
        def ensure(self): pass
        def close(self): pass
    monkeypatch.setattr(worker, "ModelServer", Server)
    monkeypatch.setattr(sys, "argv", ["worker", "--source", str(tmp_path)])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"command":"search","question":"Zephyr"}\n{"command":"close"}\n'))
    assert worker.main() == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(e["type"] == "progress" and e["phase"] == "embedding" and e["completed"] == e["total"] for e in events)
    assert next(e for e in events if e["type"] == "ready")["sources"] == ["source.txt"]
    assert next(e for e in events if e["type"] == "results")["results"][0]["source"] == "source.txt"
    assert events[-1]["type"] == "closed"


def test_ui_library_worker_reuses_external_model_without_owning_it(tmp_path, monkeypatch, capsys):
    import io
    import sys
    from library import worker
    (tmp_path / "source.txt").write_text("Astronomy stars")
    monkeypatch.setattr(indexing, "embedding_model", lambda *a: TestEmbedder())
    monkeypatch.setattr(retrieval, "embedding_model", lambda *a: TestEmbedder())
    monkeypatch.setattr(worker, "load_config", lambda: {"library": {"embedding_model": "test", "server_url": "http://127.0.0.1:8081/v1"}})
    calls = []
    class Server:
        def __init__(self, *a): pass
        def healthy(self):
            calls.append("healthy")
            return True
        def ensure(self):
            pytest.fail("UI worker must not start its own model server")
        def close(self): calls.append("close-unowned")
    monkeypatch.setattr(worker, "ModelServer", Server)
    monkeypatch.setattr(sys, "argv", ["worker", "--source", str(tmp_path), "--external-model"])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"command":"close"}\n'))
    assert worker.main() == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(e["type"] == "ready" for e in events)
    assert calls == ["healthy", "close-unowned"]
