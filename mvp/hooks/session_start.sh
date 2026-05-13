#!/bin/bash
# TINM SessionStart hook — auto-load the current thread's context block at
# the beginning of every Claude Code session.
#
# Stdout from a SessionStart hook is added to Claude's context (per
# https://code.claude.com/docs/en/hooks). We use that to surface the
# trajectory + artifacts so Claude has the user's prior work in mind from
# turn 1 onwards. If no current thread is set, we exit quietly so the
# session is unchanged.

set -e

CURRENT_FILE="$HOME/.tinm/current_thread"
VENV_PY="$HOME/.tinm/.venv/bin/python"
LOAD_SCRIPT="$HOME/.claude/skills/tinm/tinm_load.py"

# No current thread → nothing to inject, exit cleanly so Claude Code does
# not show an error.
[ -r "$CURRENT_FILE" ] || exit 0
THREAD_ID="$(tr -d '[:space:]' < "$CURRENT_FILE")"
[ -n "$THREAD_ID" ] || exit 0

# Venv must be installed before this hook can do anything useful. Fail
# silently rather than spam every session if the user has not finished
# install yet.
[ -x "$VENV_PY" ] || exit 0
[ -r "$LOAD_SCRIPT" ] || exit 0

# Run the loader and let its markdown stdout enter Claude's context.
# Stderr is dropped so warnings (e.g., LibreSSL noise) do not pollute.
"$VENV_PY" "$LOAD_SCRIPT" "$THREAD_ID" 2>/dev/null
