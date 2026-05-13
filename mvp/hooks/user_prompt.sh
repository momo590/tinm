#!/bin/bash
# TINM UserPromptSubmit hook — append every user prompt to the current
# thread's trajectory and EMA-update the anchor.
#
# Per https://code.claude.com/docs/en/hooks, the hook receives a JSON
# payload on stdin with at least `prompt` and `session_id` fields. We
# only need `prompt`. We do not print anything to stdout because we do
# NOT want the update result (alpha, anchor stats) to bleed into
# Claude's context — the silent persistence side of the protocol.

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
UPDATE_SCRIPT="$HOME/.claude/skills/tinm/tinm_update.py"

# No current thread → nothing to do.
[ -r "$CURRENT_FILE" ] || exit 0
THREAD_ID="$(tr -d '[:space:]' < "$CURRENT_FILE")"
[ -n "$THREAD_ID" ] || exit 0

# Venv guard — see session_start.sh for the same rationale.
[ -x "$VENV_PY" ] || exit 0
[ -r "$UPDATE_SCRIPT" ] || exit 0

# Read the hook payload from stdin and extract the prompt text via the
# venv Python — we have a Python runtime right there, no reason to add a
# `jq` dependency that the user may not have on a fresh macOS box.
PROMPT_JSON="$(cat)"
PROMPT_TEXT="$(printf '%s' "$PROMPT_JSON" | "$VENV_PY" -c \
    'import json, sys; print(json.load(sys.stdin).get("prompt", ""), end="")' \
    2>/dev/null)"
[ -n "$PROMPT_TEXT" ] || exit 0

# Run the update silently. stdout/stderr both dropped — the script
# already persists the new turn into the thread file, which is the only
# side effect we want.
"$VENV_PY" "$UPDATE_SCRIPT" "$THREAD_ID" \
    --query "$PROMPT_TEXT" \
    --role user \
    --client claude-code \
    >/dev/null 2>&1 || true

# Phase 2 sync (best-effort, background): if the PCP store is a git
# repo, commit + push the new turn so the peer host picks it up at its
# next SessionStart. Detached subshell so the user's prompt is not
# blocked on the network round-trip. Failure is silent — local state is
# already persisted, so a missed push just delays propagation.
if [ -d "$TINM_PCP_DIR/.git" ]; then
    (
        git -C "$TINM_PCP_DIR" add -A
        if ! git -C "$TINM_PCP_DIR" diff --cached --quiet; then
            git -C "$TINM_PCP_DIR" commit -m "auto: turn @ $(date -u +%FT%TZ) from $(hostname -s)" --quiet
            git -C "$TINM_PCP_DIR" push --quiet 2>/dev/null
        fi
    ) >/dev/null 2>&1 &
fi

exit 0
