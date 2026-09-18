from unittest.mock import patch

import pytest

import topic_reset_trigger as trigger_module
from topic_reset_trigger import is_explicit_reset_trigger, best_match
from memory_flush import EmbeddingError


# ---------------------------------------------------------------------------
# Fake embedding space: map specific strings to fixed vectors so tests
# don't depend on a real Ollama server. Anything not explicitly listed
# gets an "unrelated" vector, far from every anchor.
# ---------------------------------------------------------------------------

ANCHOR_VEC = [1.0, 0.0, 0.0]          # what every anchor phrase embeds to
NEAR_ANCHOR_VEC = [0.85, 0.15, 0.0]   # close enough to trigger at threshold 0.8
FAR_VEC = [0.0, 1.0, 0.0]             # unrelated

TEST_ANCHORS = ["ancora unu", "ancora doi"]


def fake_embed(text, model=None, timeout=None):
    if text in TEST_ANCHORS:
        return ANCHOR_VEC
    if text == "mesaj apropiat":
        return NEAR_ANCHOR_VEC
    if text == "mesaj departe":
        return FAR_VEC
    if text == "explodeaza":
        raise EmbeddingError("simulated failure")
    return FAR_VEC

@pytest.fixture(autouse=True)
def clear_anchor_cache_between_tests():
    """Anchor vectors are cached for the life of the process. Tests swap in
    different fake embedding spaces, so each one must start cold."""
    trigger_module.clear_anchor_cache()
    yield
    trigger_module.clear_anchor_cache()

# ---------------------------------------------------------------------------
# is_explicit_reset_trigger
# ---------------------------------------------------------------------------

@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_close_match_triggers(mock_embed):
    result = is_explicit_reset_trigger("mesaj apropiat", anchors=TEST_ANCHORS, threshold=0.8)
    assert result is True


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_far_message_does_not_trigger(mock_embed):
    result = is_explicit_reset_trigger("mesaj departe", anchors=TEST_ANCHORS, threshold=0.8)
    assert result is False


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_empty_message_does_not_trigger(mock_embed):
    assert is_explicit_reset_trigger("", anchors=TEST_ANCHORS) is False
    assert is_explicit_reset_trigger("   ", anchors=TEST_ANCHORS) is False


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_threshold_is_respected(mock_embed):
    # NEAR_ANCHOR_VEC has cosine similarity ~0.985 with ANCHOR_VEC —
    # comfortably above 0.8 but should fail a stricter 0.99 threshold.
    assert is_explicit_reset_trigger("mesaj apropiat", anchors=TEST_ANCHORS, threshold=0.8) is True
    assert is_explicit_reset_trigger("mesaj apropiat", anchors=TEST_ANCHORS, threshold=0.99) is False


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_uses_default_anchor_set_when_none_given(mock_embed):
    # Doesn't crash / doesn't require passing anchors explicitly. Uses
    # the TEST_ANCHORS-style fake space via an explicit unrelated
    # message; the point is only that omitting `anchors=` doesn't raise.
    result = is_explicit_reset_trigger("mesaj departe")
    assert result in (True, False)  # just confirm it runs without error


# ---------------------------------------------------------------------------
# Fail-safe behavior on embedding errors — the core reliability property
# ---------------------------------------------------------------------------

@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_embedding_error_on_message_fails_safe_to_false(mock_embed):
    # "explodeaza" is wired to raise EmbeddingError when embedded.
    result = is_explicit_reset_trigger("explodeaza", anchors=TEST_ANCHORS)
    assert result is False


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_embedding_error_on_one_anchor_skips_it_not_whole_call(mock_embed):
    # One bad anchor shouldn't crash the whole check — it should just be
    # skipped, and a good anchor further down the list can still match.
    anchors = ["explodeaza", "ancora unu"]
    result = is_explicit_reset_trigger("mesaj apropiat", anchors=anchors, threshold=0.8)
    assert result is True


def test_embedding_error_never_raises_out_of_the_function(monkeypatch):
    # Real (unmocked) call against an unreachable Ollama — must return
    # False, never propagate EmbeddingError to the caller.
    monkeypatch.setattr(trigger_module, "TRIGGER_EMBED_MODEL", "irrelevant")
    import memory_flush
    monkeypatch.setattr(memory_flush, "OLLAMA_BASE_URL", "http://127.0.0.1:1")
    result = is_explicit_reset_trigger("orice mesaj", anchors=["orice ancora"])
    assert result is False


# ---------------------------------------------------------------------------
# best_match — debugging/calibration helper
# ---------------------------------------------------------------------------

@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_best_match_returns_closest_anchor_and_score(mock_embed):
    anchor, score = best_match("mesaj apropiat", anchors=TEST_ANCHORS)
    assert anchor in TEST_ANCHORS
    assert score > 0.9


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_best_match_on_empty_message(mock_embed):
    anchor, score = best_match("", anchors=TEST_ANCHORS)
    assert anchor is None
    assert score == 0.0


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_best_match_skips_failing_anchor(mock_embed):
    anchors = ["explodeaza", "ancora unu"]
    anchor, score = best_match("mesaj apropiat", anchors=anchors)
    assert anchor == "ancora unu"
    assert score > 0.9


# ---------------------------------------------------------------------------
# Default anchor set / threshold sanity
# ---------------------------------------------------------------------------

def test_default_anchor_phrases_is_nonempty_list_of_strings():
    from topic_reset_trigger import DEFAULT_ANCHOR_PHRASES
    assert isinstance(DEFAULT_ANCHOR_PHRASES, list)
    assert len(DEFAULT_ANCHOR_PHRASES) > 0
    assert all(isinstance(p, str) and p.strip() for p in DEFAULT_ANCHOR_PHRASES)


def test_default_threshold_is_the_calibrated_value():
    # Locks in the calibration result from tools/calibrate_layer_a.py +
    # tools/verify_layer_a_threshold.py (0.80: 4/6 true positives, 0/6
    # false positives on the Romanian calibration set) — catches an
    # accidental revert to the uncalibrated 0.7 guess.
    assert trigger_module.RESET_TRIGGER_THRESHOLD == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# Anchor caching — the hot-path cost this layer promises to avoid

# ---------------------------------------------------------------------------

@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_anchor_is_embedded_once_across_many_messages(mock_embed):
    """Layer A is documented as the millisecond path. Re-embedding every
    anchor on every message meant one Ollama round-trip per anchor per
    message; anchors are fixed, so they are embedded once and reused."""
    for i in range(10):
        is_explicit_reset_trigger(f"mesaj departe {i}", anchors=TEST_ANCHORS, threshold=0.8)

    embedded = [call.args[0] for call in mock_embed.call_args_list]
    for anchor in TEST_ANCHORS:
        assert embedded.count(anchor) == 1, f"{anchor!r} re-embedded {embedded.count(anchor)} times"


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_message_is_still_embedded_every_call(mock_embed):
    """Only anchors are cached — each incoming message is distinct and must
    be embedded fresh, or the detector would answer from stale input."""
    is_explicit_reset_trigger("mesaj departe", anchors=TEST_ANCHORS)
    is_explicit_reset_trigger("mesaj departe", anchors=TEST_ANCHORS)

    embedded = [call.args[0] for call in mock_embed.call_args_list]
    assert embedded.count("mesaj departe") == 2


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_failed_anchor_is_retried_not_cached_as_broken(mock_embed):
    """A transient embedding outage must not permanently disable an anchor
    for the lifetime of the process — failures are never cached."""
    anchors = ["explodeaza", "ancora unu"]
    is_explicit_reset_trigger("mesaj apropiat", anchors=anchors, threshold=0.8)
    is_explicit_reset_trigger("mesaj apropiat", anchors=anchors, threshold=0.8)

    embedded = [call.args[0] for call in mock_embed.call_args_list]
    assert embedded.count("explodeaza") == 2   # retried
    assert embedded.count("ancora unu") == 1   # cached after its one success


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_cache_is_keyed_by_model(mock_embed):
    """Two embedding models produce different vector spaces; a vector cached
    under one must never be served for the other."""
    is_explicit_reset_trigger("mesaj departe", anchors=["ancora unu"], model="model-a")
    is_explicit_reset_trigger("mesaj departe", anchors=["ancora unu"], model="model-b")

    embedded = [call.args[0] for call in mock_embed.call_args_list]
    assert embedded.count("ancora unu") == 2


@patch("topic_reset_trigger.embed", side_effect=fake_embed)
def test_clear_anchor_cache_forces_re_embedding(mock_embed):
    is_explicit_reset_trigger("mesaj departe", anchors=TEST_ANCHORS)
    trigger_module.clear_anchor_cache()
    is_explicit_reset_trigger("mesaj departe", anchors=TEST_ANCHORS)

    embedded = [call.args[0] for call in mock_embed.call_args_list]
    assert embedded.count(TEST_ANCHORS[0]) == 2
