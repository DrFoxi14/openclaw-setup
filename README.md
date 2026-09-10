# OpenClaw Setup

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
- **Heartbeat** — periodic background check
- **Scheduled jobs**: email triage (automatic inbox check/sort), plus the nightly dreaming cycle

## Key decisions

- **Local model, zero API cost** — this was the central decision everything else derives from. No budget for ongoing per-token costs, so the model had to run entirely on owned hardware, not a subscription.
- **`gog` with a restricted scope** (`gmail,calendar,docs,drive,contacts,sheets`) instead of accepting the CLI's default broad OAuth request (which included AdWords, Classroom, Photos, YouTube — none of it needed).
- **`dreaming.frequency` fixed to `03:00 Europe/Bucharest`** with `verboseLogging: false`, so memory consolidation runs only at night and never leaks its internal output into live chat.

## What broke, and what I learned

This project has gone through at least two full rebuilds. Rather than hide that, here's what went wrong and why:

- **Error-handling loop**: when a tool call failed, the agent wouldn't diagnose cleanly — it would re-reason out loud, contradict itself, and take 250+s instead of under 30s. Full writeup: [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).
- **OAuth/`gog` auth flow**: repeated token expiry and a confusing manual re-auth loop. See the same doc.
- **`dreaming` process misconfiguration**: memory consolidation running mid-conversation instead of at night, competing for resources with the live chat model.

## Setup

<!-- Trimite spre docs/SETUP.md, nu duplica aici -->
See [`docs/SETUP.md`](docs/SETUP.md) for how to reproduce this from scratch.

## Stack

- Model: Ornith-1.5:35B (local, via Ollama)
- Language: Python (primary), with some HTML/JS for generated artifacts and local UI tools
- Interface: Telegram
- Google integration: `gog` CLI
- Browser control: CDP (Chromium-based only — Brave/Chrome, not Safari)

## Status

This repo is a work in progress: I'm rebuilding the whole setup from scratch (v0 → v1), moving away from an unstructured, undocumented install toward something versioned and properly documented as I go, instead of writing it all up after the fact.

---

*Built and maintained by [Paul], Oradea, Romania.*
