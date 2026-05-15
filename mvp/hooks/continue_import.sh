#!/bin/bash
# TINM x Continue.dev session importer [BETA-unverified]
#
# One-shot importer for Continue.dev session history. Continue.dev does not
# expose a real-time hook surface, so we read its on-disk session JSON
# (typically ~/.continue/sessions/<session_id>.json) and bulk-replay each user
# message through TINM's normal user_prompt.sh pipeline.
#
# Usage:
#   bash continue_import.sh ~/.continue/sessions/<id>.json
#
# Overrides:
#   TINM_HOME — TINM install root (default: $HOME/.tinm)

set -e

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
VENV_PY="$TINM_HOME/.venv/bin/python"
ADAPTER="$HOME/.claude/skills/tinm/tinm_continue.py"
SESSION_FILE="$1"

if [ -z "$SESSION_FILE" ] || [ ! -r "$SESSION_FILE" ]; then
    echo "Usage: $0 <path to Continue.dev session JSON>" >&2
    exit 1
fi

[ -x "$VENV_PY" ] || {
    echo "tinm continue_import: TINM venv not found at $VENV_PY" >&2
    exit 1
}
[ -r "$ADAPTER" ] || {
    echo "tinm continue_import: adapter not found at $ADAPTER" >&2
    exit 1
}

exec "$VENV_PY" "$ADAPTER" --import "$SESSION_FILE"
