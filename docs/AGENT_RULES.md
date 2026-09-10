# AGENT_RULES.md — Explicit behavior rules

Rules I've given the agent directly, in response to specific failures I hit. These aren't hypothetical best practices — each one exists because something broke. See [`docs/TROUBLESHOOTING.md`](../docs/TROUBLESHOOTING.md) for the incidents that motivated them.

## Never announce a task as "in progress" without an actual tool call (2026-09-03)

**Never** say a task is "in progress," "running in the background," "give me a few seconds," or similar, **unless the relevant tool has already been called in this exact turn.**

Derived rules:

1. If the tool hasn't been called yet, say so plainly — "Calling the tool now" — and call it immediately, in the same response. Don't defer it to a future turn.
2. **Never** send progress-sounding text without a real `tool_result` confirming actual state. Progress text with no backing result is a false-progress loop.
3. If a tool call is slow or errors out, report the actual blocker plainly (e.g. "Calling the tool now," then the real result) — don't send filler "please wait" messages.

Applies to: file generation (.docx/.txt/.pptx), installs, any task involving external tools or background work.

## Async tasks over 30s → watchdog cron to verify completion (2026-09-03)

For any task expected to take more than 30 seconds (file generation, cron jobs, external tools, installs, etc.), automatically create a separate **watchdog cron job**:

1. Scheduled for **+2 minutes** after the task starts.
2. At +2 min, checks whether the task actually finished (e.g. does the output file exist on disk).
3. If **yes** → stay silent (the task is done; confirm the real result if asked).
4. If **no** → automatically send a message with the **real error**, either retrying the task or reporting the failure — never just repeat the original promise.

The rule as I originally phrased it:
> "For any task you say will take more than 30 seconds, automatically create a watchdog cron at +2min that confirms whether the file was actually created on disk. If not, report the real error explicitly — don't repeat the promise."

Example cron payload:
```json
{
  "name": "watchdog: <task description>",
  "schedule": { "kind": "at", "at": "<ISO +2min>" },
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "Watchdog for <task>: check on disk whether <path> exists.\n- EXISTS → confirm done.\n- DOES NOT EXIST → report the real error and retry/close the task, without repeating the original promise."
  },
  "delivery": { "mode": "announce" }
}
```

Applies to: file generation (.docx/.txt/.pptx), installs (pip/env), any async task announced as taking more than 30s.

## General tool-use rules

- Always run a read/fetch on the target file immediately before editing it, to guarantee an exact match for the edit.
- **Artifacts dynamic link**: when generating HTML files, always check the active tunnel/URL file first, and append the filename to that domain for a shareable link — then also send the `.html` file directly as a backup attachment.
- **Tool efficiency**: don't execute setup steps sequentially. Batch file creation, execution, and verification into a single script/command call where possible.
- **Fast mode**: for routine, low-risk file creation and tool calls, skip verbose internal reasoning and proceed directly to the tool call.
- **Single-tool rule**: for complex tasks (file creation, server setup, networking), write one unified script that performs all steps and outputs the final result, rather than splitting into many small tool calls.

## Artifact rules — mandatory for any generated code or file

I built a small local tool (`artifact_manager.py`) that any "substantial" generated artifact (roughly 10+ lines of code, an interactive HTML/JS file, or anything that produces a visual/data result) must go through — never just pasted raw into chat.

**Rule 1 — Always save through the tool, never write ad hoc**
Write the content to a temp file first, then:
```
python3 <path-to-tools>/artifact_manager.py save <artifact-name> --file /tmp/draft.py --lang py
```
Pick a short, descriptive, space-free name. Reusing the same name for an update creates a new *version*, not a new artifact.

**Rule 2 — For `.py`: don't report success until it's actually run**
After every save of a Python artifact, immediately run:
```
python3 <path-to-tools>/artifact_manager.py run <artifact-name>
```
Read STDOUT/STDERR/EXIT_CODE. If exit code isn't 0: diagnose the error from STDERR, fix the code, save again under the same name, run again. Repeat up to 3 times — if it still doesn't work, report the exact last error to the user instead of claiming success.

**Rule 3 — For `.html`: local preview only, never raw code in chat**
After saving with `--lang html`:
```
python3 <path-to-tools>/artifact_manager.py serve <artifact-name>
```
This returns a `127.0.0.1`-only local URL. It auto-refreshes when the artifact is re-saved under the same name. **Never expose this via ngrok or any public tunnel** — it's local-only, by design.

**Rule 4 — Edits update the same artifact**
A follow-up change ("make it blue", "add a column") edits and re-saves under the same name — it never creates a new artifact.

**Rule 5 — History and rollback**
```
python3 <path-to-tools>/artifact_manager.py log <artifact-name>
python3 <path-to-tools>/artifact_manager.py rollback <artifact-name> [hash]
```
No hash = roll back to the previous version. Always confirm to the user which version it rolled back to.

**Rule 6 — Cleanup**
```
python3 <path-to-tools>/artifact_manager.py stop <artifact-name>   # or: stop all
```
Not mandatory after every artifact, but avoids leaving dozens of local servers running.

**What this is meant to prevent:**
- No raw code dumped into chat when a saved artifact already exists for it.
- No public tunnels (ngrok, cloudflared, etc.) for local artifacts — ever.
- No claiming a script "works" without having actually run it and seen exit code 0.
