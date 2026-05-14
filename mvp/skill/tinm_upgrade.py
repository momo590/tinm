"""TINM upgrade — re-runs the idempotent install.sh in place.

install.sh is designed so re-running it on an existing install:
  * Pulls the latest source via git
  * pip-installs any new requirements
  * Refreshes the skill symlinks
  * Re-registers the 4 hooks in ~/.claude/settings.json (idempotent merge,
    preserves the user's other hooks)
  * Does NOT touch ~/.tinm/pcp/ (threads, artifacts, journals, markers
    are preserved across upgrades)

Two invocation paths:
  1. **From a local install** (the script is in ~/.claude/skills/tinm/):
     it just re-runs ~/.tinm/source/mvp/scripts/install.sh.
  2. **Fallback** (no local install.sh found): fetches install.sh from
     the public repo via curl and pipes to bash.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

UPSTREAM_INSTALL_URL = "https://raw.githubusercontent.com/momo590/tinm/main/mvp/scripts/install.sh"
LOCAL_INSTALL_PATH = Path.home() / ".tinm" / "source" / "mvp" / "scripts" / "install.sh"


def main() -> int:
    print("→ TINM upgrade — re-running install.sh (idempotent)")
    if LOCAL_INSTALL_PATH.is_file():
        # Skip the interactive opt-in question on upgrade (config already exists).
        env = {**os.environ, "TINM_SKIP_OPT_IN": "1"}
        print(f"  using local installer at {LOCAL_INSTALL_PATH}")
        rc = subprocess.call(["bash", str(LOCAL_INSTALL_PATH)], env=env)
    else:
        print(f"  no local installer found — fetching from {UPSTREAM_INSTALL_URL}")
        rc = subprocess.call(
            ["bash", "-c", f"curl -fsSL {UPSTREAM_INSTALL_URL} | TINM_SKIP_OPT_IN=1 bash"],
        )

    if rc == 0:
        print("\n✓ TINM upgrade complete. Your threads + artifacts are preserved.")
    else:
        print("\n✗ TINM upgrade failed. Run install.sh manually for full diagnostics.",
              file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
