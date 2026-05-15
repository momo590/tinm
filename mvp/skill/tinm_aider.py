"""TINM Aider adapter — thin wrapper around tinm_vendor_adapters.

[BETA - schema unverified] Captures user prompts from the Aider CLI via the
wrap-the-binary pattern in tinm_cli_wrap.py. Each user input line typed at
Aider's `> ` prompt is normalized through this module before being piped to
user_prompt.sh.

This module delegates to `tinm_vendor_adapters.normalize_for("aider", ...)`.
Field mappings live in that module's VENDORS["aider"] entry.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

from tinm_vendor_adapters import normalize_for


def normalize(payload: dict) -> Optional[dict]:
    """Normalize an Aider hook payload to TINM's UserPromptSubmit schema."""
    return normalize_for("aider", payload)


def main() -> int:
    """CLI entry: read JSON from stdin, output normalized JSON to stdout."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        print(f"tinm_aider: failed to parse input: {e}", file=sys.stderr)
        return 0  # non-blocking

    result = normalize(payload)
    if result is None:
        print(
            "tinm_aider: no recognizable prompt field in payload. "
            "Run aider_hook.sh with TINM_DEBUG=1 to inspect the JSON schema.",
            file=sys.stderr,
        )
        return 0

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
