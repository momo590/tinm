"""TINM Cline adapter — normalizes Cline (VS Code extension) hook JSON.

[BETA - schema unverified] Based on Cline's VS Code task notification
structure. Run cline_hook.sh --debug to verify the actual JSON schema received
from Cline.

This module delegates to `tinm_vendor_adapters.normalize_for("cline", ...)`.
Field mappings live in that module's VENDORS["cline"] entry.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

from tinm_vendor_adapters import normalize_for


def normalize(payload: dict) -> Optional[dict]:
    """Normalize a Cline hook payload to TINM's UserPromptSubmit schema."""
    return normalize_for("cline", payload)


def main() -> int:
    """CLI entry: read JSON from stdin, output normalized JSON to stdout."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        print(f"tinm_cline: failed to parse input: {e}", file=sys.stderr)
        return 0  # non-blocking

    result = normalize(payload)
    if result is None:
        print(
            "tinm_cline: no recognizable prompt field in payload. "
            "Run cline_hook.sh --debug to inspect the JSON schema.",
            file=sys.stderr,
        )
        return 0

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
