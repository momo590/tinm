#!/bin/bash
# TINM PreCompact / PostCompact hook — drop a marker file when Claude
# Code's native context compaction fires (manual via /compact or auto on
# context-window pressure). digest_generator._native_compaction_fired()
# reads these markers to decide whether to cede to native compaction
# instead of injecting its own digest.
#
# Per https://code.claude.com/docs/en/hooks, PreCompact receives JSON on
# stdin with at least: session_id, transcript_path, cwd, hook_event_name.
# The matcher ("manual" | "auto") may be passed via the first CLI arg
# when the hook is wired with separate matchers; otherwise we record
# "unknown".

set -e

PROMPT_JSON="$(cat)"
TRIGGER="${1:-unknown}"

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
VENV_PY="$TINM_HOME/.venv/bin/python"

MARKERS_DIR="$TINM_PCP_DIR/compaction_markers"
mkdir -p "$MARKERS_DIR"

SESSION_ID="$(printf '%s' "$PROMPT_JSON" | "$VENV_PY" -c \
    'import json, sys; print(json.load(sys.stdin).get("session_id", ""), end="")' \
    2>/dev/null)"
[ -n "$SESSION_ID" ] || exit 0

HOOK_EVENT="$(printf '%s' "$PROMPT_JSON" | "$VENV_PY" -c \
    'import json, sys; print(json.load(sys.stdin).get("hook_event_name", "PreCompact"), end="")' \
    2>/dev/null)"

MARKER="$MARKERS_DIR/${SESSION_ID}.json"
TS="$(date -u +%FT%TZ)"

"$VENV_PY" - "$MARKER" "$SESSION_ID" "$TS" "$TRIGGER" "$HOOK_EVENT" << 'PYEOF' 2>/dev/null || true
import json, sys, pathlib
marker, sid, ts, trigger, event = sys.argv[1:6]
p = pathlib.Path(marker)
existing = {}
if p.exists():
    try: existing = json.loads(p.read_text())
    except Exception: existing = {}
existing.setdefault("events", []).append({"ts": ts, "trigger": trigger, "event": event})
existing["session_id"] = sid
existing["last_ts"] = ts
p.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
PYEOF

exit 0
