# Context Switching & Incremental Memory — Architecture Pseudocode

Core idea: **separate what the model is actively thinking about (hot context)
from what it knows long-term (cold, searchable memory).** Never let the
two blur into one giant, ever-growing prompt.

> **A note on language**: anchor phrases and example user messages
> throughout this document are in Romanian — that's the language this
> agent is actually operated in, so the detector has to match real input,
> not a translation of it. All surrounding documentation, code, and
> comments are in English.

Three subsystems, working together:
1. Topic-change detector (runs before every message reaches the big model)
2. Incremental flush (writes finished segments to disk immediately, not at night)
3. Task isolation (spawns a clean sub-session for "serious" work like coding)

---

## 1. Topic-change detector — two layers, not one

Research backing (Membox, arXiv 2601.03785) found that most segment-worthy
transitions are **partial shifts**, not clean discontinuities — new topics
usually emerge through residual links to prior context, not abrupt resets.
That means a binary "same topic / different topic" flag loses information
you actually want. Use two independent layers instead:

**Layer A — explicit trigger (zero LLM, milliseconds, never touches Ornith's context)**

This is the "schimbăm subiectul" case. It runs as a completely separate
process that never sees Ornith's live conversation — only the raw incoming
message — so there's no race between "Ornith answers using stale context"
and "the detector decides to clear it."

```
function is_explicit_reset_trigger(message):
    # nomic-embed-text (already installed) against a fixed anchor set —
    # no LLM call needed at all for this layer
    trigger_phrases = [
        "schimbăm subiectul", "subiect nou", "las-o balta cu asta",
        "trecem la altceva", "nou task"
    ]
    return semantic_match(message, trigger_phrases, threshold=0.7)
```

**Layer B — 3-state classifier (small LLM, catches implicit drift)**

For everything the user doesn't say out loud. Classifies into
`continuous`, `partial_shift`, or `discontinuous` instead of a binary
flag — this is what lets you treat "related new topic" differently from
"totally unrelated new topic."

Model choice: **Llama-3.2-1B-Instruct or Qwen2.5-1.5B-Instruct.** A
systematic evaluation of 41 open-weight models on zero-shot intent
classification found both Pareto-optimal (best accuracy per unit of
latency) — the frontier is dominated by small models here, so there's no
reason to spend 35B-class compute on this. If you want a stronger option
and have RAM to spare, Qwen2.5-3B-Instruct has the best aggregate accuracy
under 4B (0.632) at ~5.8GB VRAM — worth it only if 1B/1.5B misclassifies
too often in practice.

```
function on_user_message(new_message, session):
    if is_explicit_reset_trigger(new_message):          # Layer A — instant, no context needed
        handle_explicit_topic_change(session)
        return confirm_to_user("Am arhivat subiectul anterior.")

    window = session.last_two_messages()                # Membox-style: 1 user + 1 agent turn
    shift_state = classify_topic_shift(window, new_message)   # Layer B — Llama-3.2-1B / Qwen2.5-1.5B

    if shift_state == "discontinuous":
        handle_topic_change(session)                    # full flush + clear, as below
    elif shift_state == "partial_shift":
        handle_partial_shift(session)                   # flush + keep a short bridging summary
    # "continuous" — no action, proceed normally

    session.hot_context.append(new_message)
    response = call_ornith(session.hot_context)
    session.hot_context.append(response)
    return response


function handle_explicit_topic_change(session):
    finished_segment = session.hot_context.copy()
    write_to_disk(finished_segment, summarize=True, embed=True)
    session.hot_context = []                            # full, guaranteed clear
    log_event("explicit_reset", timestamp=now())


function handle_partial_shift(session):
    finished_segment = session.hot_context.copy()
    write_to_disk(finished_segment, summarize=True, embed=True)
    bridge = summarize_locally(finished_segment, max_tokens=100)
    session.hot_context = [f"[context bridge: {bridge}]"]  # keep a thread, don't fully sever it
```

The threshold for Layer A (`0.7`) and the classifier prompt for Layer B
both need tuning against your real conversation logs — start here, adjust
once you see false positives/negatives in practice.

---

## 2. Incremental flush — writing the past down as it happens

Triggered by the detector above — NOT by /new, NOT only at 3 AM.

```
function handle_topic_change(session):
    finished_segment = session.hot_context.copy()

    if finished_segment.is_nontrivial():                # skip 1-2 line segments
        segment_id = generate_id()
        summary = summarize_locally(finished_segment)    # small/cheap model call
        embedding = embed(summary)

        write_to_disk({
            "id": segment_id,
            "timestamp": now(),
            "raw_messages": finished_segment,
            "summary": summary,
            "embedding": embedding,
            "topic_tags": extract_tags(summary)
        }, path=f"memory/segments/{segment_id}.json")

        update_embedding_index(segment_id, embedding)     # for later semantic search

    session.hot_context = []                               # clear the live window
    session.hot_context.append_system_note(
        f"[previous topic archived as {segment_id}]"
    )
```

Key property: this happens **synchronously, at the moment of the switch** —
not deferred to a nightly batch job. The "dreaming" cycle at 3 AM still runs,
but its job changes: instead of being the *only* place memory gets saved,
it becomes a **consolidation** pass — merging related segments, pruning
duplicates, strengthening links between related topics. The real-time
flush guarantees nothing is ever lost between now and the next dreaming cycle.

---

## 3. Retrieval — bringing the past back, on demand only

This is what makes memory "searchable, not resident." The model never
carries old segments by default — it asks for them when needed.

```
function on_user_message_v2(new_message, session):
    new_embedding = embed(new_message)

    # Does this message reference something from the past?
    if looks_like_reference(new_message):                 # heuristic or small classifier
        matches = search_embedding_index(new_embedding, top_k=3)
        relevant = [m for m in matches if m.similarity > RETRIEVAL_THRESHOLD]

        for segment in relevant:
            session.hot_context.inject(segment.summary)   # summary, not raw transcript

    # ... continue as in function 1
```

`looks_like_reference` catches things like "revin la treaba cu freelance-ul
de aseară" — explicit backreferences. You can also run retrieval speculatively
on every message (cheap, since it's just an embedding lookup), and only
inject if similarity clears the threshold. That way even *implicit*
callbacks get caught without the user having to say "remember when..."

---

## 4. Task isolation — full attention for terminal/coding work

Separate mechanism, triggered by *intent*, not topic drift.

```
function on_user_message_v3(new_message, session):
    if is_serious_task(new_message):        # "edit this file", "run this", "fix the bug in X"
        sub_session = spawn_isolated_session(
            parent=session,
            context=extract_task_context(new_message),   # ONLY what's needed for the task
            budget_tokens=RESERVED_TASK_BUDGET
        )
        result = run_task_to_completion(sub_session)      # uses artifact_manager.py discipline

        # Only the outcome re-enters the main conversation — not the full
        # reasoning trace, not intermediate tool calls.
        session.hot_context.append_system_note(
            f"[completed: {result.short_summary}] — full log at {result.artifact_path}"
        )
        return result.short_summary

    else:
        return on_user_message_v2(new_message, session)
```

This is the same principle you already use for cron jobs
(`sessionTarget: isolated`) — just extended into live conversation.
The sub-session gets a clean slate: no gym chat, no freelance job
context, just the task. When it's done, only a short result comes back,
never the raw internal reasoning.

---

## Storage layout (concrete)

```
memory/
├── segments/                    # one file per archived topic segment
│   ├── seg_20260913_0900.json
│   └── seg_20260913_1130.json
├── embedding_index/              # vector index for semantic search
│   └── index.faiss  (or sqlite-vec, or simple flat file for this scale)
├── consolidated/                 # output of the nightly "dreaming" pass
│   └── weekly_summary_2026W37.json
└── active_session.json           # the current hot_context, volatile-ish
```

## What changes vs. what you have today

| Today | With this architecture |
|---|---|
| Memory saved at /new or 3 AM dreaming | Saved the instant a topic ends |
| Full history risks bleeding into new topics | Hot context resets on topic shift |
| Long context = model re-reads everything | Model retrieves only what's relevant, on demand |
| Terminal/coding work shares context with chat | Isolated sub-session, clean budget, summary-only return |

## Open tuning questions for when you build this

- Layer A/B thresholds — need real conversation data to calibrate
- Cost of `summarize_locally()` — can this run on Ornith-1.5-9B instead of
  the 35B, to keep it cheap and fast?
- Embedding index choice — FAISS vs. sqlite-vec vs. something simpler,
  given your scale (personal use, not production-scale)
- How `is_serious_task()` gets implemented — small classifier model vs.
  rule-based heuristics vs. asking Ornith itself with a tiny, cheap prompt
- Embedding model: `nomic-embed-text` (already installed, 274MB) works,
  but since you write to Claude in Romanian, `qwen3-embedding:0.6b`
  (639MB, 100+ languages, Matryoshka truncation) may retrieve better on
  Romanian-language segments — worth an A/B test once you have real logs

## Research this architecture draws on

- **Membox** (arXiv 2601.03785) — the 3-state topic-continuity
  classification (continuous / partial shift / discontinuous) and the
  small sliding-window classification approach come directly from this
  paper's design.
- **Context engineering survey** (mem0.ai, 2026) — the "isolate" strategy
  (separate memory stores per topic to prevent interference) and the
  framing that most agent failures are context failures, not model
  failures.
- **Selecting Open-Weight Models for Zero-Shot Intent Classification**
  (arXiv 2607.27421) — systematic 41-model evaluation behind the
  Llama-3.2-1B / Qwen2.5-1.5B / Qwen2.5-3B recommendations above.
- **Memory for Autonomous LLM Agents survey** (arXiv 2603.07670) —
  broader taxonomy (context-resident compression, retrieval-augmented
  stores, reflective self-improvement, hierarchical virtual context)
  that the write/flush/retrieve split above is a small instance of.
