#!/bin/bash
# TINM SessionStart hook — auto-load the current thread's context block at
# the beginning of every Claude Code session.
#
# v0.3.0 hot-path refactor: this shell wrapper now only does
#   1. Resolve TINM_HOME / TINM_PCP_DIR + verify venv.
#   2. Best-effort `git pull` of the Phase-2 PCP sync repo (kept here
#      because it is network I/O and shells out to git anyway; logged to
#      the per-host sync log, never blocks the session).
#   3. Hand stdin (the Claude Code JSON payload) to
#      `_hot_path_session_start.py`, which fans out to upgrade-notif
#      check, thread resolution + freeze, writable gate, tinm_load, and
#      journal append in a single Python process.
#   4. Spawn the clipboard daemon if the user opted in.
#
# Per https://code.claude.com/docs/en/hooks the hook's stdout is added
# to Claude's context — that is where the loaded thread block lands.
#
# ── v0.2.3 thread-isolation (DEC-2 freeze-at-start) ──────────────────────
# The python runner resolves the thread ONCE here and writes
#   ${TINM_HOME}/session-<session_id>.thread
# so user_prompt.sh and stop.sh — separate hook invocations — read the
# frozen thread without re-resolving. cd'ing mid-session does NOT switch
# threads.

set -e

# Read stdin once. SessionStart receives JSON (session_id, transcript_path)
# we need to key the per-session handoff file. Empty stdin (manual test)
# falls back to $$ inside the runner.
PAYLOAD_JSON=""
if [ ! -t 0 ]; then
    PAYLOAD_JSON="$(cat)"
fi

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

VENV_PY="$TINM_HOME/.venv/bin/python"
SKILL_DIR="$HOME/.claude/skills/tinm"
HOT_PATH="$SKILL_DIR/_hot_path_session_start.py"
CLIPBOARD_SCRIPT="$SKILL_DIR/tinm_clipboard.py"

# Venv guard — see install.sh for the rationale.
[ -x "$VENV_PY" ] || exit 0
[ -r "$HOT_PATH" ] || exit 0

# Phase 2 sync (best-effort, v0.2.2): if the PCP store is a git repo, pull
# the latest snapshot from the remote so this host's session starts with the
# freshest threads/artifacts the peer pushed.
#
# v0.2.2 fix: --autostash only stashes TRACKED modifications. When two
# hosts independently create the same file path, pulling fails with
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

# Single Python invocation. stdin (the Claude Code payload) is piped
# straight through; stdout becomes injected context.
printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" "$HOT_PATH" 2>/dev/null || true

# Clipboard watcher auto-start (opt-in via ~/.tinm/clipboard_enabled flag).
# Detached background daemon — survives this hook's exit. No-op if not
# opted in or already running. Privacy: the trigger phrase requirement
# means clipboard content is never captured without explicit user intent.
if [ -r "$CLIPBOARD_SCRIPT" ] && [ -f "$TINM_HOME/clipboard_enabled" ]; then
    "$VENV_PY" "$CLIPBOARD_SCRIPT" --ensure-daemon 2>/dev/null &
fi

exit 0
