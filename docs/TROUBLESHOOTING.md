# Troubleshooting log

This isn't a list of every bug — it's the incidents that actually shaped design decisions in this setup. Each one led to a concrete config or architecture change.

## Incident 1: "Dreaming" process leaking into live chat

**Symptom**: The memory-core plugin's consolidation process ("dreaming" — light/REM/deep cycles) is supposed to run silently overnight and only update stored memory. Instead, its output started arriving as actual Telegram messages around midday, even though it was configured to run at night.

**Root cause**: `openclaw.json` had `dreaming.enabled: true` but no explicit `frequency` or `timezone`. Without those, the scheduler fell back to a default that didn't match the intended 3 AM run time — and more importantly, there was nothing stopping the process's internal output from being routed to the same channel as normal chat replies.

**Fix**: Set explicit values —
```json
"dreaming": {
  "enabled": true,
  "frequency": "0 3 * * *",
  "timezone": "Europe/Bucharest",
  "verboseLogging": false
}
```
`verboseLogging: false` was the part that actually stopped the leakage — frequency/timezone only fixed *when* it ran, not *whether it talked to me*.

## Incident 2: Heartbeat process "thinking out loud" on Telegram

**Symptom**: Separately from dreaming, the periodic background heartbeat check started sending fully unsolicited messages — visible reasoning/self-talk, not replies to anything I'd asked. These arrived with no prompt from me at all.

**Root cause**: Same class of bug as Incident 1 — a background process whose internal reasoning wasn't being kept internal. Likely the heartbeat's own verbose/debug output wasn't gated the same way chat responses are.

**Fix**: Addressed alongside Incident 1's logging changes. *(Note: exact heartbeat-specific config change to be confirmed/documented during the rebuild — this was fixed live but not fully recorded at the time, which is itself part of why this rebuild insists on documenting changes as they happen, not after.)*

**Open question carried into the rebuild**: after applying the fix, it wasn't clear whether a full gateway restart happened or just a config reload. Going forward: every config change gets a full restart, and that restart gets logged here.

## Incident 3: `gog` OAuth loop (same night as Incidents 1–2)

**Symptom**: Hours after fixing the dreaming/heartbeat leakage, `gog` (the Google OAuth CLI used for Gmail/Calendar/Drive/Docs) started failing with an expired/revoked refresh token. Re-running `gog auth add` triggered a manual OAuth flow that repeatedly broke:
- Initial auth request asked for far more scopes than needed (AdWords, Classroom, Photos, YouTube — none of which this setup uses)
- The agent's own troubleshooting of the failure became unreliable: it reported the account as successfully reconnected, then a few messages later reported the token as expired again, with no explanation for the discrepancy
- Attempting to complete the OAuth flow through the agent's managed browser failed because that browser control is CDP-based (Chrome DevTools Protocol), which only works with Chromium-family browsers (Chrome, Brave) — not Safari

**Root cause (suspected)**: Not fully isolated — plausibly related to the same session instability that caused Incidents 1–2 (a gateway/agent process in a bad state after live config changes without a clean restart). The scope-creep on the OAuth request was a separate, avoidable issue: the `--manual` flow request all available scopes by default instead of the minimal set actually needed.

**Fix**: Re-ran `gog auth add` scoped explicitly to `--services gmail,calendar,docs,drive,contacts,sheets`, avoiding the broad default scope list. Confirmed working with a live read-only call (`gog gmail threads list`) rather than trusting the agent's self-report.

**Lesson carried into the rebuild**: never trust an agent's own "it's fixed now" — verify with an independent, read-only check. And request the minimum OAuth scope needed from the start, not the CLI's default.

## Pattern across all three incidents

All three trace back to the same underlying issue: **background/internal processes (dreaming, heartbeat, tool-error recovery) didn't have a clear boundary between "internal reasoning" and "output the user sees."** When something went wrong internally, the fix wasn't to reason more visibly — it was to reason less visibly and report status more explicitly.

This is the single biggest design principle carried into the rebuild: every background process gets `verboseLogging: false` by default, and any status it needs to report to me goes through a short, explicit message — not raw reasoning.
