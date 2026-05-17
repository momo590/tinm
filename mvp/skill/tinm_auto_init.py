"""Best-effort auto-routing of the current PCP v0 thread at session start.

As of v0.2.3 (thread-isolation design), this module is a thin wrapper
over `tinm_provenance.resolve_thread_for_cwd`. The provenance module
owns all derivation logic (git toplevel, non-git `{basename}-{sha}`
fallback, workspace fingerprinting, seed gating) — DEC-5 in the
design doc.

What changed vs v0.2.2:
  - No more git-only restriction (provenance handles non-git fallback).
  - No more write to `~/.tinm/current_thread`. The session-start hook
    now freezes the resolved thread name in env (Lane C). The global
    pointer is being phased out — it was the root cause of the demo-
    pollution bug observed on 2026-05-17.

Backward compat: `main()` still exists so any installed copy of the
SessionStart hook that invokes this script keeps working — it just
becomes a no-op pointer-wise and returns the resolved thread id on
stderr for `bash -x` debugging.
"""
from __future__ import annotations

import os
import sys

from tinm_provenance import resolve_thread_for_cwd


def auto_init() -> str | None:
    """Resolve the thread for the current cwd. Never writes ambient state."""
    return resolve_thread_for_cwd(os.getcwd())


def main() -> int:
    try:
        result = auto_init()
        if result:
            print(f"tinm_auto_init: resolved thread {result!r}", file=sys.stderr)
    except Exception as e:
        # Never fail the session over auto-init issues.
        print(f"tinm_auto_init: ignored error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
