#!/usr/bin/env python3
"""
topic_reset_trigger.py — Layer A of the topic-change detector.

Implements section 1 ("Layer A — explicit trigger") of
context-memory-architecture.md. This is the zero-LLM path: a raw
incoming message is compared against a fixed set of anchor phrases
using embedding similarity only — no chat model call, milliseconds,
never touches Ornith's live context. This must stay a completely
separate process from the main conversation, so there's no race
between "Ornith answers using stale context" and "the detector
decides to clear it."

On a match, the caller (your agent loop) is expected to call
memory_flush.flush_segment() on the finished hot_context and then
clear it — this module only detects the trigger, it does not touch
session state itself (session/hot_context management lives in your
real OpenClaw process, not here).

Usage:

    from topic_reset_trigger import is_explicit_reset_trigger

    if is_explicit_reset_trigger(incoming_message):
        # flush_segment(...) the finished hot_context, then clear it
        ...

Calibration:
    See calibrate_layer_a.py for a script that scores a set of
    "should trigger" / "should NOT trigger" phrases against the
    anchor set, the same way calibrate_threshold.py did for
    RETRIEVAL_THRESHOLD in memory_flush.py. Don't trust the default
    threshold below without running that against real Ollama output —
    it's a starting point, not a measured value (same caveat as the
    original RETRIEVAL_THRESHOLD before calibration).
"""

from __future__ import annotations

import os
from typing import Optional

from memory_flush import embed, _cosine_similarity, EmbeddingError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Calibrated against tools/calibrate_layer_a.py. IMPORTANT: at 0.7 the
# gap between "should trigger" and "should NOT trigger" phrases was
# *negative* (-0.125) — the classic embedding failure of lexical overlap
# masquerading as semantic similarity (e.g. "am un subiect nou pentru
# eseul de facultate" scored HIGHER than a real reset request, purely
# because it shares the words "subiect nou" with an anchor, not because
# it means the same thing — this is a documented embedding failure mode,
# not a bug in this code; see hybrid-search literature on lexical vs.
# semantic mismatch). No single threshold cleanly separates the two
# classes on this anchor set.
#
# Raised to 0.80 so Layer A only fires on near-exact phrasing, where
# embeddings ARE reliable. On the calibration set this is the sweet
# spot: 0.80 catches 4/6 real triggers with 0 false positives; 0.85+
# only drops true positives (loses "ok alt subiect acum" at 0.811) for
# no precision gain, since the worst false positive (0.738) is already
# well clear of 0.80.
#
# Everything more ambiguous (paraphrases, partial topic shifts, cases
# like "las-o balta cu freelance-ul" at 0.613) is deliberately left to
# Layer B's small classifier model instead, which can actually reason
# about intent rather than just measuring vector distance. Layer A's
# job shrinks to "catch the obvious, unambiguous case fast" — not
# "catch every possible phrasing of a topic reset."
RESET_TRIGGER_THRESHOLD = float(os.environ.get("RESET_TRIGGER_THRESHOLD", "0.80"))

# Same embedding model chosen for memory_flush.py after calibration —
# keeping Layer A and retrieval on the same model avoids a second,
# separately-calibrated similarity space.
TRIGGER_EMBED_MODEL = os.environ.get("MEMORY_EMBED_MODEL", "qwen3-embedding:0.6b")

# Starting anchor set. The five from context-memory-architecture.md are
# formal/written-register ("schimbăm subiectul", "subiect nou"). Extended
# here with more colloquial variants a person would actually type to an
# agent in chat, since Layer A only catches phrases *semantically close*
# to something in this list — a narrow, formal-only list would miss
# casual real usage. Paul: trim/edit anything below that doesn't sound
# like you; these are a reasonable starting guess, not measured on your
# actual phrasing.
DEFAULT_ANCHOR_PHRASES = [
    # from context-memory-architecture.md
    "schimbăm subiectul",
    "subiect nou",  # riskiest anchor: short + generic, causes lexical-overlap
                     # false positives (e.g. "am un subiect nou pentru eseul
                     # de facultate" scored 0.738 against it) — safe at the
                     # current 0.80 threshold on the calibration set, but if
                     # false triggers show up in real use, this is the first
                     # anchor to reconsider
    "las-o balta cu asta",
    "trecem la altceva",
    "nou task",
    # colloquial extensions
    "gata cu asta",
    "hai sa vorbim de altceva",
    "uita ce am zis, altceva acum",
    "sa trecem mai departe",
    "nu mai conteaza asta, vreau sa te intreb altceva",
    "ok, alt subiect",
    "revin la asta mai tarziu, acum vreau",
]


# ---------------------------------------------------------------------------
# Core detector
# ---------------------------------------------------------------------------

def is_explicit_reset_trigger(
    message: str,
    anchors: Optional[list[str]] = None,
    threshold: float = RESET_TRIGGER_THRESHOLD,
    model: str = TRIGGER_EMBED_MODEL,
) -> bool:
    """Return True if `message` is semantically close to any anchor phrase.

    Fails safe: if the embedding call itself fails (Ollama down, network
    issue), this returns False rather than raising — a missed explicit
    trigger just falls through to Layer B / normal handling, which is a
    much smaller problem than crashing the message-handling path on an
    embedding outage.
    """
    if not message or not message.strip():
        return False

    anchor_list = anchors if anchors is not None else DEFAULT_ANCHOR_PHRASES

    try:
        msg_vec = embed(message, model=model)
    except EmbeddingError:
        return False

    for phrase in anchor_list:
        try:
            anchor_vec = embed(phrase, model=model)
        except EmbeddingError:
            continue
        if _cosine_similarity(msg_vec, anchor_vec) >= threshold:
            return True

    return False


def best_match(
    message: str,
    anchors: Optional[list[str]] = None,
    model: str = TRIGGER_EMBED_MODEL,
) -> tuple[Optional[str], float]:
    """Return (best-matching anchor phrase, its similarity score).

    Useful for calibration and debugging — is_explicit_reset_trigger()
    only needs the boolean, but seeing *which* anchor nearly matched
    (and how closely) is what calibrate_layer_a.py needs to print.
    """
    if not message or not message.strip():
        return None, 0.0

    anchor_list = anchors if anchors is not None else DEFAULT_ANCHOR_PHRASES

    try:
        msg_vec = embed(message, model=model)
    except EmbeddingError:
        return None, 0.0

    best_phrase = None
    best_score = 0.0
    for phrase in anchor_list:
        try:
            anchor_vec = embed(phrase, model=model)
        except EmbeddingError:
            continue
        sim = _cosine_similarity(msg_vec, anchor_vec)
        if sim > best_score:
            best_score = sim
            best_phrase = phrase

    return best_phrase, best_score
