#!/bin/bash
# TINM Windsurf adapter — bridges Windsurf's Cascade hook to the TINM pipeline.
#
# [BETA - schema unverified] This adapter was written based on Windsurf's
# Cascade agent hook surface (derived from the Cursor lineage) but has not
# been tested against a live Windsurf installation. Use --debug to verify
# the JSON schema received.
#
# Installation in Windsurf:
#   Reference this script from the Cascade hook configuration:
#   bash ~/.tinm/source/mvp/hooks/windsurf_hook.sh "$@"
#
# The hook receives a JSON payload on stdin with Windsurf-specific fields.
# We normalize it to TINM's expected format and call user_prompt.sh logic.
#
# Usage:
#   windsurf_hook.sh [--debug]
#
# Debug mode: logs the received JSON to /tmp/tinm_windsurf_debug.log

set -e

DEBUG_MODE=0
for arg in "$@"; do
    [ "$arg" = "--debug" ] && DEBUG_MODE=1
done

PAYLOAD_JSON="$(cat)"

if [ "$DEBUG_MODE" -eq 1 ]; then
    echo "[$(date -u +%FT%TZ)] windsurf_hook.sh received:" >> /tmp/tinm_windsurf_debug.log
    echo "$PAYLOAD_JSON" | python3 -m json.tool >> /tmp/tinm_windsurf_debug.log 2>/dev/null || \
        echo "$PAYLOAD_JSON" >> /tmp/tinm_windsurf_debug.log
    echo "---" >> /tmp/tinm_windsurf_debug.log
fi

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
VENV_PY="$TINM_HOME/.venv/bin/python"
WINDSURF_ADAPTER="$HOME/.claude/skills/tinm/tinm_windsurf.py"

[ -x "$VENV_PY" ] || exit 0
[ -r "$WINDSURF_ADAPTER" ] || exit 0

# Normalize Windsurf JSON to TINM's UserPromptSubmit format using the Python adapter
NORMALIZED="$(printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" "$WINDSURF_ADAPTER" 2>>/tmp/tinm_hook.log)" || exit 0
[ -n "$NORMALIZED" ] || exit 0

# Hand off to the TINM user_prompt.sh pipeline
HOOK_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
USER_PROMPT_HOOK="$HOOK_DIR/user_prompt.sh"
[ -r "$USER_PROMPT_HOOK" ] || exit 0

printf '%s' "$NORMALIZED" | bash "$USER_PROMPT_HOOK"

exit 0
