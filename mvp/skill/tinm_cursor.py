"""TINM Cursor adapter — normalizes Cursor hook JSON to TINM's UserPromptSubmit format.

[BETA - schema unverified] Based on Cursor's documented beforeSubmitPrompt hook API.
Run cursor_hook.sh --debug to verify the actual JSON schema received from Cursor.

Cursor's beforeSubmitPrompt hook (documented as of Cursor 1.7+) passes a JSON
payload on stdin. Known field names (may vary):
  - prompt / text / message / input: the user's message text
  - session_id / sessionId: session identifier
  - model: the model being used
  - project_root / workspaceRoot: project directory

We normalize to TINM's UserPromptSubmit schema:
  {
    "prompt": str,           # the user's message
    "session_id": str,       # session identifier (generated if missing)
    "transcript_path": str,  # empty string if unavailable from Cursor
    "hook_type": "cursor_beforeSubmitPrompt"  # for adapter identification
  }
"""
from __future__ import annotations

import json
import sys
import uuid
from typing import Optional


# Field name candidates for the prompt text, in priority order
_PROMPT_FIELDS = ["prompt", "text", "message", "input", "query", "content"]

# Field name candidates for session_id
_SESSION_FIELDS = ["session_id", "sessionId", "session", "conversation_id", "conversationId"]


def normalize(payload: dict) -> Optional[dict]:
    """Normalize a Cursor hook payload to TINM's UserPromptSubmit schema.

    Returns None if the payload does not contain a recognizable prompt field.
    """
    # Extract prompt text
    prompt_text = None
    for field in _PROMPT_FIELDS:
        val = payload.get(field)
        if isinstance(val, str) and val.strip():
            prompt_text = val.strip()
            break
        # Handle nested: {"message": {"text": "..."}}
        if isinstance(val, dict):
            for sub in _PROMPT_FIELDS:
                sub_val = val.get(sub)
                if isinstance(sub_val, str) and sub_val.strip():
                    prompt_text = sub_val.strip()
                    break
        if prompt_text:
            break

    if not prompt_text:
        return None

    # Extract session_id
    session_id = None
    for field in _SESSION_FIELDS:
        val = payload.get(field)
        if isinstance(val, str) and val.strip():
            session_id = val.strip()
            break
    if not session_id:
        session_id = f"cursor-{uuid.uuid4().hex[:8]}"

    return {
        "prompt": prompt_text,
        "session_id": session_id,
        "transcript_path": "",  # Cursor doesn't expose transcript path
        "hook_type": "cursor_beforeSubmitPrompt",
    }


def main() -> int:
    """CLI entry: read JSON from stdin, output normalized JSON to stdout."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        print(f"tinm_cursor: failed to parse input: {e}", file=sys.stderr)
        return 0  # non-blocking

    result = normalize(payload)
    if result is None:
        print(
            "tinm_cursor: no recognizable prompt field in payload. "
            "Run cursor_hook.sh --debug to inspect the JSON schema.",
            file=sys.stderr,
        )
        return 0

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
