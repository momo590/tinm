#!/usr/bin/env python3
"""Tiny stdin → session_id helper for the Stop hook.

Reads the Claude Code Stop payload (JSON object) from stdin and prints
the `session_id` field to stdout. No newline. Exits 0 on any parse
failure, printing nothing — the bash caller falls back to legacy
behaviour without aborting the session.

Standalone script (NOT `python - << HEREDOC`) so the stdin pipe carries
the payload, not the script body. See mvp/hooks/stop.sh for the bug
that motivated the split.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    sid = ""
    if isinstance(payload, dict):
        raw = payload.get("session_id")
        if isinstance(raw, str):
            sid = raw
    sys.stdout.write(sid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
