# OpenClaw Setup

[![tests](https://github.com/DrFoxi14/openclaw-setup/actions/workflows/tests.yml/badge.svg)](https://github.com/DrFoxi14/openclaw-setup/actions/workflows/tests.yml)

> Personal AI agent gateway running on a local LLM (Ornith/Qwen), controlled via Telegram, with tool access to Gmail/Calendar/Drive/Docs (via `gog`), a browser-control layer, and a persistent memory system.

## Why this exists

I started this with almost no budget — my first machine was an old Lenovo (i5, 8GB RAM), nowhere near enough to run anything locally. So I began by wiring it up to a cloud GPU API (NVIDIA NIM) just to get something working.

Once I moved to a MacBook, the first thing I did was rip out every external API dependency and switch to a fully local model (Ornith, via Ollama). Two reasons: I don't have money for ongoing API costs, and I care about privacy — DeepSeek was tempting on capability, but I wasn't comfortable with where that data goes. Rather than depend on a subscription, I chose to invest in local hardware I actually own and control.

So this isn't "local AI because it's trendy" — it's local AI because it was the only option that fit both my budget and what I was willing to trust with personal data.

## Architecture

- **Gateway / agent loop** — local process, Telegram as the interface
- **Model**: Ornith-1.5:35B via Ollama, running fully locally (no external API calls for inference)
- **`gog`** — Google OAuth CLI for Gmail/Calendar/Drive/Docs/Sheets access
- **Browser control layer** — CDP-based (Chromium only: Chrome/Brave, not Safari)
- **Memory-core plugin** — persistent memory with a "dreaming" consolidation process (light/REM/deep cycles), scheduled for 3 AM only
- **Context & memory layer** (`src/`) — a topic-change detector and incremental disk-flush built on top of memory-core, so a finished topic is written to disk the moment it ends rather than waiting for the nightly pass. Design: [`docs/context-memory-architecture.md`](docs/context-memory-architecture.md). Why it exists: [`docs/failure-simulation-openclaw-ornith.md`](docs/failure-simulation-openclaw-ornith.md).
- **Heartbeat** — periodic background check
- **Scheduled jobs**: email triage (automatic inbox check/sort), plus the nightly dreaming cycle

## Key decisions

- **Local model, zero API cost** — this was the central decision everything else derives from. No budget for ongoing per-token costs, so the model had to run entirely on owned hardware, not a subscription.
- **`gog` with a restricted scope** (`gmail,calendar,docs,drive,contacts,sheets`) instead of accepting the CLI's default broad OAuth request (which included AdWords, Classroom, Photos, YouTube — none of it needed).
- **`dreaming.frequency` fixed to `03:00 Europe/Bucharest`** with `verboseLogging: false`, so memory consolidation runs only at night and never leaks its internal output into live chat.

## What's in this repo

| Path | What it is |
|---|---|
| `docs/SETUP.md` | Step-by-step reproduction of this install, written live during the rebuild |
| `docs/TROUBLESHOOTING.md` | Postmortems of the incidents that shaped the config |
| `docs/AGENT_RULES.md` | Behavior rules given to the agent, each traced to a real failure |
| `docs/context-memory-architecture.md` | Design for topic detection, incremental flush, and retrieval |
| `docs/failure-simulation-openclaw-ornith.md` | How this stack degrades over hours/days, and what that implies for build order |
| `src/memory_flush.py` | Incremental disk-flush with atomic writes and semantic search |
| `src/topic_reset_trigger.py` | Layer A of the topic detector — embedding-only, no LLM call |
| `src/cache.py` | Thread-safe TTL + LRU cache |
| `tools/artifact_manager.py` | Local artifact versioning: save, run, serve, roll back |
| `tools/calibrate_*.py`, `tools/compare_embed_models.py` | Threshold and embedding-model calibration — measured, not guessed |
| `config/openclaw.example.json` | Secrets-redacted example config |
| `task_app/` | Small vanilla-JS task tracker |

43 tests pass across `src/` — see [`docs/SETUP.md`](docs/SETUP.md#running-the-tests).

## What broke, and what I learned

This project has gone through at least two full rebuilds. Rather than hide that, here's what went wrong and why:

- **Error-handling loop**: when a tool call failed, the agent wouldn't diagnose cleanly — it would re-reason out loud, contradict itself, and take 250+s instead of under 30s. Full writeup: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).
- **OAuth/`gog` auth flow**: repeated token expiry and a confusing manual re-auth loop. See the same doc.
- **`dreaming` process misconfiguration**: memory consolidation running mid-conversation instead of at night, competing for resources with the live chat model.

## Setup

See [`docs/SETUP.md`](docs/SETUP.md) for how to reproduce this from scratch.

## Stack

- Model: Ornith-1.5:35B (local, via Ollama)
- Language: Python (primary), with some HTML/JS for generated artifacts and local UI tools
- Interface: Telegram
- Google integration: `gog` CLI
- Browser control: CDP (Chromium-based only — Brave/Chrome, not Safari)

## Status

Rebuilt from scratch (v0 → v1) in September 2026, documented as it happened
rather than written up afterward. The gateway runs locally with fully local
inference and embeddings; secrets are out of plaintext; memory search was
caught defaulting to a cloud provider and corrected before any data left the
machine (see [`docs/SETUP.md`](docs/SETUP.md)).

Still open: Telegram channel re-enable, `gog` OAuth with minimal scope,
scheduled backups, and wiring the `src/` memory layer into the live agent loop.

---

*Built and maintained by [Paul], Oradea, Romania.*
