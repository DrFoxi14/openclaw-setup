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

### Model provider setup

| Prompt | Choice | Reason |
|---|---|---|
| How should I set things up? | **Ask first** (not Full access) | Avoid automatic scanning/use of any pre-existing API keys on the machine |
| May I look around to find your AI access? | Yes, take a look | Safe at this point — "Ask first" was already selected, so this only *shows* detected options, doesn't activate them |
| Model/auth provider | **Ollama** (via "More…" — not shown in the top-level list) | Only fully local, zero-cost option; matches `config/openclaw.example.json` |
| Ollama mode | **Local only** (auto-selected) | Explicitly avoids Ollama's cloud tier |
| Ollama base URL | `http://127.0.0.1:11434` (default, unchanged) | Confirmed reachable earlier via `curl` |

> **Note**: onboarding auto-verified inference against `qwen2.5-coder:3b`
> (whatever was available), not the intended primary model. Corrected
> manually afterward in the dashboard — see next section — to
> `ornith-1.5:35b-262k` as primary, `ornith-1.5:9b` as fallback.

### Fallback behavior — how it actually works (researched, not assumed)

Contrary to an initial assumption, fallback models are **not** loaded
simultaneously with the primary — OpenClaw only invokes a fallback when
the primary request actually fails, in this order:
1. Auth-profile rotation within the current provider
2. Model fallback to the next entry in `agents.defaults.model.fallbacks`

Ollama itself also loads/unloads models on demand rather than keeping
everything resident, which further reduces (but doesn't eliminate) the
RAM-overlap risk between `ornith-1.5:35b-262k` (primary) and the two
local fallbacks (`ornith-1.5:9b`, `muse-glimmer:30b-mlx`).

**Known related issue (OpenClaw #28927, older versions)**: local Ollama
fallbacks can sometimes hit a false "No API key found for provider
ollama" error during failover, even though the provider is local and
needs no auth. Not yet tested against this install — flagged to verify
before relying on fallback in production.

Fallback testing itself was deliberately deferred to a later session —
not required for this initial setup pass.

### Result

- Gateway running at `ws://127.0.0.1:18789`
- LaunchAgent installed: `~/Library/LaunchAgents/ai.openclaw.gateway.plist` (auto-starts on login)
- Workspace: `~/.openclaw/workspace`
- Sessions: `~/.openclaw/agents/main/sessions`
- Logs: `~/Library/Logs/openclaw/gateway.log`

## Memory-core configuration (2026-09-17)

### What went wrong first, and why

Initial attempt used `plugins.entries.memory-core.config.dreaming.mode: "core"`,
based on official multi-language documentation. This was rejected by the
locally installed schema (`additionalProperties: false` on `dreaming`, no
`mode` field). Extracted the real schema directly from the running install
with `openclaw config schema --json`, which confirmed the actual valid
shape matches the original architecture from `context-memory-architecture.md`:
`enabled`, `frequency` (cron expression), `timezone`, `verboseLogging`, plus
granular `phases.{light,deep,rem}` overrides. Lesson: for a fast-moving
project, trust the locally installed schema over general documentation,
which can lag behind or vary by version.

### Final working config

```json
"plugins": {
  "entries": {
    "memory-core": {
      "enabled": true,
      "config": {
        "dreaming": {
          "enabled": true,
          "frequency": "0 3 * * *",
          "timezone": "Europe/Bucharest",
          "verboseLogging": false
        }
      }
    }
  }
},
"memory": {
  "search": {
    "provider": "ollama",
    "model": "qwen3-embedding:0.6b",
    "remote": {
      "baseUrl": "http://127.0.0.1:11434"
    }
  }
}
```

### Critical catch: default embedding provider was OpenAI, not Ollama

`openclaw memory status --agent main` initially showed
`Provider: openai, Model: text-embedding-3-small` — memory search defaults
to a cloud provider and does NOT auto-detect Ollama, even when every other
part of the config (chat model, `dreaming`) is fully local. This directly
contradicts the project's zero-API-cost, privacy-first design and would
have silently sent data to OpenAI (and incurred cost) on every memory
search or reindex. Caught before any reindex ran — corrected to
`provider: "ollama"` with `qwen3-embedding:0.6b` (the model this project
already calibrated as the better fit for Romanian-language text — see
`tools/compare_embed_models.py`).

**Lesson for the rebuild**: never assume a plugin's default matches the
project's local-only stance — verify with `memory status` (or equivalent)
before considering a subsystem "done."

### Config path also changed vs. older docs

`openclaw doctor --fix` auto-migrated `agents.defaults.memorySearch` →
`memory.search` — the older key is deprecated in this version. Doctor
handled this migration correctly and non-destructively.

### Verification

```bash
openclaw memory status --index --agent main
```

Confirmed: `Provider: ollama`, `Embeddings: ready`, `Vector store: indexed`,
`Dirty: no`. Fully local, zero API cost, dreaming scheduled correctly.

## Migrating secrets out of plaintext (2026-09-17)

`openclaw doctor` flagged two plaintext secrets in `openclaw.json`:
`gateway.auth.token` and `models.providers.ollama.apiKey`.

### Process (learned by reading `--help` at each step, not guessing)

```bash
# 1. Store the value (kind=secret is write-only, never revealed later)
echo -n "<value>" > /tmp/secret.txt
openclaw secrets store set SOME_NAME --kind secret --value-file /tmp/secret.txt
rm /tmp/secret.txt

# 2. Manually replace the plaintext value in openclaw.json with a reference:
#    "token": { "source": "store", "provider": "default", "id": "SOME_NAME" }

# 3. Validate, restart, reload, verify
openclaw config validate
openclaw gateway restart
openclaw secrets reload
openclaw secrets audit --check
```

Note: `openclaw secrets configure` (the interactive wizard) expects the
store entry to already exist — running it before `secrets store set`
fails with "Secret store entry ... was not found." Store first, then
either use the wizard or edit the JSON reference directly (faster once
you know the reference shape).

Store IDs must match `/^[A-Z][A-Z0-9_]{0,127}$/` — uppercase letters,
digits, underscore only (no hyphens).

### Result

```bash
openclaw secrets audit --check
# Secrets audit: clean. plaintext=0, unresolved=0, shadowed=0, storeResidue=0, legacy=0.
```

Both `gateway.auth.token` and `models.providers.ollama.apiKey` are now
SecretRefs backed by the local SQLite secret store (`kind: secret`,
write-only — never exposed via list/get, only resolved internally at
runtime).


## Running the tests

The Python modules in `src/` have a pytest suite. They depend on nothing
outside the standard library, but pytest itself lives in a project-local
virtual environment rather than the system Python.

```bash
cd ~/Projects/openclaw-setup
python3 -m venv .venv
source .venv/bin/activate
pip install pytest
```

`.venv/` is gitignored — it never leaves this machine.

Run the suite:

```bash
PYTHONPATH=src python3 -m pytest src/ -q
```

`PYTHONPATH=src` is required: the tests import `memory_flush` and
`topic_reset_trigger` as top-level modules, not as a package.

> **Every new shell needs the environment activated again** —
> `source .venv/bin/activate`. Without it the commands above run against
> the system Python, which has no pytest, and the failure reads as a
> missing-module error rather than "you forgot to activate." If the
> prompt doesn't show `(.venv)`, the tests are not running.

Expected: 43 passed (9 cache, 21 memory flush, 13 topic reset trigger).
