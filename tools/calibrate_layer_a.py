"""
Calibration script for RESET_TRIGGER_THRESHOLD in topic_reset_trigger.py.

Run this directly:
    python3 calibrate_layer_a.py

Tests two groups of phrases against the anchor set:
  - phrases that SHOULD trigger an explicit topic reset
  - ordinary conversational phrases that should NOT trigger one
      (including ones that mention "subject"/"topic" in passing,
       which is the realistic false-positive risk for this layer)

Prints the best anchor match + score for each, then the same
separation-gap summary style used in compare_embed_models.py, so we
calibrate RESET_TRIGGER_THRESHOLD on measurement instead of the 0.7
guess from context-memory-architecture.md.
"""

from topic_reset_trigger import DEFAULT_ANCHOR_PHRASES, best_match

SHOULD_TRIGGER = [
    "schimbam subiectul",
    "hai sa trecem la altceva",
    "gata cu asta, vreau sa te intreb altceva",
    "las-o balta cu freelance-ul, ce ma intrebai despre WING",
    "ok alt subiect acum",
    "nu mai conteaza ce am zis, uite ce vreau de fapt",
]

SHOULD_NOT_TRIGGER = [
    "care e subiectul discutiei noastre de azi",  # mentions "subject" but isn't a reset
    "am un subiect nou pentru eseul de facultate",  # mentions "new topic" but about essay content
    "cat percep pentru un site web",
    "probleme cu mixerul audio la biserica",
    "ce mancare sa gatesc diseara",
    "explica-mi cum functioneaza cache-ul TTL",
]


def run():
    print(f"Anchor set has {len(DEFAULT_ANCHOR_PHRASES)} phrases.\n")

    trigger_scores = []
    print("=" * 60)
    print("SHOULD TRIGGER")
    print("=" * 60)
    for phrase in SHOULD_TRIGGER:
        anchor, score = best_match(phrase)
        trigger_scores.append(score)
        print(f"  {score:.3f}  {phrase!r}\n         -> closest anchor: {anchor!r}")

    no_trigger_scores = []
    print("\n" + "=" * 60)
    print("SHOULD NOT TRIGGER")
    print("=" * 60)
    for phrase in SHOULD_NOT_TRIGGER:
        anchor, score = best_match(phrase)
        no_trigger_scores.append(score)
        print(f"  {score:.3f}  {phrase!r}\n         -> closest anchor: {anchor!r}")

    weakest_trigger = min(trigger_scores)
    strongest_non_trigger = max(no_trigger_scores)
    gap = weakest_trigger - strongest_non_trigger

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Weakest 'should trigger' score:      {weakest_trigger:.3f}")
    print(f"Strongest 'should NOT trigger' score: {strongest_non_trigger:.3f}")
    print(f"Separation gap: {gap:.3f}  ({'usable' if gap > 0 else 'NO CLEAN THRESHOLD EXISTS'})")
    if gap > 0:
        suggested = strongest_non_trigger + gap / 2
        print(f"Suggested threshold (midpoint): {suggested:.3f}")


if __name__ == "__main__":
    run()
