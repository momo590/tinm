#!/bin/bash
# TINM Stop hook — buffer the assistant's final text for the next
# UserPromptSubmit to score (approval / rejection / neutral).
#
# Per https://code.claude.com/docs/en/hooks, the Stop hook receives a JSON
# payload on stdin with session_id + transcript_path. We extract the final
# assistant turn text and persist it to ~/.tinm/buffer/<host>-<session>.json
# (single-slot per session, ephemeral, machine-local).
#
# Spec ref: design/root-tinm-design-20260514-approval-weighted-capture.md §2.4
#
# ── v0.2.3 thread-isolation (DEC-2 freeze-at-start) ──────────────────────
# THREAD_ID is read from the per-session handoff file
# ~/.tinm/session-<session_id>.thread that SessionStart wrote. We finalise
# whatever thread that file points at — never re-derive from cwd.
# After this hook completes, we delete the handoff file so the dir does
# not accumulate stale entries.
#
# Fallback: legacy ~/.tinm/current_thread for sessions that started under
# v0.2.2 (no per-session handoff file). Log one warning to the per-host
# hook-warn log so peer-host upgrade lag is visible.
# ─────────────────────────────────────────────────────────────────────────
#
# ── 2026-05-21 fix: heredoc-on-stdin bug ─────────────────────────────────
# Two `python - << PYEOF` blocks used to do the heavy lifting inline. That
# pattern is broken: `python -` reads the SCRIPT from stdin, so the
# heredoc IS the stdin, and the piped JSON payload was silently
# discarded. As a result write_buffer() NEVER ran in production — there
# were zero buffer files on disk despite the hook firing thousands of
# times. Repro:
#     $ echo '{"foo":1}' | python3 - << 'PYEOF'
#       import sys; print(repr(sys.stdin.read()))
#       PYEOF
#     ''   # the JSON is gone
# Logic now lives in two standalone scripts in this directory:
#   _stop_extract_session.py    payload-JSON → session_id
#   _stop_capture_assistant.py  payload-JSON + thread_id + session_id
#                               → walks transcript, calls write_buffer
# ─────────────────────────────────────────────────────────────────────────

set -e

PAYLOAD_JSON="$(cat)"
echo "[$(date -u +%FT%TZ)] stop-hook fired" >> /tmp/tinm_hook.log

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

VENV_PY="$TINM_HOME/.venv/bin/python"
SKILL_DIR="$HOME/.claude/skills/tinm"
HOOKS_DIR="$(cd "$(dirname "$0")" && pwd)"
CAPTURE_SCRIPT="$SKILL_DIR/tinm_assistant_capture.py"
EXTRACT_SESSION_PY="$HOOKS_DIR/_stop_extract_session.py"
CAPTURE_ASSISTANT_PY="$HOOKS_DIR/_stop_capture_assistant.py"

# Expose the skill dir so _stop_capture_assistant.py can find
# tinm_assistant_capture even when invoked from a non-standard hook path
# (e.g., in the test harness where hooks live under a tempdir but the
# skill is symlinked to $HOME/.claude/skills/tinm).
export TINM_SKILL_DIR="$SKILL_DIR"

LEGACY_CURRENT_FILE="$TINM_HOME/current_thread"
HOOK_WARN_LOG="$TINM_HOME/hook-warn-$(hostname | tr '.' '-').log"

[ -x "$VENV_PY" ] || exit 0
[ -r "$CAPTURE_SCRIPT" ] || exit 0
[ -r "$EXTRACT_SESSION_PY" ] || exit 0
[ -r "$CAPTURE_ASSISTANT_PY" ] || exit 0

# Extract session_id early so we can locate the per-session handoff file.
# (Standalone script — see _stop_extract_session.py for why we cannot use
# `python - << HEREDOC` here.)
SESSION_ID="$(printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" "$EXTRACT_SESSION_PY" 2>>/tmp/tinm_hook.log)"

# ── DEC-2: read frozen thread name from per-session handoff file ─────────
THREAD_ID=""
SESSION_THREAD_FILE=""
if [ -n "$SESSION_ID" ]; then
    SESSION_THREAD_FILE="$TINM_HOME/session-${SESSION_ID}.thread"
    if [ -r "$SESSION_THREAD_FILE" ]; then
        THREAD_ID="$(tr -d '[:space:]' < "$SESSION_THREAD_FILE")"
    fi
fi

if [ -z "$THREAD_ID" ]; then
    if [ -r "$LEGACY_CURRENT_FILE" ]; then
        THREAD_ID="$(tr -d '[:space:]' < "$LEGACY_CURRENT_FILE")"
        if [ -n "$THREAD_ID" ]; then
            _TS="$(date -u +%FT%TZ)"
            printf '%s stop fell back to legacy current_thread (session_id=%s thread=%s)\n' \
                "$_TS" "${SESSION_ID:-<missing>}" "$THREAD_ID" \
                >> "$HOOK_WARN_LOG" 2>/dev/null || true
        fi
    fi
fi

# Without a thread there's nothing to capture; still clean up the
# handoff file if we can identify it.
if [ -z "$THREAD_ID" ]; then
    [ -n "$SESSION_THREAD_FILE" ] && [ -f "$SESSION_THREAD_FILE" ] && rm -f "$SESSION_THREAD_FILE"
    exit 0
fi

# Hand the payload to the standalone capture script. It extracts the
# assistant text from the transcript and calls write_buffer atomically.
# Errors are logged (via the script's stderr) but never block the session.
if [ -n "$SESSION_ID" ]; then
    printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" "$CAPTURE_ASSISTANT_PY" \
        "$THREAD_ID" "$SESSION_ID" 2>>/tmp/tinm_hook.log || true
fi

# Push on session end — fires immediately (no 30s delay) so peer host's
# session_start pull gets the latest content even when sessions open back-to-back.
PUSH_THROTTLE_SCRIPT="$SKILL_DIR/push_throttle.py"
if [ -d "$TINM_PCP_DIR/.git" ] && [ -r "$PUSH_THROTTLE_SCRIPT" ] && [ -x "$VENV_PY" ]; then
    "$VENV_PY" "$PUSH_THROTTLE_SCRIPT" force "$TINM_PCP_DIR" \
        >>"$TINM_HOME/sync-$(hostname).log" 2>&1 &
fi

# Clean up the per-session handoff file so $TINM_HOME does not accumulate
# stale entries. Done at the very end so a hook crash earlier still
# leaves the file in place for the next inspection / debug.
#
# TODO(stop-firing-semantics): Per https://code.claude.com/docs/en/hooks
# the Stop hook fires "right before Claude concludes its response" — i.e.
# at the end of EACH assistant turn, not at session end. Deleting the
# handoff file here would therefore break the DEC-2 freeze contract for
# every turn after the first (subsequent user_prompt.sh invocations would
# fall back to legacy current_thread instead of using the frozen handoff).
#
# In practice this has not surfaced as a regression because:
#   (a) `test_session_isolation::test_stop_removes_handoff_file` codifies
#       this deletion as the design intent,
#   (b) user_prompt.sh's _read_session_thread() falls back to
#       ~/.tinm/current_thread, which session_start.sh also keeps in sync
#       on Linux installs, so multi-turn sessions keep functioning.
#
# Leaving the deletion in place per the task spec ("If unclear, leave the
# deletion in place but add a clear comment explaining the assumption and
# a TODO"). Revisit this when adding multi-turn-aware buffer scoring.
if [ -n "$SESSION_THREAD_FILE" ] && [ -f "$SESSION_THREAD_FILE" ]; then
    rm -f "$SESSION_THREAD_FILE" 2>/dev/null || true
fi

# Also clean up the per-session upgrade-notif marker + prompt counter
# written by user_prompt.sh's mid-session upgrade check (F1b). They are
# only meaningful within a single session — leaving them around would
# silently suppress next session's notif (marker) or skew the interval
# (counter).
if [ -n "$SESSION_ID" ]; then
    rm -f "$TINM_HOME/session-${SESSION_ID}.upgrade-shown" 2>/dev/null || true
    rm -f "$TINM_HOME/session-${SESSION_ID}.prompt-count" 2>/dev/null || true
fi

exit 0
