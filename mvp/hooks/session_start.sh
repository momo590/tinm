#!/bin/bash
# TINM SessionStart hook — auto-load the current thread's context block at
# the beginning of every Claude Code session.
#
# Stdout from a SessionStart hook is added to Claude's context (per
# https://code.claude.com/docs/en/hooks). We use that to surface the
# trajectory + artifacts so Claude has the user's prior work in mind from
# turn 1 onwards. If no current thread is set, we exit quietly so the
# session is unchanged.

set -e

# TINM paths — honor TINM_HOME / TINM_PCP_DIR for Phase 2 multi-host
# setups (Mac<->VPS via Syncthing-over-Tailscale). Defaults match the
# Phase 1 single-host layout. current_thread + venv stay under TINM_HOME
# (machine-local); the PCP store goes under TINM_PCP_DIR.
TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

CURRENT_FILE="$TINM_HOME/current_thread"
VENV_PY="$TINM_HOME/.venv/bin/python"
LOAD_SCRIPT="$HOME/.claude/skills/tinm/tinm_load.py"
AUTO_INIT_SCRIPT="$HOME/.claude/skills/tinm/tinm_auto_init.py"

# Phase 2 sync (best-effort, v0.2.2): if the PCP store is a git repo, pull
# the latest snapshot from the remote so this host's session starts with the
# freshest threads/artifacts the peer pushed.
#
# v0.2.2 fix: --autostash only stashes TRACKED modifications. When Mac and
# VPS independently create the same file path, pulling fails with
# "untracked working tree files would be overwritten by checkout" — the
# error was being silenced by 2>/dev/null and the user saw stale state.
# Switch to explicit stash --include-untracked, log everything to a per-host
# sync log, and reset working tree on pop conflict so the session is never
# blocked. Failure is still non-fatal — a dropped network or transient
# remote error must not block the session.
if [ -d "$TINM_PCP_DIR/.git" ]; then
    _SYNC_LOG="$TINM_HOME/sync-$(hostname).log"
    _SYNC_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    {
        echo "=== $_SYNC_TS session_start sync from $(hostname) ==="
        _STASHED=0
        if [ -n "$(git -C "$TINM_PCP_DIR" status --porcelain 2>/dev/null)" ]; then
            git -C "$TINM_PCP_DIR" stash push --include-untracked \
                -m "tinm-session-start-autostash-$_SYNC_TS" --quiet 2>&1 \
                && _STASHED=1 || true
        fi
        if git -C "$TINM_PCP_DIR" pull --rebase --quiet 2>&1; then
            _PULL_OK=1
        else
            _PULL_OK=0
            echo "WARN: pull failed; aborting rebase if active"
            git -C "$TINM_PCP_DIR" rebase --abort 2>&1 || true
        fi
        if [ "$_STASHED" = "1" ]; then
            if ! git -C "$TINM_PCP_DIR" stash pop --quiet 2>&1; then
                echo "WARN: stash pop conflict; resetting working tree, work preserved in stash list"
                git -C "$TINM_PCP_DIR" checkout -- . 2>&1 || true
                git -C "$TINM_PCP_DIR" clean -fd 2>&1 || true
                git -C "$TINM_PCP_DIR" stash list 2>&1 | head -1
            fi
        fi
        echo "=== exit (pull_ok=$_PULL_OK stashed=$_STASHED) ==="
    } >> "$_SYNC_LOG" 2>&1 || true
fi

# Gstack-style update check (best-effort, 24h cached, silent on error).
# Prints a one-line notice into Claude's context if a newer version of
# TINM has shipped. Controlled by ~/.tinm/config.json:update_notify
# (default true) and auto_upgrade (default false).
if [ -x "$VENV_PY" ]; then
    "$VENV_PY" - << 'PYEOF' 2>/dev/null || true
import os, sys, pathlib, subprocess
sys.path.insert(0, str(pathlib.Path.home() / ".claude" / "skills" / "tinm"))
try:
    from tinm_config import get_config
    from tinm_update_check import check_for_update
    cfg = get_config()
    if not cfg.get("update_notify", True):
        sys.exit(0)
    r = check_for_update()
    if not r.get("has_update"):
        sys.exit(0)
    current, latest = r.get("current"), r.get("latest")
    if cfg.get("auto_upgrade", False):
        upgrade_script = pathlib.Path.home() / ".claude" / "skills" / "tinm" / "tinm_upgrade.py"
        subprocess.Popen(
            [sys.executable, str(upgrade_script)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"⏫ TINM v{latest} available — auto-upgrading in background.")
    else:
        print(
            f"⏫ TINM update available: v{current} → v{latest}. "
            f"Run `python ~/.claude/skills/tinm/tinm_upgrade.py` to apply."
        )
except Exception:
    pass
PYEOF
fi

# Auto-init: if no current thread on this host but we are inside a git
# repo, derive a slug from the repo's basename and create / select a
# thread silently. Lets the user skip `/tinm init` entirely on new
# projects. No-op if current_thread is already set, or if we are not in
# a git repo. Stderr is dropped so any diagnostic chatter does not leak
# into Claude's context.
if [ -x "$VENV_PY" ] && [ -r "$AUTO_INIT_SCRIPT" ]; then
    "$VENV_PY" "$AUTO_INIT_SCRIPT" 2>/dev/null || true
fi

# No current thread → nothing to inject, exit cleanly so Claude Code does
# not show an error.
[ -r "$CURRENT_FILE" ] || exit 0
THREAD_ID="$(tr -d '[:space:]' < "$CURRENT_FILE")"
[ -n "$THREAD_ID" ] || exit 0

# Venv must be installed before this hook can do anything useful. Fail
# silently rather than spam every session if the user has not finished
# install yet.
[ -x "$VENV_PY" ] || exit 0
[ -r "$LOAD_SCRIPT" ] || exit 0

# Run the loader and let its markdown stdout enter Claude's context.
# Stderr is dropped so warnings (e.g., LibreSSL noise) do not pollute.
"$VENV_PY" "$LOAD_SCRIPT" "$THREAD_ID" 2>/dev/null

# Auto-journal: append a session_start entry to pcp/journal-<hostname>.jsonl.
# Per-host file (not journal.jsonl) avoids the Mac↔VPS git race when both
# ends append concurrently. Reads in tinm_journal.py glob journal-*.jsonl
# to reconstruct the cross-host stream.
"$VENV_PY" - "$THREAD_ID" "$TINM_PCP_DIR" << 'PYEOF' 2>/dev/null || true
import json, sys, datetime, pathlib, socket
thread_id, pcp_dir = sys.argv[1], sys.argv[2]
journal_path = pathlib.Path(pcp_dir) / f"journal-{socket.gethostname().replace('.', '-')}.jsonl"
try:
    t = json.loads((pathlib.Path(pcp_dir) / "threads" / f"{thread_id}.json").read_text())
    turns = len([x for x in t.get("trajectory", []) if x.get("role") == "user"])
    anchor = t.get("anchor", {}).get("top_terms", [])[:4]
except Exception:
    turns, anchor = 0, []
try:
    a = json.loads((pathlib.Path(pcp_dir) / "artifacts" / f"{thread_id}.json").read_text())
    n_art = len(a)
except Exception:
    n_art = 0
entry = {
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "thread": thread_id, "turns": turns, "anchor": anchor,
    "artifacts": n_art, "event": "session_start"
}
with open(journal_path, "a") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PYEOF
