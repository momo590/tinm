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

CURRENT_FILE="$HOME/.tinm/current_thread"
VENV_PY="$HOME/.tinm/.venv/bin/python"
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

exit 0
