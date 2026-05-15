#!/bin/bash
# TINM Cursor adapter — bridges Cursor's beforeSubmitPrompt hook to the TINM pipeline.
#
# [BETA - schema unverified] This adapter was written based on Cursor's documented
# hook API but has not been tested against a live Cursor installation. Use --debug
# to verify the JSON schema received.
#
# Installation in Cursor:
#   In .cursor/hooks/beforeSubmitPrompt.sh (or cursor rules):
#   bash ~/.tinm/source/mvp/hooks/cursor_hook.sh "$@"
#
# The hook receives a JSON payload on stdin with Cursor-specific fields.
# We normalize it to TINM's expected format and call user_prompt.sh logic.
#
# Usage:
#   cursor_hook.sh [--debug]
#
# Debug mode: logs the received JSON to /tmp/tinm_cursor_debug.log

set -e

DEBUG_MODE=0
for arg in "$@"; do
    [ "$arg" = "--debug" ] && DEBUG_MODE=1
done

PAYLOAD_JSON="$(cat)"

if [ "$DEBUG_MODE" -eq 1 ]; then
    echo "[$(date -u +%FT%TZ)] cursor_hook.sh received:" >> /tmp/tinm_cursor_debug.log
    echo "$PAYLOAD_JSON" | python3 -m json.tool >> /tmp/tinm_cursor_debug.log 2>/dev/null || \
        echo "$PAYLOAD_JSON" >> /tmp/tinm_cursor_debug.log
    echo "---" >> /tmp/tinm_cursor_debug.log
fi

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
VENV_PY="$TINM_HOME/.venv/bin/python"
CURSOR_ADAPTER="$HOME/.claude/skills/tinm/tinm_cursor.py"

[ -x "$VENV_PY" ] || exit 0
[ -r "$CURSOR_ADAPTER" ] || exit 0

# Normalize Cursor JSON to TINM's UserPromptSubmit format using the Python adapter
NORMALIZED="$(printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" "$CURSOR_ADAPTER" 2>>/tmp/tinm_hook.log)" || exit 0
[ -n "$NORMALIZED" ] || exit 0

# Hand off to the TINM user_prompt.sh pipeline
HOOK_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
USER_PROMPT_HOOK="$HOOK_DIR/user_prompt.sh"
[ -r "$USER_PROMPT_HOOK" ] || exit 0

printf '%s' "$NORMALIZED" | bash "$USER_PROMPT_HOOK"

exit 0
