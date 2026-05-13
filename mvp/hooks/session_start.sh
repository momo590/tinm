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

# TINM paths — honor TINM_HOME / TINM_PCP_DIR for Phase 2 multi-host
# setups (Mac<->VPS via Syncthing-over-Tailscale). Defaults match the
# Phase 1 single-host layout. current_thread + venv stay under TINM_HOME
# (machine-local); the PCP store goes under TINM_PCP_DIR.
TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

CURRENT_FILE="$TINM_HOME/current_thread"
VENV_PY="$TINM_HOME/.venv/bin/python"
LOAD_SCRIPT="$HOME/.claude/skills/tinm/tinm_load.py"
AUTO_INIT_SCRIPT="$HOME/.claude/skills/tinm/tinm_auto_init.py"

# Phase 2 sync (best-effort): if the PCP store is a git repo, pull the
# latest snapshot from the remote so this host's session starts with the
# freshest threads/artifacts the peer pushed. Failure is silent — a
# dropped network or transient remote error must not block the session.
if [ -d "$TINM_PCP_DIR/.git" ]; then
    git -C "$TINM_PCP_DIR" pull --rebase --autostash --quiet 2>/dev/null || true
fi

# Auto-init: if no current thread on this host but we are inside a git
# repo, derive a slug from the repo's basename and create / select a
# thread silently. Lets the user skip `/tinm init` entirely on new
# projects. No-op if current_thread is already set, or if we are not in
# a git repo. Stderr is dropped so any diagnostic chatter does not leak
# into Claude's context.
if [ -x "$VENV_PY" ] && [ -r "$AUTO_INIT_SCRIPT" ]; then
    "$VENV_PY" "$AUTO_INIT_SCRIPT" 2>/dev/null || true
fi

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
