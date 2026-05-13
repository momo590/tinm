"""Single source of truth for TINM storage paths.

Honors `TINM_HOME` and `TINM_PCP_DIR` env vars so Phase 2 multi-host
setups can point the PCP store at a synced folder (Syncthing, iCloud,
Tailscale Drive) while keeping the venv and the per-machine
`current_thread` marker local.

Layout, with defaults:

    ${TINM_HOME:-~/.tinm}/                       # machine-local
        .venv/                                   # arch-specific, NEVER sync
        current_thread                           # per-machine session marker
        ${TINM_PCP_DIR:-${TINM_HOME}/pcp}/       # PCP store — SAFE to sync
            threads/<thread_id>.json
            artifacts/<thread_id>.json
"""
from __future__ import annotations

import os
from pathlib import Path


def _tinm_home() -> Path:
    raw = os.environ.get("TINM_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".tinm"


def _tinm_pcp_dir() -> Path:
    """Resolve the PCP store directory.

    Order of precedence:
      1. Explicit `TINM_PCP_DIR` env var (the Phase 2 escape hatch).
      2. `$TINM_HOME/pcp` if that directory already exists (post-migration).
      3. `$TINM_HOME` itself if a legacy `threads/` lives there but no
         `pcp/` does (Phase 1 install that has not run
         `scripts/migrate_to_pcp_subdir.sh` yet). This keeps the new
         code drop-in compatible — the user can merge first, migrate later.
      4. `$TINM_HOME/pcp` as the forward default (fresh install).
    """
    raw = os.environ.get("TINM_PCP_DIR")
    if raw:
        return Path(raw).expanduser()
    home = _tinm_home()
    pcp = home / "pcp"
    if pcp.exists():
        return pcp
    if (home / "threads").exists():
        return home
    return pcp


TINM_HOME: Path = _tinm_home()
TINM_PCP_DIR: Path = _tinm_pcp_dir()

THREADS_DIR: Path = TINM_PCP_DIR / "threads"
ARTIFACTS_DIR: Path = TINM_PCP_DIR / "artifacts"

CURRENT_FILE: Path = TINM_HOME / "current_thread"
VENV_PYTHON: Path = TINM_HOME / ".venv" / "bin" / "python"
