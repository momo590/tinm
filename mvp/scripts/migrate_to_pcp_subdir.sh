#!/usr/bin/env bash
# One-time migration for Phase 2:
#   $TINM_HOME/threads      ->  $TINM_PCP_DIR/threads
#   $TINM_HOME/artifacts    ->  $TINM_PCP_DIR/artifacts
#
# Phase 1 stored PCP data directly under $TINM_HOME (default ~/.tinm).
# Phase 2 moves the synced subtree into $TINM_PCP_DIR (default
# $TINM_HOME/pcp) so the parent directory can hold machine-local state
# (current_thread, .venv/) outside the sync scope.
#
# Idempotent: skips moves when the target already exists; bails out with a
# warning if both legacy and target are populated (don't auto-merge — let
# the user reconcile).
#
# Override either path via env:
#   TINM_HOME=...      base dir, default ~/.tinm
#   TINM_PCP_DIR=...   synced PCP store, default $TINM_HOME/pcp
set -euo pipefail

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"

echo "TINM_HOME    = $TINM_HOME"
echo "TINM_PCP_DIR = $TINM_PCP_DIR"
echo

migrated=0
skipped_existing=0
conflicts=0

for sub in threads artifacts; do
    legacy="$TINM_HOME/$sub"
    target="$TINM_PCP_DIR/$sub"

    if [ ! -d "$legacy" ]; then
        echo "  $sub: no legacy dir at $legacy (nothing to move)"
        continue
    fi

    if [ -d "$target" ] && [ -n "$(ls -A "$target" 2>/dev/null || true)" ]; then
        echo "  $sub: CONFLICT — both $legacy and $target exist and are non-empty."
        echo "    Refusing to overwrite. Reconcile manually, then re-run."
        conflicts=$((conflicts + 1))
        continue
    fi

    if [ -d "$target" ]; then
        # Empty target — remove and rename so we don't end up with nested dirs.
        rmdir "$target"
    fi

    mkdir -p "$TINM_PCP_DIR"
    mv "$legacy" "$target"
    echo "  $sub: moved $legacy -> $target"
    migrated=$((migrated + 1))
done

echo
echo "Summary: migrated=$migrated  conflicts=$conflicts  legacy-missing=$skipped_existing"

if [ "$conflicts" -gt 0 ]; then
    exit 2
fi

# Sanity check: the current thread (if any) should now resolve under the
# new layout. Don't fail on this — just print, so the user sees what is
# about to happen on the next session.
CURRENT_FILE="$TINM_HOME/current_thread"
if [ -r "$CURRENT_FILE" ]; then
    thread_id="$(tr -d '[:space:]' < "$CURRENT_FILE")"
    if [ -n "$thread_id" ]; then
        if [ -f "$TINM_PCP_DIR/threads/$thread_id.json" ]; then
            echo "OK: current thread '$thread_id' resolves at $TINM_PCP_DIR/threads/$thread_id.json"
        else
            echo "WARN: current_thread points to '$thread_id' but no file at $TINM_PCP_DIR/threads/$thread_id.json"
        fi
    fi
fi
