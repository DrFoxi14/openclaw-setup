#!/usr/bin/env python3
"""
memory_flush.py — incremental disk-flush layer for OpenClaw's hot context.

Implements section 2 ("Incremental flush") of context-memory-architecture.md.
This is the piece that failure-simulation-openclaw-ornith.md identifies as
the single strongest defense against the "bad path" compaction scenario:
if a topic segment is written to disk the moment it ends, a later panic-
compression event (overflow, crash, forced /new) can never lose it — the
only thing at risk is whatever is still in the *current*, unflushed segment.

Design choices, and why:
- stdlib only, no vector-db dependency. At personal-use scale (hundreds to
  low thousands of segments) brute-force cosine similarity over a flat
  JSON index is fast enough and has zero moving parts to misconfigure.
- Embeddings come from a local Ollama call (nomic-embed-text, per your
  README) via urllib — no extra Python package, consistent with your
  "zero external API cost" decision.
- Segment writes are atomic: write to a temp file in the same directory,
  then os.replace(). A crash mid-write leaves the old state intact rather
  than a half-written JSON file — this is the actual mechanism that makes
  "flush before compaction" trustworthy instead of just aspirational.
- summarize_locally() is left as an explicit hook, not guessed at. Wire it
  to an Ollama chat call against ornith-1.5:9b (or whatever small local
  model you settle on) once you're back in the real OpenClaw process —
  I'm not going to fabricate that call's shape without seeing your actual
  Ollama setup.

Usage (see test_memory_flush.py for worked examples):

    from memory_flush import flush_segment, search_segments

    segment_id = flush_segment(
        raw_messages=[{"role": "user", "content": "..."}, ...],
        summary="short summary of what this segment covered",
        topic_tags=["freelance", "python"],
    )

    hits = search_segments("what did we decide about pricing?", top_k=3)
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import uuid

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MEMORY_ROOT = Path(os.environ.get("MEMORY_ROOT", Path(__file__).parent / "memory")).resolve()
SEGMENTS_DIR = MEMORY_ROOT / "segments"
INDEX_PATH = MEMORY_ROOT / "embedding_index" / "index.json"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
# qwen3-embedding:0.6b over nomic-embed-text: measured on a synthetic
# 3-segment / 5-query Romanian test set (see compare_embed_models.py),
# qwen3 gave an 8x wider separation gap between correct and irrelevant
# top-matches (0.032 vs 0.004) and much clearer ranking between topics
# (e.g. 0.21 gap between 1st/2nd place vs 0.04 for nomic). Neither model
# had a wide margin — this is a real but modest win, not a slam dunk.
EMBED_MODEL = os.environ.get("MEMORY_EMBED_MODEL", "qwen3-embedding:0.6b")

# Calibrated against qwen3-embedding:0.6b on that same synthetic set:
# weakest correct top-match was 0.561, strongest irrelevant top-match
# was 0.529. 0.50 sits below both with margin. THIS IS NOT A FINAL
# CALIBRATION — 3 segments / 5 queries is not enough data. Re-tune once
# you have real conversation segments (per the "open tuning questions"
# in context-memory-architecture.md).
RETRIEVAL_THRESHOLD = 0.50  # cosine similarity floor for search_segments()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class EmbeddingError(RuntimeError):
    """Raised when the local Ollama embedding call fails."""


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not INDEX_PATH.exists():
        _atomic_write_json(INDEX_PATH, {"entries": []})


# ---------------------------------------------------------------------------
# Atomic disk writes — the actual "never lose it" mechanism
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: temp file + os.replace(), so a crash mid-write
    can never leave a corrupted or half-written file at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # atomic on POSIX and Windows
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Embedding (local Ollama call)
# ---------------------------------------------------------------------------

def embed(text: str, model: str = EMBED_MODEL, timeout: float = 15.0) -> list[float]:
    """Call the local Ollama embeddings endpoint. Raises EmbeddingError on
    any failure — callers decide whether that's fatal (see flush_segment)."""
    if not text or not text.strip():
        raise EmbeddingError("cannot embed empty text")

    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise EmbeddingError(f"Ollama embedding call failed: {e}") from e
    except json.JSONDecodeError as e:
        raise EmbeddingError(f"Ollama returned non-JSON response: {e}") from e

    vector = body.get("embedding")
    if not isinstance(vector, list) or not vector:
        raise EmbeddingError(f"Ollama response missing 'embedding' field: {body}")
    return vector


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# summarize_locally — explicit hook, not implemented here
# ---------------------------------------------------------------------------

def summarize_locally(raw_messages: list[dict], max_tokens: int = 200) -> str:
    """Placeholder hook for a small-model summary call (e.g. ornith-1.5:9b
    via Ollama chat). Deliberately not implemented against a guessed API
    shape — wire this to your real Ollama chat endpoint once you're back
    in the OpenClaw process. Until then, callers can pass an explicit
    `summary=` to flush_segment() to bypass this entirely.
    """
    raise NotImplementedError(
        "summarize_locally() is a hook — wire it to your local Ollama chat "
        "model, or pass summary=... explicitly to flush_segment()."
    )


# ---------------------------------------------------------------------------
# Segment model
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    id: str
    timestamp: float
    raw_messages: list[dict]
    summary: str
    embedding: list[float]
    topic_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "raw_messages": self.raw_messages,
            "summary": self.summary,
            "embedding": self.embedding,
            "topic_tags": self.topic_tags,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_nontrivial(raw_messages: list[dict], min_messages: int = 2) -> bool:
    """Skip flushing 1-2 line segments, per context-memory-architecture.md."""
    return len(raw_messages) >= min_messages


def flush_segment(
    raw_messages: list[dict],
    summary: Optional[str] = None,
    topic_tags: Optional[list[str]] = None,
    skip_trivial: bool = True,
) -> Optional[str]:
    """Write a finished conversation segment to disk immediately and update
    the embedding index. Returns the segment_id, or None if the segment was
    skipped as trivial.

    This is meant to be called synchronously at the moment of a topic
    switch (Layer A or Layer B firing) — not deferred to a nightly batch.
    """
    ensure_dirs()

    if skip_trivial and not is_nontrivial(raw_messages):
        return None

    if summary is None:
        summary = summarize_locally(raw_messages)

    vector = embed(summary)

    segment = Segment(
        id=f"seg_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        timestamp=time.time(),
        raw_messages=raw_messages,
        summary=summary,
        embedding=vector,
        topic_tags=topic_tags or [],
    )

    segment_path = SEGMENTS_DIR / f"{segment.id}.json"
    _atomic_write_json(segment_path, segment.to_dict())

    _update_index(segment.id, vector, summary, topic_tags or [])

    return segment.id


def _update_index(segment_id: str, vector: list[float], summary: str, topic_tags: list[str]) -> None:
    ensure_dirs()
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    index["entries"].append({
        "id": segment_id,
        "embedding": vector,
        "summary": summary,
        "topic_tags": topic_tags,
    })
    _atomic_write_json(INDEX_PATH, index)


@dataclass
class SearchHit:
    id: str
    similarity: float
    summary: str
    topic_tags: list[str]


def search_segments(query: str, top_k: int = 3, threshold: float = RETRIEVAL_THRESHOLD) -> list[SearchHit]:
    """Semantic search over archived segments. Returns only the summary +
    metadata, never the raw transcript — matches the 'inject summary, not
    raw messages' rule from context-memory-architecture.md section 3."""
    ensure_dirs()
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    entries = index.get("entries", [])
    if not entries:
        return []

    query_vec = embed(query)

    scored = []
    for entry in entries:
        sim = _cosine_similarity(query_vec, entry["embedding"])
        if sim >= threshold:
            scored.append(SearchHit(
                id=entry["id"],
                similarity=sim,
                summary=entry["summary"],
                topic_tags=entry.get("topic_tags", []),
            ))

    scored.sort(key=lambda h: h.similarity, reverse=True)
    return scored[:top_k]


def load_segment(segment_id: str) -> dict:
    """Load a full segment (including raw_messages) by id — used when the
    agent explicitly needs the full transcript, not just the summary."""
    path = SEGMENTS_DIR / f"{segment_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No such segment: {segment_id}")
    return json.loads(path.read_text(encoding="utf-8"))
