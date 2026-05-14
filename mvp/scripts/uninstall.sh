#!/usr/bin/env bash
# TINM uninstaller — fully reversible. Removes:
#   - $HOME/.tinm/ (venv, source, PCP store)
#   - $HOME/.claude/skills/tinm/ (symlinks)
#   - TINM hook entries from $HOME/.claude/settings.json (preserves user's other hooks)
#
# Does NOT touch your Claude Code / Claude Desktop install.
# A backup of settings.json (pre-uninstall) is left at the same path with
# `.pre-uninstall` suffix.
#
# By default, the PCP store ($HOME/.tinm/pcp/) is deleted with everything else.
# Set TINM_KEEP_PCP=1 to preserve it (e.g., if you might reinstall later and
# want to keep your thread history).
set -euo pipefail

TINM_HOME="${TINM_HOME:-$HOME/.tinm}"
CLAUDE_DIR="$HOME/.claude"
CLAUDE_SKILLS_DIR="$CLAUDE_DIR/skills/tinm"
CLAUDE_SETTINGS="$CLAUDE_DIR/settings.json"

echo "→ Uninstalling TINM"

# 1. Strip TINM hook blocks from settings.json
if [ -f "$CLAUDE_SETTINGS" ]; then
    cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.pre-uninstall"
    python3 - "$CLAUDE_SETTINGS" "$CLAUDE_SKILLS_DIR" << 'PYEOF'
import json, sys, pathlib

settings_path = pathlib.Path(sys.argv[1])
skills_dir_prefix = sys.argv[2]

data = json.loads(settings_path.read_text())
hooks = data.get("hooks", {})

def is_tinm_block(block, prefix):
    for h in block.get("hooks", []):
        if h.get("command", "").startswith(prefix):
            return True
    return False

events = list(hooks.keys())
for event in events:
    blocks = hooks[event]
    surviving = [b for b in blocks if not is_tinm_block(b, skills_dir_prefix)]
    if surviving:
        hooks[event] = surviving
    else:
        # Event had ONLY TINM blocks — drop the whole event.
        del hooks[event]

if not data.get("hooks"):
    data.pop("hooks", None)

tmp = settings_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data, indent=2))
tmp.replace(settings_path)
print(f"  scrubbed TINM hooks from {settings_path}")
PYEOF
fi

# 2. Remove symlinks dir
if [ -e "$CLAUDE_SKILLS_DIR" ] || [ -L "$CLAUDE_SKILLS_DIR" ]; then
    rm -rf "$CLAUDE_SKILLS_DIR"
    echo "  removed $CLAUDE_SKILLS_DIR"
fi

# 3. Remove TINM_HOME (venv, source, PCP)
if [ -d "$TINM_HOME" ]; then
    if [ "${TINM_KEEP_PCP:-0}" = "1" ] && [ -d "$TINM_HOME/pcp" ]; then
        # Preserve PCP store outside TINM_HOME.
        moved="$HOME/tinm-pcp-keep-$(date +%s)"
        mv "$TINM_HOME/pcp" "$moved"
        echo "  preserved PCP store at $moved (TINM_KEEP_PCP=1)"
    fi
    rm -rf "$TINM_HOME"
    echo "  removed $TINM_HOME"
fi

cat <<EOF

✓ TINM uninstalled.

Files retained:
  $CLAUDE_SETTINGS.pre-uninstall   (rollback your settings if needed)
EOF
if [ "${TINM_KEEP_PCP:-0}" = "1" ]; then
    echo "  $HOME/tinm-pcp-keep-*           (your thread history)"
fi
echo ""
