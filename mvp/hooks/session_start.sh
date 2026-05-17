#!/bin/bash
# TINM SessionStart hook — auto-load the current thread's context block at
# the beginning of every Claude Code session.
#
# Stdout from a SessionStart hook is added to Claude's context (per
# https://code.claude.com/docs/en/hooks). We use that to surface the
# trajectory + artifacts so Claude has the user's prior work in mind from
# turn 1 onwards. If no thread can be resolved for this cwd, we exit
# quietly so the session is unchanged.
#
# ── v0.2.3 thread-isolation (DEC-2 freeze-at-start) ──────────────────────
# Per the design (design-thread-isolation-2026-05-17.md), the thread for a
# session is RESOLVED ONCE at SessionStart from $PWD via
# tinm_provenance.resolve_thread_for_cwd, then FROZEN for the rest of the
# session. cd'ing mid-session must NOT switch threads.
#
# Cross-process channel: a per-session file at
#   ${TINM_HOME}/session-<session_id>.thread
# holds the resolved thread name so user_prompt.sh and stop.sh — which
# run in independent shell invocations — can read it without re-resolving.
# Stop.sh deletes this file when the session ends so the directory does
# not accumulate stale handoffs.
#
# Choice rationale (per-session FILE vs hook env passing): Claude Code
# does not propagate env vars between separate hook invocations — each
# hook runs in its own bash process. A file keyed by the session_id
# (which Claude Code does provide in every hook's stdin JSON payload)
# is the only reliable cross-hook channel. Files also survive a Claude
# Code crash mid-session well enough for the next session to clean up.
# ─────────────────────────────────────────────────────────────────────────

set -e

# Read stdin once — Claude Code passes session_id + transcript_path JSON
# even to SessionStart. We need session_id to key the per-session
# handoff file. Missing-stdin (manual hook test) → empty payload, we
# fall back to $$ below.
PAYLOAD_JSON=""
if [ ! -t 0 ]; then
    PAYLOAD_JSON="$(cat)"
fi

# TINM paths — honor TINM_HOME / TINM_PCP_DIR for Phase 2 multi-host
# setups (e.g. Mac<->Linux via Syncthing-over-Tailscale, or any two
# POSIX hosts sharing a remote). Defaults match the Phase 1 single-host
# layout. session handoff files + venv stay under TINM_HOME (machine-local);
# the PCP store goes under TINM_PCP_DIR.
TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
export TINM_HOME TINM_PCP_DIR

VENV_PY="$TINM_HOME/.venv/bin/python"
SKILL_DIR="$HOME/.claude/skills/tinm"
LOAD_SCRIPT="$SKILL_DIR/tinm_load.py"
AUTO_INIT_SCRIPT="$SKILL_DIR/tinm_auto_init.py"
PROVENANCE_SCRIPT="$SKILL_DIR/tinm_provenance.py"

# Per-host hook warning log — used for provenance gate refusals + other
# diagnostic chatter that we deliberately keep out of Claude's context.
HOOK_WARN_LOG="$TINM_HOME/hook-warn-$(hostname | tr '.' '-').log"

# Phase 2 sync (best-effort, v0.2.2): if the PCP store is a git repo, pull
# the latest snapshot from the remote so this host's session starts with the
# freshest threads/artifacts the peer pushed.
#
# v0.2.2 fix: --autostash only stashes TRACKED modifications. When two
# hosts independently create the same file path, pulling fails with
# "untracked working tree files would be overwritten by checkout" — the
# error was being silenced by 2>/dev/null and the user saw stale state.
# Switch to explicit stash --include-untracked, log everything to a per-host
# sync log, and reset working tree on pop conflict so the session is never
# blocked. Failure is still non-fatal — a dropped network or transient
# remote error must not block the session.
if [ -d "$TINM_PCP_DIR/.git" ]; then
    _SYNC_LOG="$TINM_HOME/sync-$(hostname).log"
    _SYNC_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    {
        echo "=== $_SYNC_TS session_start sync from $(hostname) ==="
        _STASHED=0
        if [ -n "$(git -C "$TINM_PCP_DIR" status --porcelain 2>/dev/null)" ]; then
            git -C "$TINM_PCP_DIR" stash push --include-untracked \
                -m "tinm-session-start-autostash-$_SYNC_TS" --quiet 2>&1 \
                && _STASHED=1 || true
        fi
        if git -C "$TINM_PCP_DIR" pull --rebase --quiet 2>&1; then
            _PULL_OK=1
        else
            _PULL_OK=0
            echo "WARN: pull failed; aborting rebase if active"
            git -C "$TINM_PCP_DIR" rebase --abort 2>&1 || true
        fi
        if [ "$_STASHED" = "1" ]; then
            if ! git -C "$TINM_PCP_DIR" stash pop --quiet 2>&1; then
                echo "WARN: stash pop conflict; resetting working tree, work preserved in stash list"
                git -C "$TINM_PCP_DIR" checkout -- . 2>&1 || true
                git -C "$TINM_PCP_DIR" clean -fd 2>&1 || true
                git -C "$TINM_PCP_DIR" stash list 2>&1 | head -1
            fi
        fi
        echo "=== exit (pull_ok=$_PULL_OK stashed=$_STASHED) ==="
    } >> "$_SYNC_LOG" 2>&1 || true
fi

# Extract session_id from the JSON payload BEFORE F1 so that the upgrade
# notification can write a per-session "already shown" marker — which
# user_prompt.sh reads to suppress duplicate mid-session notifications.
#
# If session_id is absent (manual hook test or pre-spec Claude Code
# build), we fall back to the hook PID for SESSION_KEY. The fallback is
# intentionally fragile: user_prompt.sh will receive a real session_id
# and look for a different file, which means the legacy current_thread
# fallback in user_prompt.sh kicks in. That's the safe path — never
# break a session over a missing session_id.
SESSION_ID=""
if [ -n "$PAYLOAD_JSON" ] && [ -x "$VENV_PY" ]; then
    SESSION_ID="$(printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" -c \
        'import json, sys
try:
    print(json.load(sys.stdin).get("session_id", ""), end="")
except Exception:
    pass' 2>/dev/null)"
fi
SESSION_KEY="${SESSION_ID:-$$}"
SESSION_THREAD_FILE="$TINM_HOME/session-${SESSION_KEY}.thread"
# Per-session marker that the upgrade notif has been surfaced. Created
# by F1 below (SessionStart side) OR by user_prompt.sh (mid-session
# side); whichever fires first wins. Removed by stop.sh.
SESSION_UPGRADE_MARKER="$TINM_HOME/session-${SESSION_KEY}.upgrade-shown"

# F1: Upgrade notification (best-effort, 24h cached, 3s timeout, silent on error).
# Emits a single line to stdout (injected into Claude's context by Claude Code).
# Format: 💡 TINM vX.Y.Z available — respond 'upgrade' at the start of your
#         next message to install automatically.
# Controlled by ~/.tinm/config.json:update_notify (default true) and
# auto_upgrade (default false).
#
# On emit, we also touch the per-session marker so user_prompt.sh's
# mid-session L1 check suppresses a duplicate notif this session.
if [ -x "$VENV_PY" ]; then
    _UPGRADE_OUTPUT="$(SESSION_UPGRADE_MARKER="$SESSION_UPGRADE_MARKER" \
        timeout 3s "$VENV_PY" - << 'PYEOF' 2>/dev/null || true
import os, sys, pathlib, subprocess
sys.path.insert(0, str(pathlib.Path.home() / ".claude" / "skills" / "tinm"))
try:
    from tinm_config import get_config
    from tinm_update_check import check_for_update
    cfg = get_config()
    if not cfg.get("update_notify", True):
        sys.exit(0)
    r = check_for_update()
    if not r.get("has_update"):
        sys.exit(0)
    current, latest = r.get("current"), r.get("latest")
    if cfg.get("auto_upgrade", False):
        upgrade_script = pathlib.Path.home() / ".claude" / "skills" / "tinm" / "tinm_upgrade.py"
        subprocess.Popen(
            [sys.executable, str(upgrade_script)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"\U000026ab TINM v{latest} available — auto-upgrading in background.")
    else:
        print(
            f"\U0001f4a1 TINM v{latest} available — respond 'upgrade' at the start of your next message to install automatically."
        )
    # Drop a marker so user_prompt.sh's mid-session L1 check does not
    # re-emit the same notif within this session. Best-effort: write
    # failure must not block the rest of the hook.
    marker = os.environ.get("SESSION_UPGRADE_MARKER", "")
    if marker:
        try:
            p = pathlib.Path(marker)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        except OSError:
            pass
except Exception:
    pass
PYEOF
)"
    if [ -n "$_UPGRADE_OUTPUT" ]; then
        printf '%s\n' "$_UPGRADE_OUTPUT"
    fi
fi

# ── DEC-2: Resolve the thread ONCE for this cwd, freeze it ───────────────
# Venv must be installed before we can call provenance. Fail silently if
# not yet provisioned — the user is mid-install.
if [ ! -x "$VENV_PY" ] || [ ! -r "$PROVENANCE_SCRIPT" ]; then
    exit 0
fi

# Resolve thread for cwd via the single source of truth.
# Stdout = thread_id (or empty on failure); exit 1 means "no thread".
TINM_SESSION_THREAD="$( cd "$PWD" && SKILL_DIR="$SKILL_DIR" \
    "$VENV_PY" -c "
import os, sys
sys.path.insert(0, os.environ['SKILL_DIR'])
from tinm_provenance import resolve_thread_for_cwd
r = resolve_thread_for_cwd(os.getcwd())
if r:
    print(r, end='')
" 2>>"$HOOK_WARN_LOG" )"

if [ -z "$TINM_SESSION_THREAD" ]; then
    # No thread for this cwd — exit cleanly. Note: do NOT touch
    # current_thread; under v0.2.3 it is being phased out. The legacy
    # global pointer remains untouched so v0.2.2 installs in the middle
    # of upgrading do not regress.
    exit 0
fi

# Persist the resolved thread name so user_prompt.sh + stop.sh — which
# run in separate shell processes — can read it without re-resolving.
# Stop.sh deletes this file at session end.
mkdir -p "$TINM_HOME"
printf '%s\n' "$TINM_SESSION_THREAD" > "$SESSION_THREAD_FILE"

# DEC-4 cross-host bridge prompt is OUT OF SCOPE (Lane E). For now, run
# the writable check; on refusal, log a one-line warning to the per-host
# log and exit 0. The session stays usable — UserPromptSubmit will read
# the file and either find the gate is now bridged (Lane E lands the
# interactive prompt) or simply re-flag the refusal each turn.
SKILL_DIR="$SKILL_DIR" "$VENV_PY" - "$TINM_SESSION_THREAD" "$HOOK_WARN_LOG" \
    2>>"$HOOK_WARN_LOG" << 'PYEOF' || true
import json, os, sys, datetime, pathlib
sys.path.insert(0, os.environ['SKILL_DIR'])
from tinm_provenance import compute_fingerprint, check_thread_writable
from tinm_paths import THREADS_DIR

thread_id, warn_log = sys.argv[1], sys.argv[2]
tp = THREADS_DIR / f"{thread_id}.json"
if not tp.exists():
    sys.exit(0)
try:
    t = json.loads(tp.read_text())
except (json.JSONDecodeError, OSError):
    sys.exit(0)
fp = compute_fingerprint(os.getcwd())
ok, reason = check_thread_writable(t, fp)
if not ok:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} session_start gate-refused thread={thread_id} cwd={os.getcwd()} reason={reason}\n"
    try:
        with open(warn_log, "a") as f:
            f.write(line)
    except OSError:
        pass
PYEOF

# Run the loader and let its markdown stdout enter Claude's context.
# Stderr is dropped so warnings (e.g., LibreSSL noise) do not pollute.
if [ -r "$LOAD_SCRIPT" ]; then
    "$VENV_PY" "$LOAD_SCRIPT" "$TINM_SESSION_THREAD" 2>/dev/null || true
fi

# Auto-journal: append a session_start entry to pcp/journal-<hostname>.jsonl.
# Per-host file (not journal.jsonl) avoids the cross-host git race when
# both ends append concurrently. Reads in tinm_journal.py glob
# journal-*.jsonl to reconstruct the cross-host stream.
"$VENV_PY" - "$TINM_SESSION_THREAD" "$TINM_PCP_DIR" << 'PYEOF' 2>/dev/null || true
import json, sys, datetime, pathlib, socket
thread_id, pcp_dir = sys.argv[1], sys.argv[2]
journal_path = pathlib.Path(pcp_dir) / f"journal-{socket.gethostname().replace('.', '-')}.jsonl"
try:
    t = json.loads((pathlib.Path(pcp_dir) / "threads" / f"{thread_id}.json").read_text())
    turns = len([x for x in t.get("trajectory", []) if x.get("role") == "user"])
    anchor = t.get("anchor", {}).get("top_terms", [])[:4]
except Exception:
    turns, anchor = 0, []
try:
    a = json.loads((pathlib.Path(pcp_dir) / "artifacts" / f"{thread_id}.json").read_text())
    n_art = len(a)
except Exception:
    n_art = 0
entry = {
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "thread": thread_id, "turns": turns, "anchor": anchor,
    "artifacts": n_art, "event": "session_start"
}
with open(journal_path, "a") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
PYEOF

# Clipboard watcher auto-start (opt-in via ~/.tinm/clipboard_enabled flag).
# Detached background daemon — survives this hook's exit. No-op if not
# opted in or already running. Privacy: the trigger phrase requirement
# means clipboard content is never captured without explicit user intent.
CLIPBOARD_SCRIPT="$SKILL_DIR/tinm_clipboard.py"
if [ -x "$VENV_PY" ] && [ -r "$CLIPBOARD_SCRIPT" ] && [ -f "$TINM_HOME/clipboard_enabled" ]; then
    "$VENV_PY" "$CLIPBOARD_SCRIPT" --ensure-daemon 2>/dev/null || true
fi

exit 0
