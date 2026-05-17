#!/bin/bash
# TINM UserPromptSubmit hook — append every user prompt to the current
# thread's trajectory, EMA-update the anchor, and emit the trajectory
# hint to stdout (injected into Claude's context by Claude Code).
#
# Per https://code.claude.com/docs/en/hooks, the hook receives a JSON
# payload on stdin. stdout is injected as additional context before
# Claude processes the current message — we use it to deliver the
# TINM queries-only trajectory hint.
#
# ── v0.2.3 thread-isolation (DEC-2 freeze-at-start) ──────────────────────
# THREAD_ID is read from the per-session handoff file written by
# session_start.sh — keyed by the session_id Claude Code passes in the
# JSON payload. We do NOT re-resolve from cwd here: cd'ing mid-session
# must not switch threads (DEC-2).
#
# Fallback: if the handoff file is missing (parallel CC instances,
# pre-v0.2.3 SessionStart hook on the peer host, hook ordering race),
# read the legacy ~/.tinm/current_thread file so v0.2.2 installs mid-
# migration do not break. Log one warning line per fallback to the
# per-host hook-warn log.
# ─────────────────────────────────────────────────────────────────────────

set -e

# Read stdin ONCE at the top. Must happen before any subshell reads
# /dev/stdin — otherwise the first N bytes are consumed and the JSON
# is corrupted for the subsequent PROMPT_TEXT extraction.
PROMPT_JSON="$(cat)"

echo "[$(date -u +%FT%TZ)] hook fired" >> /tmp/tinm_hook.log

# TINM paths — honor TINM_HOME / TINM_PCP_DIR for Phase 2 multi-host
# setups (e.g. Mac<->Linux via Syncthing-over-Tailscale). Defaults match the
# Phase 1 single-host layout. session handoff + venv stay under TINM_HOME
# (machine-local); the PCP store goes under TINM_PCP_DIR.
TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

VENV_PY="$TINM_HOME/.venv/bin/python"
SKILL_DIR="$HOME/.claude/skills/tinm"
UPDATE_SCRIPT="$SKILL_DIR/tinm_update.py"
PUSH_THROTTLE_SCRIPT="$SKILL_DIR/push_throttle.py"
CAPTURE_SCRIPT="$SKILL_DIR/tinm_assistant_capture.py"

# Legacy current_thread fallback path (read-only fallback only —
# user_prompt.sh never writes to it under v0.2.3).
LEGACY_CURRENT_FILE="$TINM_HOME/current_thread"
HOOK_WARN_LOG="$TINM_HOME/hook-warn-$(hostname | tr '.' '-').log"

# Venv guard — see session_start.sh for the same rationale.
[ -x "$VENV_PY" ] || exit 0
[ -r "$UPDATE_SCRIPT" ] || exit 0

# Extract prompt text, transcript path, and session ID from the captured JSON.
# All three are needed early — transcript path and session ID before the F1
# upgrade check, prompt text before everything else.
PROMPT_TEXT="$(printf '%s' "$PROMPT_JSON" | "$VENV_PY" -c \
    'import json, sys; print(json.load(sys.stdin).get("prompt", ""), end="")' \
    2>/dev/null)"
[ -n "$PROMPT_TEXT" ] || exit 0

# Heuristic log for native-compaction detection fallback. Consumed by
# tinm_compaction_detect.heuristic_compaction_fired() when the PreCompact
# marker is absent (e.g., hook not yet installed on peer host, or
# pre-marker session). Cheap: one wc -l + one printf >> file.
TRANSCRIPT_PATH="$(printf '%s' "$PROMPT_JSON" | "$VENV_PY" -c \
    'import json, sys; print(json.load(sys.stdin).get("transcript_path", ""), end="")' \
    2>/dev/null)"
SESSION_ID="$(printf '%s' "$PROMPT_JSON" | "$VENV_PY" -c \
    'import json, sys; print(json.load(sys.stdin).get("session_id", ""), end="")' \
    2>/dev/null)"

# ── DEC-2: read frozen thread name from per-session handoff file ─────────
THREAD_ID=""
if [ -n "$SESSION_ID" ]; then
    SESSION_THREAD_FILE="$TINM_HOME/session-${SESSION_ID}.thread"
    if [ -r "$SESSION_THREAD_FILE" ]; then
        THREAD_ID="$(tr -d '[:space:]' < "$SESSION_THREAD_FILE")"
    fi
fi

if [ -z "$THREAD_ID" ]; then
    # Fallback: legacy global current_thread (v0.2.2 backwards compat).
    # Log one line so we can detect peer hosts that have not yet
    # upgraded SessionStart to v0.2.3.
    if [ -r "$LEGACY_CURRENT_FILE" ]; then
        THREAD_ID="$(tr -d '[:space:]' < "$LEGACY_CURRENT_FILE")"
        if [ -n "$THREAD_ID" ]; then
            _TS="$(date -u +%FT%TZ)"
            printf '%s user_prompt fell back to legacy current_thread (session_id=%s thread=%s)\n' \
                "$_TS" "${SESSION_ID:-<missing>}" "$THREAD_ID" \
                >> "$HOOK_WARN_LOG" 2>/dev/null || true
        fi
    fi
fi

# No thread → nothing to do.
[ -n "$THREAD_ID" ] || exit 0

# Compute transcript line count once — reused by F1, F3, and F7.
_TRANSCRIPT_LINES=0
if [ -r "$TRANSCRIPT_PATH" ]; then
    _TRANSCRIPT_LINES="$(wc -l < "$TRANSCRIPT_PATH" 2>/dev/null | tr -d ' ' || echo 0)"
fi

if [ -n "$TRANSCRIPT_PATH" ] && [ -r "$TRANSCRIPT_PATH" ] && [ -n "$SESSION_ID" ]; then
    [ -n "$_TRANSCRIPT_LINES" ] && printf '{"ts":"%s","session_id":"%s","n_messages":%s}\n' \
        "$(date -u +%FT%TZ)" "$SESSION_ID" "$_TRANSCRIPT_LINES" \
        >> "$TINM_PCP_DIR/transcript_size.jsonl" 2>/dev/null || true
fi

# ── F4: Conv index — populate intra-session rolling index ────────────────────
# Reads text from stdin via tinm_conv_add.py to avoid all shell quoting hazards
# around arbitrary user prompt content. Best-effort: || true ensures the hook
# never fails due to a conv_index error. TURN_N is the number of transcript
# lines at this point, used as a monotonically increasing turn counter.
CONV_ADD_SCRIPT="$SKILL_DIR/tinm_conv_add.py"
if [ -n "$SESSION_ID" ] && [ -n "$PROMPT_TEXT" ] && [ -x "$VENV_PY" ] && [ -r "$CONV_ADD_SCRIPT" ]; then
    printf '%s' "$PROMPT_TEXT" | \
        "$VENV_PY" "$CONV_ADD_SCRIPT" "$SESSION_ID" "$_TRANSCRIPT_LINES" "user" \
        2>>/tmp/tinm_hook.log || true
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── F1: Upgrade trigger ──────────────────────────────────────────────────────
# If the first 20 characters of the prompt (stripped, lowercased) start with
# "upgrade" AND this is the first user turn (transcript has < 4 lines), launch
# tinm_upgrade.py in the background and emit an acknowledgement to stdout so
# Claude surfaces it. This is checked BEFORE the L1 threshold to ensure it is
# never suppressed by the skip-find gate.
UPGRADE_SCRIPT="$SKILL_DIR/tinm_upgrade.py"
_FIRST20="$(printf '%s' "$PROMPT_TEXT" | tr '[:upper:]' '[:lower:]' | cut -c1-20 | tr -d ' \t\n')"
if printf '%s' "$_FIRST20" | grep -q '^upgrade' && [ "$_TRANSCRIPT_LINES" -lt 4 ]; then
    if [ -r "$UPGRADE_SCRIPT" ] && [ -x "$VENV_PY" ]; then
        "$VENV_PY" "$UPGRADE_SCRIPT" \
            >/tmp/tinm_upgrade_out.log 2>&1 &
        echo "[TINM] Upgrade triggered — running in background. You will see the result shortly."
    fi
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── F1b: Mid-session upgrade notification ────────────────────────────────────
# A new TINM version released DURING a long session would otherwise only be
# surfaced at the user's NEXT session_start. Every N user prompts (N =
# config update_notify_interval, default 20), re-check for an upgrade,
# honoring the 24h network cache inside tinm_update_check.
#
# Suppression rules:
#   - If ~/.tinm/session-<id>.upgrade-shown exists, the SessionStart notif
#     (or a prior mid-session notif) already surfaced — do not duplicate.
#   - If update_notify=false in config, skip entirely.
#   - If update_notify_interval=0, skip entirely (disable mid-session).
#
# State kept per-session under $TINM_HOME (machine-local; stop.sh cleans up):
#   session-<id>.prompt-count   — monotonically increasing prompt counter
#   session-<id>.upgrade-shown  — marker file (touch) once notif emitted
#
# Cost when no check fires: 1 file read + 1 file write + 1 arithmetic — < 1ms.
# Cost when check fires (every Nth prompt): an extra Python import + cache
# read; the 24h cache means it almost never hits the network. The check runs
# in foreground so the notif appears in this prompt's context, but is bounded
# by a 3s timeout so a slow remote can never block the user.
if [ -n "$SESSION_ID" ] && [ -x "$VENV_PY" ]; then
    _COUNT_FILE="$TINM_HOME/session-${SESSION_ID}.prompt-count"
    _MARKER_FILE="$TINM_HOME/session-${SESSION_ID}.upgrade-shown"

    # Increment per-session prompt counter. Best-effort: a corrupt file
    # resets the count to 1 (no notif on this turn, will resume on next).
    _PROMPT_N=0
    if [ -r "$_COUNT_FILE" ]; then
        _PROMPT_N="$(tr -d '[:space:]' < "$_COUNT_FILE" 2>/dev/null || echo 0)"
        case "$_PROMPT_N" in
            ''|*[!0-9]*) _PROMPT_N=0 ;;
        esac
    fi
    _PROMPT_N=$((_PROMPT_N + 1))
    printf '%s\n' "$_PROMPT_N" > "$_COUNT_FILE" 2>/dev/null || true

    if [ ! -e "$_MARKER_FILE" ]; then
        _UPGRADE_NOTIF="$(MARKER="$_MARKER_FILE" PROMPT_N="$_PROMPT_N" \
            timeout 3s "$VENV_PY" - << 'PYEOF' 2>/dev/null || true
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path.home() / ".claude" / "skills" / "tinm"))
try:
    from tinm_config import get_config
    from tinm_update_check import check_for_update
    cfg = get_config()
    if not cfg.get("update_notify", True):
        sys.exit(0)
    interval = int(cfg.get("update_notify_interval", 20) or 0)
    if interval <= 0:
        sys.exit(0)
    try:
        n = int(os.environ.get("PROMPT_N", "0"))
    except ValueError:
        n = 0
    # Fire only on the Nth, 2Nth, 3Nth... prompt of the session.
    if n < interval or (n % interval) != 0:
        sys.exit(0)
    r = check_for_update()
    if not r.get("has_update"):
        sys.exit(0)
    latest = r.get("latest") or "?"
    print(
        f"\U0001f4a1 TINM v{latest} available — respond 'upgrade' at the start of your next message to install automatically."
    )
    marker = os.environ.get("MARKER", "")
    if marker:
        try:
            p = pathlib.Path(marker)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        except OSError:
            pass
except Exception:
    pass
PYEOF
)"
        if [ -n "$_UPGRADE_NOTIF" ]; then
            printf '%s\n' "$_UPGRADE_NOTIF"
        fi
    fi
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── F3: L1 activation threshold ─────────────────────────────────────────────
# Skip expensive hint-emission on very short sessions unless anaphora is
# detected. Saves ~150ms on turns 1-2.
# _TRANSCRIPT_LINES was computed above; alias for clarity.
_TURN_COUNT="${_TRANSCRIPT_LINES:-0}"

# Anaphora detection (FR + EN) — must match the logic in tinm_update.py
# _ANAPHORIC_TOKEN_RE so results are consistent.
_ANAPHORA_PATTERN='(avant|earlier|before|comme|précédemment|previously|turn|tour|step|étape|like we|what we|ce qu|qu'"'"'on)'
_HAS_ANAPHORA=0
if printf '%s' "$PROMPT_TEXT" | grep -iqE "$_ANAPHORA_PATTERN" 2>/dev/null; then
    _HAS_ANAPHORA=1
fi

_SKIP_FIND=0
if [ "$_TURN_COUNT" -lt 6 ] && [ "$_HAS_ANAPHORA" -eq 0 ]; then
    _SKIP_FIND=1
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── F7: Digest injection + async trigger ────────────────────────────────────
DIGEST_SCRIPT="$SKILL_DIR/tinm_digest.py"
COMPACTION_SCRIPT="$SKILL_DIR/tinm_compaction_detect.py"

if [ -r "$DIGEST_SCRIPT" ] && [ -x "$VENV_PY" ] && [ -n "$SESSION_ID" ]; then
    # Build a small inline Python runner for both compaction detect + digest ops.
    _DIGEST_OUT="$(SKILL_DIR="$SKILL_DIR" \
        "$VENV_PY" - "$SESSION_ID" "$THREAD_ID" "$TRANSCRIPT_PATH" "$_TURN_COUNT" \
        2>>/tmp/tinm_hook.log << 'PYEOF'
import sys, os
from pathlib import Path
sys.path.insert(0, os.environ.get('SKILL_DIR', ''))

session_id    = sys.argv[1]
thread_id     = sys.argv[2]
transcript_path = sys.argv[3]
try:
    turn_count = int(sys.argv[4])
except (IndexError, ValueError):
    turn_count = 0

from tinm_digest import get_pending_digest, should_trigger_digest, launch_digest_async
from tinm_compaction_detect import compaction_active

# Count lines in transcript (may differ from hook's value if compaction fired)
n_lines = 0
if transcript_path:
    try:
        n_lines = sum(1 for _ in open(transcript_path) if _.strip())
    except Exception:
        pass

# Check for a pending digest from a previous background run.
pending = get_pending_digest(session_id, turn_count)
if pending:
    print(f"[TINM digest]\n{pending}")

# Conditionally launch a new digest if session is long enough and not compacted.
is_compacted = compaction_active(session_id, n_lines)
if not is_compacted and should_trigger_digest(transcript_path, session_id):
    launch_digest_async(session_id, thread_id, transcript_path, turn_count)
PYEOF
    2>/dev/null)" || true

    # Emit pending digest to stdout (Claude's context) if one was ready.
    if [ -n "$_DIGEST_OUT" ]; then
        printf '%s\n' "$_DIGEST_OUT"
    fi
fi
# ─────────────────────────────────────────────────────────────────────────────

# Run the trajectory update.
# - Always record trajectory (omit --emit-hint when _SKIP_FIND=1).
# - Only emit hint when _SKIP_FIND=0 (turn >= 6 or anaphora detected).
# --telemetry wraps the call in measure_latency() (Lane H) — no-op if opted out.
if [ "$_SKIP_FIND" -eq 0 ]; then
    # Full path: record + emit hint
    "$VENV_PY" "$UPDATE_SCRIPT" "$THREAD_ID" \
        --query "$PROMPT_TEXT" \
        --role user \
        --client claude-code \
        --emit-hint \
        --telemetry user_prompt_submit \
        2>/dev/null || true
else
    # Short session, no anaphora: record trajectory but suppress hint output
    "$VENV_PY" "$UPDATE_SCRIPT" "$THREAD_ID" \
        --query "$PROMPT_TEXT" \
        --role user \
        --client claude-code \
        --telemetry user_prompt_submit \
        2>/dev/null || true
fi

# v0.2.1 — assistant capture pipeline. Score the buffered assistant turn
# against this user prompt, register approved/rejected/neutral. Never
# blocks the user's flow on error — errors go to /tmp/tinm_hook.log so
# they can be diagnosed without surfacing to the prompt path.
if [ -r "$CAPTURE_SCRIPT" ] && [ -n "$SESSION_ID" ]; then
    # Read trajectory length (= current user turn count) AFTER the update
    # above. This becomes `next_user_turn` for the buffered assistant
    # response — the precise turn that scored it.
    TURN_COUNT="$(SKILL_DIR="$SKILL_DIR" \
        "$VENV_PY" - "$THREAD_ID" 2>>/tmp/tinm_hook.log << 'PYEOF'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ.get('SKILL_DIR', ''))
from tinm_paths import THREADS_DIR
p = THREADS_DIR / f'{sys.argv[1]}.json'
if p.exists():
    try:
        print(len(json.loads(p.read_text()).get('trajectory', [])), end='')
    except (json.JSONDecodeError, OSError):
        pass
PYEOF
)"

    TURN_ARG=()
    [ -n "$TURN_COUNT" ] && TURN_ARG=(--next-turn "$TURN_COUNT")

    "$VENV_PY" "$CAPTURE_SCRIPT" score_and_flush \
        --thread-id "$THREAD_ID" \
        --session-id "$SESSION_ID" \
        --prompt "$PROMPT_TEXT" \
        "${TURN_ARG[@]}" \
        >/dev/null 2>>/tmp/tinm_hook.log || true
fi

# Phase 2 sync (best-effort, background): if the PCP store is a git
# repo, commit + push the new turn so the peer host picks it up at its
# next SessionStart. Detached subshell so the user's prompt is not
# blocked on the network round-trip. Failure is silent — local state is
# already persisted, so a missed push just delays propagation.
if [ -d "$TINM_PCP_DIR/.git" ] && [ -r "$PUSH_THROTTLE_SCRIPT" ]; then
    "$VENV_PY" "$PUSH_THROTTLE_SCRIPT" schedule "$TINM_PCP_DIR" >/dev/null 2>&1 &
fi

exit 0
