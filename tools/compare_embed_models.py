"""
Compares nomic-embed-text vs qwen3-embedding:0.6b on the exact same
segments and queries, so we can see which one actually separates
relevant from irrelevant matches better on Romanian text.

Run this directly (after `ollama pull qwen3-embedding:0.6b`):
    python3 compare_embed_models.py

Does NOT touch memory_flush's real segment storage — everything here
runs in-memory against a temporary index, so it's safe to run
repeatedly without polluting your real memory/ directory.
"""

from memory_flush import embed, _cosine_similarity

SEGMENTS = [
    ("freelance", "discutie despre pricing freelance pentru site-uri de restaurant"),
    ("audio", "configurare Behringer WING pentru sunetul de la biserica Tabor"),
    ("college", "pregatire pentru aplicatia la facultate in SUA, eseu personal"),
]

QUERIES = [
    ("freelance", "cat percep pentru un site web"),
    ("audio", "probleme cu mixerul audio la biserica"),
    ("college", "ce scriu in eseul de admitere"),
    (None, "ce mancare sa gatesc diseara"),
    (None, "cati bani am cheltuit luna asta"),
]


def run_for_model(model_name: str):
    print(f"\n{'=' * 60}")
    print(f"MODEL: {model_name}")
    print("=" * 60)

    seg_vectors = [(tag, summary, embed(summary, model=model_name)) for tag, summary in SEGMENTS]

    best_relevant = []
    best_irrelevant = []

    for expected_tag, query in QUERIES:
        qvec = embed(query, model=model_name)
        scored = sorted(
            ((tag, summary, _cosine_similarity(qvec, vec)) for tag, summary, vec in seg_vectors),
            key=lambda x: x[2],
            reverse=True,
        )
        print(f"\nQuery: {query!r}  (expected: {expected_tag or 'NO MATCH'})")
        for tag, summary, sim in scored:
            marker = " <-- correct" if tag == expected_tag else ""
            print(f"  {sim:.3f}  [{tag}] {summary}{marker}")

        top_score = scored[0][2]
        top_tag = scored[0][0]
        if expected_tag is not None and top_tag == expected_tag:
            best_relevant.append(top_score)
        elif expected_tag is None:
            best_irrelevant.append(top_score)

    if best_relevant and best_irrelevant:
        gap = min(best_relevant) - max(best_irrelevant)
        print(f"\n--- Summary for {model_name} ---")
        print(f"Weakest correct top-match score:   {min(best_relevant):.3f}")
        print(f"Strongest irrelevant top-match score: {max(best_irrelevant):.3f}")
        print(f"Separation gap: {gap:.3f}  ({'usable' if gap > 0 else 'NO CLEAN THRESHOLD EXISTS'})")


if __name__ == "__main__":
    run_for_model("nomic-embed-text")
    run_for_model("qwen3-embedding:0.6b")
