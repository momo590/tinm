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

set -e

PAYLOAD_JSON="$(cat)"
echo "[$(date -u +%FT%TZ)] stop-hook fired" >> /tmp/tinm_hook.log

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

CURRENT_FILE="$TINM_HOME/current_thread"
VENV_PY="$TINM_HOME/.venv/bin/python"
CAPTURE_SCRIPT="$HOME/.claude/skills/tinm/tinm_assistant_capture.py"

# No current thread → nothing to do.
[ -r "$CURRENT_FILE" ] || exit 0
THREAD_ID="$(tr -d '[:space:]' < "$CURRENT_FILE")"
[ -n "$THREAD_ID" ] || exit 0

[ -x "$VENV_PY" ] || exit 0
[ -r "$CAPTURE_SCRIPT" ] || exit 0

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

[ -n "$ASSISTANT_TEXT" ] || exit 0

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

# Push on session end — fires immediately (no 30s delay) so peer host's
# session_start pull gets the latest content even when sessions open back-to-back.
PUSH_THROTTLE_SCRIPT="$HOME/.claude/skills/tinm/push_throttle.py"
if [ -d "$TINM_PCP_DIR/.git" ] && [ -r "$PUSH_THROTTLE_SCRIPT" ] && [ -x "$VENV_PY" ]; then
    "$VENV_PY" "$PUSH_THROTTLE_SCRIPT" force "$TINM_PCP_DIR" \
        >>"$TINM_HOME/sync-$(hostname).log" 2>&1 &
fi

exit 0
