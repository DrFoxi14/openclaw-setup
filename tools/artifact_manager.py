#!/usr/bin/env python3
"""
artifact_manager.py — a small local "Claude Artifacts"-style tool for OpenClaw + Ornith.

Commands:
  save     — write a generated file into a versioned (git) artifacts workspace
  run      — run a .py artifact and return stdout/stderr/exit code
  serve    — serve an .html artifact locally (127.0.0.1 only), with live reload
  tunnel   — expose an artifact temporarily via a Cloudflare tunnel, password-protected
  log      — show version history for an artifact
  rollback — restore an artifact to a previous version
  list     — list managed artifacts
  stop     — stop a running server/tunnel

No external dependencies — Python 3 standard library only. Plays well with
OpenClaw's sandbox: everything here is file I/O, a subprocess for `run`,
and an HTTP server bound strictly to 127.0.0.1.

Typical usage (as run by the agent via exec):
  python3 artifact_manager.py save temp_plot --file /tmp/plot.py --lang py
  python3 artifact_manager.py run temp_plot
  python3 artifact_manager.py save color_slider --file /tmp/index.html --lang html
  python3 artifact_manager.py serve color_slider
  python3 artifact_manager.py log color_slider
  python3 artifact_manager.py rollback color_slider [commit-hash]
  python3 artifact_manager.py list
  python3 artifact_manager.py stop color_slider

The artifacts workspace (default: ~/openclaw-artifacts) is a single git repo;
each artifact is a subfolder. Rollback never deletes history — it makes a new
commit that restores that artifact's files to an earlier version, so you can
always move forward and back.
"""

import argparse
import base64
import contextlib
import hmac
import http.server
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ARTIFACTS_HOME = (
    Path(os.environ.get("ARTIFACTS_HOME", Path.home() / "openclaw-artifacts")).resolve()
)
STATE_FILE = ARTIFACTS_HOME / ".state.json"
RUN_TIMEOUT_DEFAULT = 30  # seconds, for .py artifacts
PORT_RANGE = (8765, 8865)

LANG_TO_MAIN = {
    "py": "main.py",
    "html": "index.html",
    "js": "main.js",
    "svg": "main.svg",
    "md": "main.md",
}

RELOAD_SNIPPET = """
<script>
// injected by artifact_manager.py -- local live reload, no build tools
(function () {
  let lastMtime = null;
  async function poll() {
    try {
      const r = await fetch('/__mtime', {cache: 'no-store'});
      const data = await r.json();
      if (lastMtime !== null && data.mtime !== lastMtime) {
        location.reload();
        return;
      }
      lastMtime = data.mtime;
    } catch (e) { /* server briefly down, keep polling */ }
    setTimeout(poll, 800);
  }
  poll();
})();
</script>
"""


# ---------------------------------------------------------------------------
# Basic utilities
# ---------------------------------------------------------------------------

def eprint(*a, **kw):
    print(*a, file=sys.stderr, **kw)


def run_git(args, cwd, allow_empty_fail=False):
    result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0 and not allow_empty_fail:
        eprint(f"[git {' '.join(args)}] {result.stderr.strip()}")
    return result


def ext_name(ext):
    e = str(ext or "").lower()
    known_exts = {"py", "html", "js", "css", "json", "txt", "md", "svg",
                  "png", "jpg", "jpeg", "gif"}
    return e if e[:3] == "htm" or e in known_exts else "txt"


def to_extname(lang, file=None):
    name_parts = []
    if file and "." in str(file):
        name_parts.append(str(file).rsplit(".", 1)[-1].lower())
    elif lang:
        name_parts.append(str(lang).lstrip("."))
    return ext_name(name_parts[-1] if name_parts else "txt")


def ensure_workspace():
    ARTIFACTS_HOME.mkdir(parents=True, exist_ok=True)
    if not (ARTIFACTS_HOME / ".git").exists():
        run_git(["init"], cwd=ARTIFACTS_HOME)
        run_git(["config", "user.email", "artifact-manager@local"], cwd=ARTIFACTS_HOME)
        run_git(["config", "user.name", "artifact-manager"], cwd=ARTIFACTS_HOME)
        (ARTIFACTS_HOME / ".gitignore").write_text(".state.json\n*.pid\n*.log\n")
        run_git(["add", "-A"], cwd=ARTIFACTS_HOME)
        run_git(
            ["commit", "-m", "init: artifacts workspace"],
            cwd=ARTIFACTS_HOME,
            allow_empty_fail=True,
        )


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def artifact_dir(name):
    safe = "".join(c for c in name if c.isalnum() or c in "-_").strip("-_")
    if not safe:
        eprint("Invalid artifact name.")
        sys.exit(1)
    return ARTIFACTS_HOME / safe, safe


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def find_free_port():
    for port in range(*PORT_RANGE):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    eprint("No free port in the reserved preview range.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

def cmd_save(args):
    ensure_workspace()
    d, safe = artifact_dir(args.name)
    d.mkdir(parents=True, exist_ok=True)

    if args.file:
        src = Path(args.file)
        if not src.exists():
            eprint(f"Source file does not exist: {src}")
            sys.exit(1)
        content = src.read_text(errors="replace")
    else:
        content = sys.stdin.read()

    ext = to_extname(args.lang, args.file)
    main_name = LANG_TO_MAIN.get(ext, f"main.{ext}")
    target = d / main_name
    is_new = not target.exists()
    target.write_text(content)

    run_git(["add", "-A"], cwd=ARTIFACTS_HOME)
    verb = "create" if is_new else "update"
    msg = f"{verb} {safe}/{main_name}"
    if args.message:
        msg += f" — {args.message}"
    commit = run_git(["commit", "-m", msg], cwd=ARTIFACTS_HOME, allow_empty_fail=True)

    print(f"OK: saved {target}")
    if commit.returncode == 0:
        h = run_git(["rev-parse", "--short", "HEAD"], cwd=ARTIFACTS_HOME).stdout.strip()
        print(f"COMMIT: {h}")
    else:
        print("COMMIT: (no content change since last version)")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def cmd_run(args):
    d, safe = artifact_dir(args.name)
    target = d / "main.py"
    if not target.exists():
        eprint(f"No main.py for artifact '{safe}'. Run 'save' first.")
        sys.exit(1)

    print(f"RUNNING: {target} (timeout {args.timeout}s)")
    try:
        result = subprocess.run(
            [sys.executable, str(target)],
            cwd=d,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"EXIT_CODE: timeout after {args.timeout}s — script ran too long or hung.")
        sys.exit(2)

    (d / "last_run_stdout.log").write_text(result.stdout)
    (d / "last_run_stderr.log").write_text(result.stderr)

    if result.stdout:
        print("--- STDOUT ---")
        print(result.stdout.rstrip())
    if result.stderr:
        print("--- STDERR ---")
        print(result.stderr.rstrip())
    print(f"EXIT_CODE: {result.returncode}")

    ensure_workspace()
    run_git(["add", "-A"], cwd=ARTIFACTS_HOME)
    status = "success" if result.returncode == 0 else f"error (code {result.returncode})"
    run_git(
        ["commit", "-m", f"run {safe}: {status}"],
        cwd=ARTIFACTS_HOME,
        allow_empty_fail=True,
    )

    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# serve — local-only live preview for .html artifacts
# ---------------------------------------------------------------------------

class ReloadingHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep stdout clean for the calling agent

    def _artifact_name(self):
        return Path(self.directory).name

    def _auth_ok(self):
        # Password required only if this artifact currently has an active
        # public tunnel (checked live against state.json, not at server
        # startup) — plain 127.0.0.1 access with no tunnel never prompts.
        state = load_state()
        entry = state.get(self._artifact_name())
        if not entry or not entry.get("tunnel"):
            return True

        token = entry.get("token", "")
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode()
            _, _, supplied = decoded.partition(":")
        except Exception:
            return False
        return hmac.compare_digest(supplied, token)

    def _require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="protected artifact"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        body = (
            "This artifact is exposed via a public tunnel — enter the password "
            "printed by artifact_manager.py tunnel.".encode()
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._auth_ok():
            self._require_auth()
            return

        if self.path == "/__mtime":
            target = Path(self.directory) / "index.html"
            mtime = target.stat().st_mtime if target.exists() else 0
            body = json.dumps({"mtime": mtime}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")

        if path.endswith(".html") and os.path.exists(path):
            html = Path(path).read_text(errors="replace")
            if "__mtime" not in html:
                if "</body>" in html:
                    html = html.replace("</body>", RELOAD_SNIPPET + "</body>")
                else:
                    html += RELOAD_SNIPPET
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        return super().do_GET()


def _serve_forever(directory, port):
    handler = lambda *a, **kw: ReloadingHandler(*a, directory=str(directory), **kw)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.serve_forever()


def _clean_dead_entry(state, safe):
    # If a saved pid is dead, clear the entry but keep the token if one
    # existed, so an old tunnel death doesn't silently invalidate a password
    # someone might still be holding onto.
    entry = state.get(safe)
    if entry and entry.get("pid") and not pid_alive(entry["pid"]):
        token = entry.get("token")
        state[safe] = {"token": token} if token else {}
        save_state(state)
    tun = state.get(safe, {}).get("tunnel")
    if tun and tun.get("pid") and not pid_alive(tun["pid"]):
        state[safe].pop("tunnel", None)
        save_state(state)


def ensure_server_running(name, requested_port=None):
    d, safe = artifact_dir(name)
    target = d / "index.html"
    if not target.exists():
        eprint(f"No index.html for artifact '{safe}'. Run 'save' with --lang html first.")
        sys.exit(1)

    state = load_state()
    _clean_dead_entry(state, safe)
    state = load_state()
    entry = state.get(safe)
    if entry and entry.get("pid") and pid_alive(entry["pid"]):
        return entry["port"], d, safe

    port = requested_port or find_free_port()
    token = (entry or {}).get("token") or secrets.token_urlsafe(6)
    log_path = d / "preview_server.log"

    # Double-fork to fully detach the server process from the caller's
    # terminal/pipe — otherwise the agent's exec call would hang waiting
    # for the output streams to close.
    first_pid = os.fork()
    if first_pid > 0:
        os.waitpid(first_pid, 0)
        # State is written by the detached grandchild only, to avoid a race
        # where this process and the grandchild both write pid info at once.
        time.sleep(0.4)
        return port, d, safe

    os.setsid()
    second_pid = os.fork()
    if second_pid > 0:
        os._exit(0)  # first child exits; grandchild is now a detached orphan

    devnull_fd = os.open(os.devnull, os.O_RDWR)
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.dup2(devnull_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)

    real_pid = os.getpid()
    st = load_state()
    st[safe] = {"port": port, "pid": real_pid, "dir": str(d), "token": token}
    save_state(st)

    try:
        _serve_forever(d, port)
    except OSError:
        pass
    os._exit(0)


def cmd_serve(args):
    port, d, safe = ensure_server_running(args.name, args.port)
    print(f"URL: http://127.0.0.1:{port}/ (live — reloads automatically on save)")


TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def _spawn_tunnel_watchdog(safe, tunnel_pid, expires_at):
    # Detached process that kills the tunnel on its own after expiry, even
    # if the tunnel is never stopped manually. Only touches the tunnel,
    # never the local preview server.
    first_pid = os.fork()
    if first_pid > 0:
        os.waitpid(first_pid, 0)
        return
    os.setsid()
    second_pid = os.fork()
    if second_pid > 0:
        os._exit(0)

    devnull_fd = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull_fd, 0)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)

    remaining = expires_at - time.time()
    if remaining > 0:
        time.sleep(remaining)

    state = load_state()
    entry = state.get(safe, {})
    tun = entry.get("tunnel")
    # Only act if this is still the tunnel we're watching (not one the
    # user restarted since) and it hasn't already been stopped manually.
    if tun and tun.get("pid") == tunnel_pid:
        if pid_alive(tunnel_pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(tunnel_pid, signal.SIGTERM)
        entry.pop("tunnel", None)
        state[safe] = entry
        save_state(state)
    os._exit(0)


def cmd_tunnel(args):
    if shutil.which("cloudflared") is None:
        eprint(
            "cloudflared is not installed. On macOS: 'brew install cloudflared' "
            "(free, official from Cloudflare, one-time setup)."
        )
        sys.exit(1)

    port, d, safe = ensure_server_running(args.name)
    state = load_state()
    existing = state.get(safe, {}).get("tunnel")
    if existing and existing.get("pid") and pid_alive(existing["pid"]) and existing.get("url"):
        remaining_min = max(0, int((existing.get("expires_at", 0) - time.time()) / 60))
        print(f"PUBLIC URL: {existing['url']}")
        print(f"PASSWORD: {state[safe]['token']} (required once per browser)")
        print(f"Expires automatically in ~{remaining_min} min. Extend with --minutes.")
        return

    log_path = d / "tunnel.log"
    log_path.write_text("")
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    url = None
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(0.5)
        text = log_path.read_text(errors="ignore")
        m = TUNNEL_URL_RE.search(text)
        if m:
            url = m.group(0)
            break

    if not url:
        eprint(
            f"cloudflared did not respond within {args.timeout}s. Check {log_path} "
            "for details, or try again (Cloudflare's free tunnel isn't an uptime "
            "guarantee, just occasionally slow to start)."
        )
        sys.exit(1)

    expires_at = time.time() + args.minutes * 60
    state = load_state()
    state[safe]["tunnel"] = {
        "pid": proc.pid,
        "url": url,
        "started": time.time(),
        "expires_at": expires_at,
    }
    save_state(state)
    _spawn_tunnel_watchdog(safe, proc.pid, expires_at)

    print(f"PUBLIC URL: {url}")
    print(f"PASSWORD: {state[safe]['token']} (Basic Auth, asked once per browser)")
    print(f"Closes itself in {args.minutes} minutes, even if you forget about it.")
    print("Note: anyone with the link and the password can reach it while the tunnel is up.")
    print(f"Stop it early with: python3 artifact_manager.py stop {safe}")


def cmd_stop(args):
    state = load_state()
    if args.name == "all":
        targets = list(state.keys())
    else:
        _, safe = artifact_dir(args.name)
        targets = [safe] if safe in state else []

    if not targets:
        print("Nothing to stop.")
        return

    for safe in targets:
        entry = state.pop(safe)
        if entry.get("pid") and pid_alive(entry["pid"]):
            with contextlib.suppress(ProcessLookupError):
                os.kill(entry["pid"], signal.SIGTERM)
        tun = entry.get("tunnel")
        if tun and tun.get("pid") and pid_alive(tun["pid"]):
            with contextlib.suppress(ProcessLookupError):
                os.kill(tun["pid"], signal.SIGTERM)
        print(f"Stopped preview{' + tunnel' if tun else ''} for {safe}.")
    save_state(state)


# ---------------------------------------------------------------------------
# log / rollback / list
# ---------------------------------------------------------------------------

def cmd_log(args):
    ensure_workspace()
    d, safe = artifact_dir(args.name)
    result = run_git(["log", "--oneline", "--", safe], cwd=ARTIFACTS_HOME)
    out = result.stdout.strip()
    if not out:
        print(f"No history for '{safe}' yet.")
        return
    print(out)


def cmd_rollback(args):
    ensure_workspace()
    d, safe = artifact_dir(args.name)

    if args.commit:
        commit = args.commit
    else:
        log = run_git(["log", "--format=%H", "--", safe], cwd=ARTIFACTS_HOME).stdout.split()
        if len(log) < 2:
            eprint(f"No previous version of '{safe}' to roll back to.")
            sys.exit(1)
        commit = log[1]  # second entry = previous version (first is current)

    checkout = run_git(["checkout", commit, "--", safe], cwd=ARTIFACTS_HOME)
    if checkout.returncode != 0:
        eprint(f"Rollback failed: hash '{commit}' doesn't exist or doesn't touch '{safe}'.")
        sys.exit(1)

    run_git(["add", "-A"], cwd=ARTIFACTS_HOME)
    run_git(
        ["commit", "-m", f"rollback {safe} -> {commit[:7]}"],
        cwd=ARTIFACTS_HOME,
        allow_empty_fail=True,
    )
    print(f"OK: rolled back {safe} to {commit[:7]}.")


def cmd_list(args):
    ensure_workspace()
    state = load_state()
    if not state:
        print("No managed artifacts yet.")
        return
    for safe, entry in sorted(state.items()):
        status = ""
        if entry.get("pid"):
            alive = "✓" if pid_alive(entry["pid"]) else "✗"
            port = entry.get("port", "?")
            status = f"[server {alive} :{port}]"
        if (entry or {}).get("tunnel"):
            status += " [tunnel active]"
        print(f"- {safe}{status}")


def main():
    parser = argparse.ArgumentParser(description="Local artifact manager for OpenClaw + Ornith.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_save = sub.add_parser("save", help="Save a versioned artifact file.")
    p_save.add_argument("name")
    p_save.add_argument("--file", help="File to save (defaults to stdin).")
    p_save.add_argument("--lang", default="py", help="extension: py|html|js|svg|md")
    p_save.add_argument("--message", help="Extra commit message text.")
    p_save.set_defaults(func=cmd_save)

    p_run = sub.add_parser("run", help="Run the artifact's main.py.")
    p_run.add_argument("name")
    p_run.add_argument("--timeout", type=int, default=RUN_TIMEOUT_DEFAULT)
    p_run.set_defaults(func=cmd_run)

    p_serve = sub.add_parser("serve", help="Start the local server for an .html artifact.")
    p_serve.add_argument("name")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(func=cmd_serve)

    p_tunnel = sub.add_parser("tunnel", help="Expose the local preview via a temporary Cloudflare tunnel.")
    p_tunnel.add_argument("name")
    p_tunnel.add_argument("--minutes", type=int, default=60)
    p_tunnel.add_argument("--timeout", type=int, default=120)
    p_tunnel.set_defaults(func=cmd_tunnel)

    p_stop = sub.add_parser("stop", help="Stop the server/tunnel for an artifact (or 'all').")
    p_stop.add_argument("name")
    p_stop.set_defaults(func=cmd_stop)

    p_log = sub.add_parser("log", help="Show an artifact's git history.")
    p_log.add_argument("name")
    p_log.set_defaults(func=cmd_log)

    p_rb = sub.add_parser("rollback", help="Roll an artifact back to a previous version.")
    p_rb.add_argument("name")
    p_rb.add_argument("--commit", help="Commit hash (defaults to the previous version).")
    p_rb.set_defaults(func=cmd_rollback)

    p_list = sub.add_parser("list", help="List managed artifacts.")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
