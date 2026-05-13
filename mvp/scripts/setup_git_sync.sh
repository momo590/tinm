#!/usr/bin/env bash
# Wire $TINM_PCP_DIR as a git repo with the supplied remote so the
# Phase 2 hooks (session_start.sh pull, user_prompt.sh push) can
# replicate the PCP store across hosts.
#
# Idempotent: re-running with the same remote is a no-op; with a
# different remote, the existing one is updated in place.
#
# Usage:
#   mvp/scripts/setup_git_sync.sh <git-remote-url>
#
# Example:
#   mvp/scripts/setup_git_sync.sh git@github.com:user/tinm-pcp.git
set -euo pipefail

REMOTE_URL="${1:-}"
if [ -z "$REMOTE_URL" ]; then
    echo "usage: $0 <git-remote-url>" >&2
    echo "  e.g. $0 git@github.com:user/tinm-pcp.git" >&2
    exit 1
fi

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
# Resolve TINM_PCP_DIR with the same fallback logic as tinm_paths.py so
# this script works on both unmigrated and migrated installs.
if [ -n "${TINM_PCP_DIR:-}" ]; then
    PCP="$TINM_PCP_DIR"
elif [ -d "$TINM_HOME/pcp" ]; then
    PCP="$TINM_HOME/pcp"
elif [ -d "$TINM_HOME/threads" ]; then
    PCP="$TINM_HOME"
else
    PCP="$TINM_HOME/pcp"
fi

mkdir -p "$PCP"
cd "$PCP"

echo "Target PCP dir : $PCP"
echo "Remote         : $REMOTE_URL"

# Safety check: refuse to init a git repo in a Phase 1 layout. The
# back-compat fallback in tinm_paths.py resolves TINM_PCP_DIR to
# TINM_HOME itself when ~/.tinm/pcp/ does not yet exist — fine for
# read/write, but disastrous for `git add -A`, which would try to track
# the Python venv (~500MB torch+numpy) and the per-host current_thread
# marker. Detect and bail out with a clear remediation path.
if [ -d "$PCP/.venv" ] || [ -f "$PCP/current_thread" ]; then
    cat >&2 <<EOF
ERROR: $PCP looks like a Phase 1 layout (contains .venv/ or current_thread).
       Initialising git here would try to track the Python venv and the
       per-host session marker, both of which must stay machine-local.

Fix:   Run the migration first to move threads/ and artifacts/ under pcp/:

           $(dirname "$0")/migrate_to_pcp_subdir.sh

       Then re-run this script. It will then resolve $TINM_HOME/pcp and
       only track the JSON store.
EOF
    exit 1
fi

if [ ! -d .git ]; then
    git init --quiet
    git remote add origin "$REMOTE_URL"
    echo "Initialized empty git repo at $PCP"
else
    if git remote get-url origin >/dev/null 2>&1; then
        git remote set-url origin "$REMOTE_URL"
        echo "Updated existing origin to $REMOTE_URL"
    else
        git remote add origin "$REMOTE_URL"
        echo "Added origin remote $REMOTE_URL"
    fi
fi

# Identity for auto-commits (per-repo, no user-global pollution).
git config user.email "tinm@$(hostname -s)" || true
git config user.name "TINM auto-sync ($(hostname -s))" || true

# Make sure the default branch is main (GitHub default).
current_branch="$(git symbolic-ref --short HEAD 2>/dev/null || echo '')"
if [ -z "$current_branch" ] || [ "$current_branch" != "main" ]; then
    git branch -M main 2>/dev/null || true
fi

# Try to fetch + integrate remote state. If the remote is empty (fresh
# GitHub repo with no commits) the fetch will succeed silently and the
# pull will fail benignly — that's expected on first run from one of
# the two hosts.
if git ls-remote --exit-code origin main >/dev/null 2>&1; then
    git pull origin main --rebase --autostash --quiet || {
        echo "WARN: pull failed. Resolve manually with:"
        echo "  cd $PCP && git status"
        exit 2
    }
    echo "Pulled existing PCP store from remote."
fi

# If we have local content and no upstream yet, do the initial push.
if [ -n "$(ls -A . 2>/dev/null | grep -v '^\.git$' || true)" ]; then
    git add -A
    if ! git diff --cached --quiet; then
        git commit -m "initial: TINM PCP store from $(hostname -s)" --quiet
        echo "Committed local PCP store."
    fi
    if ! git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
        git push -u origin main --quiet || {
            echo "WARN: initial push failed. Likely the GitHub repo does not exist yet,"
            echo "      or the SSH key on this host is not authorized."
            echo "      Verify: ssh -T git@github.com"
            exit 3
        }
        echo "Pushed initial PCP store to $REMOTE_URL."
    fi
fi

echo
echo "OK: git-sync ready."
echo "    Hooks will pull at SessionStart and push after each UserPromptSubmit."
