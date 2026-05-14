#!/usr/bin/env bash
# TINM v0.1 — one-liner installer.
#
# Canonical invocation (assumes the repo is public):
#     curl -fsSL https://raw.githubusercontent.com/momo590/tinm/main/mvp/scripts/install.sh | bash
#
# What it does:
#   1. Verifies platform (macOS / Linux only) and Python ≥ 3.10
#   2. Clones the TINM source to $HOME/.tinm/source (or pulls if already there)
#   3. Creates a Python venv at $HOME/.tinm/.venv and installs deps
#   4. Creates $HOME/.tinm/pcp/ as a local-only git repo (no remote needed)
#   5. Symlinks mvp/skill/*.py and mvp/hooks/*.sh into $HOME/.claude/skills/tinm/
#   6. Registers the 4 hook events in $HOME/.claude/settings.json (with backup)
#   7. Asks the user whether to enable local telemetry
#
# Idempotent: re-running updates source + deps and refreshes symlinks
# without destroying user data (PCP threads/artifacts preserved).
#
# Override these env vars for testing or custom installs:
#   TINM_HOME       — default $HOME/.tinm
#   TINM_REPO_URL   — default https://github.com/momo590/tinm.git
#                     (use git@github.com:momo590/tinm.git if private + you
#                      have SSH access, or a local path for dev testing)
#   TINM_SKIP_PIP   — set to 1 to skip the (slow) pip install step

set -euo pipefail

# Under `curl ... | bash`, stdin is the script body — so any interactive
# prompt later (the telemetry y/n at step 7) reads garbage or gets EOF.
# Re-attach stdin to the user's terminal where possible (Homebrew pattern).
# When there is no controlling tty (CI, headless), keep stdin and rely on
# the non-interactive fallback in tinm_telemetry.install_opt_in_interactive.
if [ ! -t 0 ] && [ -r /dev/tty ]; then
    exec </dev/tty
fi

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
TINM_PCP_DIR="${TINM_PCP_DIR:-$TINM_HOME/pcp}"
TINM_VENV="$TINM_HOME/.venv"
TINM_SOURCE="$TINM_HOME/source"
TINM_REPO_URL="${TINM_REPO_URL:-https://github.com/momo590/tinm.git}"

CLAUDE_DIR="$HOME/.claude"
CLAUDE_SKILLS_DIR="$CLAUDE_DIR/skills/tinm"
CLAUDE_SETTINGS="$CLAUDE_DIR/settings.json"

# ---------------------------------------------------------------------------
# 1. Platform + Python checks
# ---------------------------------------------------------------------------
echo "TINM — cross-session memory for Claude Code. Installing..."
echo "  (so a fresh session can recall yesterday's work without re-pasting context)"
echo ""

case "$(uname -s)" in
    Darwin) PLATFORM="macOS" ;;
    Linux)  PLATFORM="Linux" ;;
    *)
        echo "✗ Unsupported platform: $(uname -s). TINM supports macOS and Linux." >&2
        echo "  Windows support is on the roadmap — DM @MmakhtarDiop on X if you'd like it sooner." >&2
        exit 1
        ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
    echo "✗ python3 not found. Install Python 3.10+ first:" >&2
    if [ "$PLATFORM" = "macOS" ]; then
        echo "    brew install python@3.12" >&2
    else
        echo "    See https://www.python.org/downloads/" >&2
    fi
    exit 1
fi
PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')"
if [ "$PY_OK" != "1" ]; then
    echo "✗ Python $PY_VERSION found, but TINM needs ≥ 3.10." >&2
    exit 1
fi

echo "→ Platform: $PLATFORM · Python: $PY_VERSION"

# ---------------------------------------------------------------------------
# 2. Clone or update source
# ---------------------------------------------------------------------------
if [ -d "$TINM_SOURCE/.git" ]; then
    echo "→ Updating TINM source at $TINM_SOURCE"
    git -C "$TINM_SOURCE" fetch --quiet origin 2>/dev/null || true
    git -C "$TINM_SOURCE" reset --hard origin/main --quiet 2>/dev/null \
        || echo "  (could not fast-forward — keeping current checkout)"
else
    mkdir -p "$TINM_HOME"
    echo "→ Cloning TINM source from $TINM_REPO_URL"
    if ! git clone --quiet "$TINM_REPO_URL" "$TINM_SOURCE"; then
        echo "✗ git clone failed. Possible causes:" >&2
        echo "    - The repo is private and you don't have access." >&2
        echo "    - Network issue / SSH agent not loaded." >&2
        echo "  Try one of:" >&2
        echo "    git clone $TINM_REPO_URL $TINM_SOURCE     # debug the clone manually" >&2
        echo "    TINM_REPO_URL=git@github.com:momo590/tinm.git bash install.sh   # use SSH" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# 3. Create PCP store + init as local git (no remote required)
# ---------------------------------------------------------------------------
mkdir -p "$TINM_PCP_DIR"
if [ ! -d "$TINM_PCP_DIR/.git" ]; then
    git -C "$TINM_PCP_DIR" init --quiet
    git -C "$TINM_PCP_DIR" config user.email "tinm@$(hostname -s)" || true
    git -C "$TINM_PCP_DIR" config user.name "TINM local ($(hostname -s))" || true
    echo "→ Initialized local PCP git repo at $TINM_PCP_DIR (no remote)"
fi

# ---------------------------------------------------------------------------
# 4. Python venv + deps
# ---------------------------------------------------------------------------
if [ ! -d "$TINM_VENV" ]; then
    echo "→ Creating Python venv at $TINM_VENV"
    python3 -m venv "$TINM_VENV"
fi

if [ "${TINM_SKIP_PIP:-0}" = "1" ]; then
    echo "→ TINM_SKIP_PIP=1 — skipping pip install (dev/test mode)"
else
    echo "→ Installing Python dependencies (this can take 1-2 min on first install)"
    "$TINM_VENV/bin/pip" install --quiet --upgrade pip
    "$TINM_VENV/bin/pip" install --quiet -r "$TINM_SOURCE/mvp/requirements.txt"
fi

# ---------------------------------------------------------------------------
# 5. Symlinks in ~/.claude/skills/tinm/
# ---------------------------------------------------------------------------
mkdir -p "$CLAUDE_DIR/skills"

# If a prior install left tinm as a symlink (dev convention), back it up.
if [ -L "$CLAUDE_SKILLS_DIR" ]; then
    backup="$CLAUDE_SKILLS_DIR.pre-install-$(date +%s)"
    mv "$CLAUDE_SKILLS_DIR" "$backup"
    echo "→ Pre-existing symlink moved to $backup"
fi

mkdir -p "$CLAUDE_SKILLS_DIR"

# Refresh symlinks for skill modules + hook scripts. ln -sfn handles
# pre-existing symlinks (-f overrides, -n prevents recurse-into-target).
for f in "$TINM_SOURCE/mvp/skill"/*.py; do
    ln -sfn "$f" "$CLAUDE_SKILLS_DIR/$(basename "$f")"
done
for f in "$TINM_SOURCE/mvp/hooks"/*.sh; do
    ln -sfn "$f" "$CLAUDE_SKILLS_DIR/$(basename "$f")"
done
echo "→ Symlinks installed at $CLAUDE_SKILLS_DIR"

# ---------------------------------------------------------------------------
# 6. Register hooks in ~/.claude/settings.json (idempotent merge)
# ---------------------------------------------------------------------------
mkdir -p "$CLAUDE_DIR"
if [ ! -f "$CLAUDE_SETTINGS" ]; then
    echo "{}" > "$CLAUDE_SETTINGS"
fi

# Backup once per install. If the user has run install.sh before, the
# original (pre-TINM) backup is preserved.
if [ ! -f "$CLAUDE_SETTINGS.tinm-backup" ]; then
    cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.tinm-backup"
    echo "→ Backed up existing settings to $CLAUDE_SETTINGS.tinm-backup"
fi

python3 - "$CLAUDE_SETTINGS" "$CLAUDE_SKILLS_DIR" << 'PYEOF'
import json, sys, pathlib

settings_path = pathlib.Path(sys.argv[1])
skills_dir = pathlib.Path(sys.argv[2])

data = json.loads(settings_path.read_text())
hooks = data.setdefault("hooks", {})

tinm_blocks = {
    "SessionStart": [
        {"hooks": [{"type": "command", "command": str(skills_dir / "session_start.sh")}]}
    ],
    "UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": str(skills_dir / "user_prompt.sh")}]}
    ],
    "PreCompact": [
        {"matcher": "manual", "hooks": [{"type": "command", "command": f"{skills_dir/'pre_compact.sh'} manual"}]},
        {"matcher": "auto",   "hooks": [{"type": "command", "command": f"{skills_dir/'pre_compact.sh'} auto"}]},
    ],
    "PostCompact": [
        {"matcher": "manual", "hooks": [{"type": "command", "command": f"{skills_dir/'pre_compact.sh'} manual"}]},
        {"matcher": "auto",   "hooks": [{"type": "command", "command": f"{skills_dir/'pre_compact.sh'} auto"}]},
    ],
}

def is_tinm_block(block, skills_prefix):
    """True if any command in this block points at our skills dir."""
    for h in block.get("hooks", []):
        cmd = h.get("command", "")
        if cmd.startswith(skills_prefix):
            return True
    return False

prefix = str(skills_dir)
for event, fresh in tinm_blocks.items():
    existing = hooks.get(event, [])
    # Strip prior TINM blocks (idempotent re-install) — keep the user's own.
    preserved = [b for b in existing if not is_tinm_block(b, prefix)]
    hooks[event] = preserved + fresh

tmp = settings_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data, indent=2))
tmp.replace(settings_path)
print(f"→ Registered TINM hooks in {settings_path}")
PYEOF

# ---------------------------------------------------------------------------
# 7. Telemetry opt-in
# ---------------------------------------------------------------------------
echo ""
if [ "${TINM_SKIP_OPT_IN:-0}" = "1" ]; then
    echo "→ TINM_SKIP_OPT_IN=1 — skipping interactive opt-in (telemetry stays OFF by default)"
else
    "$TINM_VENV/bin/python" "$CLAUDE_SKILLS_DIR/tinm_telemetry.py" install || true
fi

# ---------------------------------------------------------------------------
# 8. Success message
# ---------------------------------------------------------------------------
cat <<EOF

══════════════════════════════════════════════════════════════════
✓ TINM v0.1 installed.
══════════════════════════════════════════════════════════════════

Verify the install before starting work:
    $TINM_VENV/bin/python $CLAUDE_SKILLS_DIR/tinm_status.py
You should see: "hooks loaded" and 0 threads / 0 artifacts. That means
TINM is wired into Claude Code's hooks and the PCP store is ready.

Then, in this order:

  1. Restart Claude Code (or Claude Desktop) so it loads the new hooks.

  2. Try the whoa moment without waiting 24h for cross-session recall
     to fire on your own data. Run:
         $TINM_VENV/bin/python $CLAUDE_SKILLS_DIR/tinm_demo.py
     to install the bundled "tinm-tour" demo thread, then in a fresh
     Claude Code session paste:
         "What was the biggest absolute effect we measured on the
          2WikiMultihopQA pilot?"
     Watch the [TINM ...] hook line surface BEFORE Claude responds.
     TINM will quote +0.114 F1, t=4.03 without reading any file.

  3. When you're ready to start your own work:
         /tinm init my-first-thread
     Save anything for cross-session recall:
         /tinm save "my key decision"
     TINM pulls it back in future sessions automatically.

Locations:
  source     $TINM_SOURCE
  venv       $TINM_VENV
  PCP store  $TINM_PCP_DIR
  skills     $CLAUDE_SKILLS_DIR
  settings   $CLAUDE_SETTINGS  (backup: $CLAUDE_SETTINGS.tinm-backup)

Telemetry status (opt-in, local-only by default):
  $TINM_VENV/bin/python $CLAUDE_SKILLS_DIR/tinm_telemetry.py status

Uninstall (fully reversible):
  bash $TINM_SOURCE/mvp/scripts/uninstall.sh

Feedback or issues: DM @MmakhtarDiop on X — even a 1-line "this
surprised me" or "this broke" is exactly what beta v0.1 needs.

EOF
