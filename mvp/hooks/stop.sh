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

set -e

PAYLOAD_JSON="$(cat)"
echo "[$(date -u +%FT%TZ)] stop-hook fired" >> /tmp/tinm_hook.log

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

VENV_PY="$TINM_HOME/.venv/bin/python"
SKILL_DIR="$HOME/.claude/skills/tinm"
CAPTURE_SCRIPT="$SKILL_DIR/tinm_assistant_capture.py"

LEGACY_CURRENT_FILE="$TINM_HOME/current_thread"
HOOK_WARN_LOG="$TINM_HOME/hook-warn-$(hostname | tr '.' '-').log"

[ -x "$VENV_PY" ] || exit 0
[ -r "$CAPTURE_SCRIPT" ] || exit 0

# Extract session_id early so we can locate the per-session handoff file.
SESSION_ID="$(printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" -c \
    'import json, sys
try:
    print(json.load(sys.stdin).get("session_id", ""), end="")
except Exception:
    pass' 2>/dev/null)"

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

# Extract session_id + transcript_path + assistant final text from the
# transcript JSONL. Defensive: any parse error → exit 0 silently.
ASSISTANT_TEXT="$(printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" - << 'PYEOF' 2>>/tmp/tinm_hook.log
import json, sys, pathlib

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

transcript_path = payload.get("transcript_path") or ""
session_id = payload.get("session_id") or ""
if not transcript_path or not session_id:
    sys.exit(0)

p = pathlib.Path(transcript_path)
if not p.is_file():
    sys.exit(0)

# Walk backward to find the last assistant message that has a non-empty
# text content (skip tool-only turns per E9).
last_text = ""
with p.open() as f:
    lines = [line for line in f if line.strip()]
for line in reversed(lines):
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    # Claude Code transcripts wrap each message in {"message": {...}, ...}.
    inner = msg.get("message") if isinstance(msg.get("message"), dict) else msg
    if inner.get("role") != "assistant":
        continue
    content = inner.get("content")
    if isinstance(content, str):
        last_text = content
        break
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                t = blk.get("text", "")
                if t:
                    parts.append(t)
        if parts:
            last_text = "\n".join(parts)
            break

# Pass session_id + text via env so the bash side can use them.
print(json.dumps({"session_id": session_id, "text": last_text}))
PYEOF
)"

if [ -n "$ASSISTANT_TEXT" ]; then
    # Hand off to the Python capture module which handles buffer write,
    # atomic atomicity, and tool-only-turn skip.
    echo "$ASSISTANT_TEXT" | "$VENV_PY" - "$THREAD_ID" "$CAPTURE_SCRIPT" << 'PYEOF' 2>>/tmp/tinm_hook.log || true
import json, sys, runpy, types
thread_id = sys.argv[1]
capture_path = sys.argv[2]

raw = sys.stdin.read().strip()
if not raw:
    sys.exit(0)
try:
    info = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(0)

# Load tinm_assistant_capture as a module (preserves env var paths).
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "tinm_assistant_capture", capture_path
)
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(pathlib.Path(capture_path).parent))
spec.loader.exec_module(mod)

mod.write_buffer(thread_id, info["session_id"], info["text"])
PYEOF
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
if [ -n "$SESSION_THREAD_FILE" ] && [ -f "$SESSION_THREAD_FILE" ]; then
    rm -f "$SESSION_THREAD_FILE" 2>/dev/null || true
fi

exit 0
