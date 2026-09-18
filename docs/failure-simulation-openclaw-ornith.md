# Failure Simulation: OpenClaw + Ornith-1.5-35B-A3B, 48GB RAM

A walk-through of how this specific stack breaks in practice — not
hypothetical risks, but patterns confirmed by real bug reports (OpenClaw
GitHub issues) and real user reports of the Ornith model family. Organized
as a timeline: what happens at hour 1, hour 10, day 3, day 30.

---

## Hour 1 — everything looks fine

Fresh session. Context small. `compaction.mode: safeguard`,
`keepRecentTokens: 150000`, `reserveTokensFloor: 30000` — all comfortably
unused. This is the state every demo and every "it works!" screenshot
captures. It tells you nothing about hour 10.

---

## Hour 3-8 — the reasoning degradation zone

This is the most important finding, and it's specific to your model
family, not generic LLM behavior. A real user report on the Ornith-1.0-35B
model (architecturally identical hybrid-attention design to your 1.5)
found reasoning capabilities begin to degrade significantly approaching
100K tokens, with the breakdown starting around 70-80K context tokens —
well before the advertised 256K/262K limit. In a head-to-head agentic
task, the same report found Ornith ignored provided files entirely and
fabricated output from its own prior context instead of reading what was
actually given to it.

**Why this matters architecturally, not just as a warning**: the
hybrid linear-attention design (30 GatedDeltaNet layers + 10 full-attention
layers in the 1.0 family) is exactly what makes the KV cache cheap
(~5GB at full context, as you found). But cheap cache isn't the same as
reliable recall — linear-attention layers compress history lossily by
design. The context window not filling up (RAM-wise) and the model
still losing track of what's in it are two separate failure modes, and
your 48GB budget only protects you from the first one.

**Simulated moment**: You're 6 hours into a coding session. Context sits
around 85K tokens — comfortable by RAM math (still only ~2GB KV cache).
Ornith confidently "remembers" a file you asked it to edit two hours ago
and edits from its own confabulated memory of it instead of re-reading —
because at that context depth, recall reliability, not memory pressure,
is the collapsing variable.

**Where your architecture already helps**: this is the single strongest
argument for the topic-isolation design from the other document. If
"serious work" (file edits, terminal tasks) always spawns a clean
sub-session with only the relevant files loaded — not 6 hours of
accumulated chat — you never approach the 70-80K degradation zone for
task-critical work. General chat can sit at high context; work sessions
should stay small on purpose, not just for RAM but for reliability.

---

## Day 1, background load — cron accumulation

Real reported bug (OpenClaw #17820): isolated cron runs register into two
module-level maps and never clean them up. With two jobs every 5 minutes
(576 runs/day — comparable to a heartbeat + a periodic check like yours),
gateway memory grows 1-2GB over 10-12 hours until it hits swap and
becomes unresponsive.

**Simulated moment**: Your heartbeat + a hypothetical "job scouting"
cron (from the freelance idea) both run every few minutes overnight.
By morning, the gateway process itself — not Ornith, not the KV cache,
the Node.js gateway process — has quietly eaten 2-3GB it never releases.
Do this for a week without a restart and it compounds toward your RAM
ceiling before Ornith even loads.

**Mitigation, concrete**: this isn't something your context architecture
fixes — it's a gateway-level bug class. Watch for it directly: track
gateway process RSS over time, not just model memory. Restart the
gateway on a schedule (e.g. daily at a quiet hour) as a blunt but
effective mitigation until/unless upstream fixes the leak.

---

## Day 1-3 — the compaction "bad path"

A detailed community writeup describes two very different compaction
behaviors. The good path: compaction runs ahead of time, preserving your
most recent ~20,000 tokens intact and keeping file paths/IDs even in the
summarized portion. The bad path — overflow recovery — triggers only
after the context is already too big and the request gets rejected: at
that point OpenClaw compresses everything at once just to get working
again, with no memory flush and no save-to-disk first, causing maximum,
uncontrolled context loss.

The same source states the core principle bluntly: **if it's not written
to a file, it doesn't exist** — no single in-memory mechanism (pruning,
compaction, cache-ttl) is sufficient alone; disk is the only thing that
survives a bad-path event.

**Simulated moment**: This is exactly the scenario your "incremental
flush" design (Layer A/B detector + synchronous write-to-disk on topic
shift) is built to prevent. Without it: a long, uninterrupted session hits
the overflow wall, OpenClaw panic-compresses, and whatever wasn't already
on disk is gone — including, potentially, the freelance job status, the
half-finished task detail, the thing you needed tomorrow morning. With
your architecture: every topic shift already flushed its segment before
the session ever got large enough to hit the bad path. **This single
scenario is the strongest real-world justification for building the
memory architecture from the other document before scaling up usage.**

**A separate, confirmed OpenClaw+Ollama-specific bug**: local Ollama
deployments have been reported to ignore configured
`compaction.reserveTokens`/`reserveTokensFloor` settings entirely,
running the overflow precheck with a hardcoded `reserveTokens=16384`
regardless of config — causing false "context overflow" failures well
before the real limit. Worth checking directly against your own config
once you rebuild, rather than trusting the documented settings blindly.

---

## Day 3-7 — orphaned processes eating unified memory

A separate stability project documents Chromium instances from browser
automation getting orphaned after gateway restarts — one observed case
had 24 orphaned processes consuming 4GB. Since you use CDP-based browser
control (per your README), this failure class applies directly to your
setup, not just to other users' configs.

**Simulated moment**: A week of gateway restarts (troubleshooting,
updates, crashes) each leaves behind one or two zombie Chromium
processes that never get killed. By day 7, you've silently lost 3-4GB
of your 48GB budget to browser tabs that no longer serve any active
task — invisible unless you specifically check `ps aux` or Activity
Monitor, because nothing in OpenClaw's own reporting surfaces it.

**Mitigation**: a periodic cleanup pass (part of your existing
`artifact_manager.py stop`-style discipline, extended) that kills
orphaned Chromium processes with no corresponding active gateway
session — not a memory-architecture fix, a process-hygiene one.

---

## Week 2+ — the "month without /new" endurance test

This is where every mechanism above compounds instead of acting alone.
Simulated worst case, stacking the failures in sequence:

1. **Day 1-5**: cron leak (map #17820) accumulates ~1-2GB per busy day,
   partially masked because you haven't restarted the gateway.
2. **Day 3**: a long uninterrupted work session pushes past 80K context
   tokens; Ornith's recall reliability degrades exactly as in the Hour
   3-8 scenario — it starts "remembering" things wrong, not running out
   of room.
3. **Day 4**: that same session eventually hits real context overflow.
   Because reserveTokens was silently overridden to 16384 (Ollama-specific
   bug above), the overflow trigger fires earlier than your configured
   safeguard expects — and hits the **bad path**: panic-compression, no
   flush-first, meaningful loss of what happened days 3-4.
4. **Day 5**: a browser-automation task from days earlier left orphaned
   Chromium processes running; combined with the still-unreleased cron
   leak, unified memory pressure is now high enough that Ornith's own
   inference — not just background processes — starts competing for
   bandwidth, and everything (correctly diagnosed in your README as a
   real prior incident) slows down or times out.
5. **Day 6**: you ask "what happened with the freelance job from Tuesday?"
   — and because the relevant segment was mid-conversation when the
   overflow/panic-compression hit on day 4, it was never cleanly flushed.
   The month-long-conversation dream quietly loses exactly the kind of
   fact it was supposed to preserve.

**The throughline**: none of these five steps is exotic. Each is a
documented, reported failure mode for exactly this class of stack. The
"month without /new" goal doesn't fail because of one dramatic crash —
it fails through slow compounding of small, individually-survivable
issues that your architecture either catches early (the memory-flush
design) or doesn't address at all (gateway process leaks, orphaned
browser processes) and needs separate operational discipline for.

---

## What this simulation changes about the priority order

Building from the other document, in the order that actually prevents
the worst outcomes first:

1. **Ship the incremental disk-flush first**, even before the topic
   classifier is well-tuned. It's the only thing that protects you from
   the bad-path compaction scenario (Day 1-3), which is the single most
   destructive failure in this whole simulation.
2. **Cap and monitor context depth for "serious work" sessions**
   specifically — not because of RAM, because of the 70-80K reasoning
   degradation zone. This is a correctness problem, not a memory
   problem, and the task-isolation design already planned handles it as
   a side effect if you enforce it.
3. **Treat gateway process health as a separate metric from model
   memory.** Track RSS of the gateway process itself over multi-day
   uptime; restart on a schedule until upstream fixes the cron-map leak.
4. **Audit orphaned processes (Chromium, and anything else spawned by
   tool calls) on a schedule**, not just at gateway restart.
5. **Verify your actual compaction config is being honored** by the
   Ollama provider path specifically — don't assume the documented
   settings apply; check behavior directly once rebuilt.

None of this is a reason to not build the "month-long conversation"
goal — it's the difference between building it optimistically and
building it knowing exactly which five things will quietly sabotage it
if left unaddressed.
