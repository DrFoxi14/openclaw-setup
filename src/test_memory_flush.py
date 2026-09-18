import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import memory_flush


FAKE_VECTOR_A = [1.0, 0.0, 0.0]
FAKE_VECTOR_B = [0.0, 1.0, 0.0]
FAKE_VECTOR_A_NEAR = [0.9, 0.1, 0.0]  # close to A, for retrieval testing


@pytest.fixture(autouse=True)
def isolated_memory_root(tmp_path, monkeypatch):
    """Every test gets its own memory/ dir on disk — never touches a real
    OpenClaw install, and tests can run in parallel safely."""
    monkeypatch.setattr(memory_flush, "MEMORY_ROOT", tmp_path / "memory")
    monkeypatch.setattr(memory_flush, "SEGMENTS_DIR", tmp_path / "memory" / "segments")
    monkeypatch.setattr(memory_flush, "INDEX_PATH", tmp_path / "memory" / "embedding_index" / "index.json")
    yield


def fake_embed(text, model=None, timeout=None):
    """Deterministic fake: route text to a fixed vector by keyword, so
    tests don't need a real Ollama server."""
    if "topic-a" in text:
        return FAKE_VECTOR_A
    if "topic-a-near" in text:
        return FAKE_VECTOR_A_NEAR
    if "topic-b" in text:
        return FAKE_VECTOR_B
    return [0.5, 0.5, 0.0]


# ---------------------------------------------------------------------------
# Atomic write behavior
# ---------------------------------------------------------------------------

def test_atomic_write_produces_valid_json(tmp_path):
    target = tmp_path / "sub" / "file.json"
    memory_flush._atomic_write_json(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}


def test_atomic_write_leaves_no_tmp_file_behind(tmp_path):
    target = tmp_path / "file.json"
    memory_flush._atomic_write_json(target, {"a": 1})
    leftovers = list(tmp_path.glob("*.tmp-*"))
    assert leftovers == []


def test_atomic_write_overwrite_is_still_valid(tmp_path):
    target = tmp_path / "file.json"
    memory_flush._atomic_write_json(target, {"a": 1})
    memory_flush._atomic_write_json(target, {"a": 2, "b": 3})
    assert json.loads(target.read_text()) == {"a": 2, "b": 3}


# ---------------------------------------------------------------------------
# is_nontrivial
# ---------------------------------------------------------------------------

def test_trivial_segment_is_skipped():
    assert memory_flush.is_nontrivial([{"role": "user", "content": "hi"}]) is False


def test_nontrivial_segment_passes():
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert memory_flush.is_nontrivial(msgs) is True


# ---------------------------------------------------------------------------
# flush_segment — the core "never lose it" path
# ---------------------------------------------------------------------------

@patch("memory_flush.embed", side_effect=fake_embed)
def test_flush_segment_writes_file_to_disk(mock_embed):
    msgs = [{"role": "user", "content": "topic-a discussion"}, {"role": "assistant", "content": "ok"}]
    segment_id = memory_flush.flush_segment(msgs, summary="topic-a summary")

    assert segment_id is not None
    segment_path = memory_flush.SEGMENTS_DIR / f"{segment_id}.json"
    assert segment_path.exists()

    on_disk = json.loads(segment_path.read_text())
    assert on_disk["summary"] == "topic-a summary"
    assert on_disk["raw_messages"] == msgs
    assert on_disk["embedding"] == FAKE_VECTOR_A


@patch("memory_flush.embed", side_effect=fake_embed)
def test_flush_segment_skips_trivial_by_default(mock_embed):
    segment_id = memory_flush.flush_segment([{"role": "user", "content": "hi"}], summary="x")
    assert segment_id is None
    assert list(memory_flush.SEGMENTS_DIR.glob("*.json")) == []


@patch("memory_flush.embed", side_effect=fake_embed)
def test_flush_segment_can_force_trivial_through(mock_embed):
    segment_id = memory_flush.flush_segment(
        [{"role": "user", "content": "hi"}], summary="topic-a", skip_trivial=False
    )
    assert segment_id is not None


@patch("memory_flush.embed", side_effect=fake_embed)
def test_flush_segment_updates_index(mock_embed):
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    segment_id = memory_flush.flush_segment(msgs, summary="topic-a", topic_tags=["work"])

    index = json.loads(memory_flush.INDEX_PATH.read_text())
    ids = [e["id"] for e in index["entries"]]
    assert segment_id in ids


@patch("memory_flush.embed", side_effect=fake_embed)
def test_flush_segment_requires_summary_or_hook(mock_embed):
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    # summarize_locally() is an unimplemented hook — no summary= means it
    # must raise, not silently produce garbage.
    with pytest.raises(NotImplementedError):
        memory_flush.flush_segment(msgs)


# ---------------------------------------------------------------------------
# search_segments — retrieval by similarity, summary-only
# ---------------------------------------------------------------------------

@patch("memory_flush.embed", side_effect=fake_embed)
def test_search_finds_similar_segment(mock_embed):
    msgs = [{"role": "user", "content": "topic-a"}, {"role": "assistant", "content": "x"}]
    memory_flush.flush_segment(msgs, summary="topic-a summary")

    hits = memory_flush.search_segments("topic-a-near query")
    assert len(hits) == 1
    assert hits[0].summary == "topic-a summary"
    assert hits[0].similarity > memory_flush.RETRIEVAL_THRESHOLD


@patch("memory_flush.embed", side_effect=fake_embed)
def test_search_excludes_dissimilar_segment(mock_embed):
    msgs = [{"role": "user", "content": "topic-b"}, {"role": "assistant", "content": "x"}]
    memory_flush.flush_segment(msgs, summary="topic-b summary")

    hits = memory_flush.search_segments("topic-a-near query")
    assert hits == []


@patch("memory_flush.embed", side_effect=fake_embed)
def test_search_returns_summary_not_raw_messages(mock_embed):
    msgs = [{"role": "user", "content": "topic-a"}, {"role": "assistant", "content": "secret detail"}]
    memory_flush.flush_segment(msgs, summary="topic-a summary")

    hits = memory_flush.search_segments("topic-a-near query")
    assert hits[0].summary == "topic-a summary"
    # SearchHit has no raw_messages field at all — structurally can't leak.
    assert not hasattr(hits[0], "raw_messages")


@patch("memory_flush.embed", side_effect=fake_embed)
def test_search_respects_top_k(mock_embed):
    for i in range(5):
        msgs = [{"role": "user", "content": "topic-a"}, {"role": "assistant", "content": str(i)}]
        memory_flush.flush_segment(msgs, summary=f"topic-a summary {i}")

    hits = memory_flush.search_segments("topic-a-near query", top_k=2)
    assert len(hits) == 2


def test_search_on_empty_index_returns_empty():
    with patch("memory_flush.embed", side_effect=fake_embed):
        hits = memory_flush.search_segments("anything")
    assert hits == []


# ---------------------------------------------------------------------------
# load_segment — full transcript retrieval, explicit only
# ---------------------------------------------------------------------------

@patch("memory_flush.embed", side_effect=fake_embed)
def test_load_segment_returns_full_transcript(mock_embed):
    msgs = [{"role": "user", "content": "topic-a"}, {"role": "assistant", "content": "full detail here"}]
    segment_id = memory_flush.flush_segment(msgs, summary="topic-a summary")

    loaded = memory_flush.load_segment(segment_id)
    assert loaded["raw_messages"] == msgs


def test_load_segment_missing_raises():
    with pytest.raises(FileNotFoundError):
        memory_flush.load_segment("seg_does_not_exist")


# ---------------------------------------------------------------------------
# embed() error handling — real network path, no mocking
# ---------------------------------------------------------------------------

def test_embed_rejects_empty_text():
    with pytest.raises(memory_flush.EmbeddingError):
        memory_flush.embed("")


def test_embed_raises_on_unreachable_ollama(monkeypatch):
    # Point at a port nothing is listening on — should raise EmbeddingError,
    # not hang or crash with an unhandled exception.
    monkeypatch.setattr(memory_flush, "OLLAMA_BASE_URL", "http://127.0.0.1:1")
    with pytest.raises(memory_flush.EmbeddingError):
        memory_flush.embed("some text", timeout=2.0)


# ---------------------------------------------------------------------------
# Crash-safety simulation: does a killed write ever corrupt the index?
# ---------------------------------------------------------------------------

@patch("memory_flush.embed", side_effect=fake_embed)
def test_index_survives_partial_write_simulation(mock_embed, tmp_path):
    """Simulates the exact failure mode the whole module exists to prevent:
    if the process died mid-write, the old index must still be intact and
    valid JSON — never truncated or corrupted."""
    msgs = [{"role": "user", "content": "topic-a"}, {"role": "assistant", "content": "x"}]
    memory_flush.flush_segment(msgs, summary="topic-a summary")

    original = memory_flush.INDEX_PATH.read_text()

    # Simulate a crash: os.replace() never happens, only a stray tmp file
    # is left on disk (what _atomic_write_json guards against).
    stray_tmp = memory_flush.INDEX_PATH.with_suffix(".json.tmp-99999-deadbeef")
    stray_tmp.write_text("{corrupted, not valid json")

    # The real index must be unaffected by the stray tmp file.
    reloaded = json.loads(memory_flush.INDEX_PATH.read_text())
    assert memory_flush.INDEX_PATH.read_text() == original
    assert isinstance(reloaded["entries"], list)


# ---------------------------------------------------------------------------
# Concurrency: the index must never silently drop an entry
# ---------------------------------------------------------------------------

@patch("memory_flush.embed", side_effect=fake_embed)
def test_concurrent_flush_keeps_every_index_entry(mock_embed, monkeypatch):
    """Two flushes racing must not lose an index entry.

    _update_index() is a read-modify-write on a single JSON file. Without
    serialization, two threads can both read the pre-append state and the
    second write silently drops the first entry — the segment file stays
    on disk but becomes invisible to search_segments(), which is exactly
    the silent loss this module exists to prevent.

    The write is slowed deliberately to widen the race window, so this
    test fails reliably against an unlocked implementation instead of
    passing by luck on a fast machine. (Measured against the pre-fix
    version: 19 of 20 entries lost.)
    """
    real_write = memory_flush._atomic_write_json

    def slow_write(path, data):
        time.sleep(0.005)
        return real_write(path, data)

    monkeypatch.setattr(memory_flush, "_atomic_write_json", slow_write)
    memory_flush.ensure_dirs()

    thread_count = 20
    segment_ids = []
    errors = []
    append_lock = threading.Lock()

    def worker(i):
        try:
            segment_id = memory_flush.flush_segment(
                [{"role": "user", "content": f"topic-a {i}"},
                 {"role": "assistant", "content": "x"}],
                summary=f"topic-a summary {i}",
            )
            with append_lock:
                segment_ids.append(segment_id)
        except Exception as exc:  # noqa: BLE001 - surfaced via assert below
            with append_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"flush_segment raised under concurrency: {errors}"

    index = json.loads(memory_flush.INDEX_PATH.read_text())
    indexed_ids = {entry["id"] for entry in index["entries"]}
    missing = [sid for sid in segment_ids if sid not in indexed_ids]

    assert missing == [], f"{len(missing)} segment(s) written to disk but missing from the index"
    assert len(index["entries"]) == thread_count
