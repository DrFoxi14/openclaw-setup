# SETUP.md — Reproducing this install from scratch

Written live, step by step, during the actual v0 → v1 rebuild (Sept 2026).
Each step below was run and confirmed before moving to the next.

## Prerequisites (already in place before this rebuild)

- macOS (Apple Silicon)
- Ollama installed and running locally, confirmed reachable:
```bash
  curl http://127.0.0.1:11434/api/tags
```
- Required models already pulled:
  - `ornith-1.5:35b-262k` (primary reasoning model)
  - `ornith-1.5:9b` (fallback / candidate for local summarization)
  - `qwen3-embedding:0.6b` (embedding model — chosen over `nomic-embed-text` after calibration, see `tools/compare_embed_models.py`)

## Step 1 — Install Node.js via Homebrew

The OpenClaw install script attempts to install Node itself, but this hung
on the first attempt (no error, no output, no completion — see note below).
Installing Node manually first avoided the issue on retry.

```bash
brew install node
```

Confirmed: Node v26.8.2 installed via Homebrew.

> **Note on the hung install**: the first run of `install.sh` silently
> stalled at "Installing node" with no further output and no process
> running in the background (confirmed with `ps aux`). Cause not fully
> diagnosed — possibly a transient Homebrew/network issue. Installing
> Node as a separate, explicit step before rerunning the installer
> resolved it.

## Step 2 — Run the official OpenClaw installer

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

This detects the OS, installs the OpenClaw npm package, and links the
`openclaw` binary. Confirmed working — installed OpenClaw v2026.9.4.

## Step 3 — Onboarding

```bash
openclaw onboard
```

(the installer offers to launch onboarding automatically at the end of
`install.sh` — no separate command needed if you run onboarding
immediately after install)

Choices made during onboarding, and why:

| Prompt | Choice | Reason |
|---|---|---|
| How would you like to start? | **Custom setup** (not Quick start) | Quick start's "find AI access" flow risks defaulting toward a cloud provider; custom setup keeps every choice (model provider, OAuth scope, dreaming schedule) explicit and local-first |
| Personal-by-default / multi-user disclaimer | **Yes** | This is a single-operator personal agent — no shared inbox, no multi-user lock-down needed |
| Help make OpenClaw better? (telemetry) | **No thanks** | Consistent with this project's privacy stance (see main README) — no data leaves the machine beyond what's strictly required |
| Agent name | **main** | Kept generic/default rather than a custom name, to keep this a clean reference install |
