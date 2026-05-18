#!/bin/bash
# TINM UserPromptSubmit hook — append every user prompt to the current
# thread's trajectory, EMA-update the anchor (asynchronously), and emit
# the trajectory hint to stdout (injected into Claude's context by Claude
# Code).
#
# v0.3.0 hot-path refactor: this shell wrapper now does the bare minimum
# work (path resolution, sanity check, stdin capture) before delegating
# to ONE Python process — `_hot_path_user_prompt.py` — which fans out
# all the rest. Pre-refactor the hook fired 7+ separate `python -c …`
# heredocs in series, paying ~30-40ms of cold-start each. Consolidation
# brings p99 from 740ms back under the 200ms gate.
#
# Per https://code.claude.com/docs/en/hooks the hook receives a JSON
# payload on stdin; whatever the script writes to stdout is injected as
# additional context before Claude processes the current message — we
# use that to deliver the TINM trajectory hint, pending digest, or
# upgrade notification.

set -e

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

VENV_PY="$TINM_HOME/.venv/bin/python"
SKILL_DIR="$HOME/.claude/skills/tinm"
HOT_PATH="$SKILL_DIR/_hot_path_user_prompt.py"

# If TINM is not yet installed (venv missing) or the runner is absent,
# fail silent so a partial install does not break the user's prompt.
[ -x "$VENV_PY" ] || exit 0
[ -r "$HOT_PATH" ] || exit 0

# Single Python invocation. stdin (the Claude Code JSON payload) is piped
# straight through. stdout from the runner is injected into Claude's
# context. stderr is dropped to keep the hook quiet.
exec "$VENV_PY" "$HOT_PATH" 2>/dev/null
