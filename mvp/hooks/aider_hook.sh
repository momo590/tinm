#!/bin/bash
# TINM x Aider wrapper [BETA-unverified]
#
# Use as a shell alias to transparently capture every Aider prompt into TINM:
#   alias aider='bash ~/.tinm/source/mvp/hooks/aider_hook.sh'
#
# This wraps the aider CLI and captures each user prompt via tinm_cli_wrap.py.
# If TINM is not installed or the wrapper is missing, we fall through to the
# real aider binary so the user's workflow is never blocked.
#
# Overrides:
#   TINM_AIDER_BIN  — path to the real aider binary (default: aider on PATH)
#   TINM_HOME       — TINM install root (default: $HOME/.tinm)

set -e

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
VENV_PY="$TINM_HOME/.venv/bin/python"
WRAPPER="$HOME/.claude/skills/tinm/tinm_cli_wrap.py"
REAL_AIDER="${TINM_AIDER_BIN:-aider}"

# If TINM venv or wrapper is missing, just exec aider directly (never block).
[ -x "$VENV_PY" ] || exec "$REAL_AIDER" "$@"
[ -r "$WRAPPER" ] || exec "$REAL_AIDER" "$@"

exec "$VENV_PY" "$WRAPPER" --vendor aider -- "$REAL_AIDER" "$@"
