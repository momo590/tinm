#!/bin/bash
# TINM x Codex CLI wrapper [BETA-unverified]
#
# Use as a shell alias to transparently capture every Codex prompt into TINM:
#   alias codex='bash ~/.tinm/source/mvp/hooks/codex_hook.sh'
#
# This wraps the OpenAI codex CLI and captures each user prompt via
# tinm_cli_wrap.py. If TINM is not installed or the wrapper is missing, we
# fall through to the real codex binary so the user's workflow is never
# blocked.
#
# Overrides:
#   TINM_CODEX_BIN  — path to the real codex binary (default: codex on PATH)
#   TINM_HOME       — TINM install root (default: $HOME/.tinm)

set -e

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
VENV_PY="$TINM_HOME/.venv/bin/python"
WRAPPER="$HOME/.claude/skills/tinm/tinm_cli_wrap.py"
REAL_CODEX="${TINM_CODEX_BIN:-codex}"

# If TINM venv or wrapper is missing, just exec codex directly (never block).
[ -x "$VENV_PY" ] || exec "$REAL_CODEX" "$@"
[ -r "$WRAPPER" ] || exec "$REAL_CODEX" "$@"

exec "$VENV_PY" "$WRAPPER" --vendor codex -- "$REAL_CODEX" "$@"
