"""
Calibration script for RETRIEVAL_THRESHOLD in memory_flush.py.

Run this directly:
    python3 calibrate_threshold.py

It seeds three clearly-different topic segments, then runs both
"should match" and "should NOT match" queries against them, printing
every raw similarity score (threshold=0.0) so we can see where the
real gap between relevant/irrelevant sits — instead of guessing.
"""

from memory_flush import flush_segment, search_segments

# Three segments on clearly different topics
flush_segment(
    raw_messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
    summary="discutie despre pricing freelance pentru site-uri de restaurant",
    topic_tags=["freelance"],
)
flush_segment(
    raw_messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
    summary="configurare Behringer WING pentru sunetul de la biserica Tabor",
    topic_tags=["audio"],
)
flush_segment(
    raw_messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
    summary="pregatire pentru aplicatia la facultate in SUA, eseu personal",
    topic_tags=["college"],
)

queries = [
    "cat percep pentru un site web",          # should match: freelance
    "probleme cu mixerul audio la biserica",   # should match: audio
    "ce scriu in eseul de admitere",           # should match: college
    "ce mancare sa gatesc diseara",            # should NOT match
    "cati bani am cheltuit luna asta",         # should NOT match
]

for q in queries:
    hits = search_segments(q, threshold=0.0, top_k=3)  # threshold 0 to see ALL scores
    print(f"\nQuery: {q!r}")
    for h in hits:
        print(f"  {h.similarity:.3f}  {h.summary}")
