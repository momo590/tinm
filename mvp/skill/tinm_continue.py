"""TINM Continue.dev adapter — normalizes Continue.dev session JSON.

[BETA - schema unverified] Continue.dev is a VS Code/JetBrains extension that
writes session history to `~/.continue/sessions/<session_id>.json`. Unlike
Cursor/Aider/Codex which feed prompts in real time, Continue.dev is captured
post-hoc by importing the session file.

This module exposes:

- `normalize(payload)`        — for the rare case a payload arrives via stdin
- `import_session_file(path)` — bulk-import all user messages from a session
  file into TINM by piping each through user_prompt.sh

CLI:
    python tinm_continue.py --import ~/.continue/sessions/<id>.json
    cat session.json | python tinm_continue.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

# Make sibling modules importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tinm_vendor_adapters import normalize_for  # noqa: E402


def normalize(payload: dict) -> Optional[dict]:
    """Normalize a Continue.dev payload to TINM's UserPromptSubmit schema."""
    return normalize_for("continue", payload)


def _resolve_hook_path() -> Path:
    """Locate user_prompt.sh — same logic as tinm_cli_wrap._resolve_hook_path."""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "hooks" / "user_prompt.sh",
        Path(os.environ.get("TINM_HOME", str(Path.home() / ".tinm")))
        / "source"
        / "mvp"
        / "hooks"
        / "user_prompt.sh",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def _extract_user_messages(session: dict) -> list:
    """Pull user-authored messages out of a Continue.dev session JSON.

    Continue.dev's session schema is not 100% stable, so we try a few shapes:
    - {"messages": [{"role": "user", "content": "..."}, ...]}
    - {"history": [{"role": "user", "content": "..."}, ...]}
    - {"messages": [{"role": "user", "content": [{"type":"text","text":"..."}]}]}
    """
    messages = session.get("messages") or session.get("history") or []
    out = []
    if not isinstance(messages, list):
        return out
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role != "user":
            continue
        content = msg.get("content")
        # String content
        if isinstance(content, str) and content.strip():
            out.append(content.strip())
            continue
        # Multi-part content: list of {"type": "text", "text": "..."}
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    t = part.get("text") or part.get("content")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(part, str):
                    parts.append(part)
            joined = " ".join(p for p in parts if p).strip()
            if joined:
                out.append(joined)
    return out


def import_session_file(path: str) -> int:
    """Read a Continue.dev session JSON and feed each user message to TINM.

    Returns the number of user messages successfully forwarded.
    """
    p = Path(path)
    if not p.is_file():
        print(f"tinm_continue: file not found: {path}", file=sys.stderr)
        return 0

    try:
        session = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"tinm_continue: failed to parse session: {e}", file=sys.stderr)
        return 0

    # Derive a stable session_id from the file name (or generate one)
    session_id = (
        session.get("sessionId")
        or session.get("session_id")
        or session.get("id")
        or p.stem
        or f"continue-{uuid.uuid4().hex[:8]}"
    )

    transcript_path = (
        session.get("sessionHistoryPath")
        or str(p.resolve())
    )

    messages = _extract_user_messages(session)
    if not messages:
        print(
            "tinm_continue: no user messages found in session.",
            file=sys.stderr,
        )
        return 0

    hook = _resolve_hook_path()
    if not hook.is_file():
        print(
            f"tinm_continue: user_prompt.sh not found at {hook}",
            file=sys.stderr,
        )
        return 0

    forwarded = 0
    for text in messages:
        normalized = normalize(
            {
                "text": text,
                "sessionId": session_id,
                "sessionHistoryPath": transcript_path,
            }
        )
        if not normalized:
            continue
        try:
            proc = subprocess.Popen(
                ["bash", str(hook)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            proc.communicate(
                input=json.dumps(normalized).encode(), timeout=10
            )
            forwarded += 1
        except Exception:
            # Skip but keep going on the rest
            continue

    return forwarded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TINM Continue.dev adapter — normalize or import sessions."
    )
    parser.add_argument(
        "--import",
        dest="import_path",
        default=None,
        help="Path to a Continue.dev session JSON to bulk-import.",
    )
    args = parser.parse_args()

    if args.import_path:
        n = import_session_file(args.import_path)
        print(f"tinm_continue: imported {n} user message(s).", file=sys.stderr)
        return 0

    # Default: stdin → normalized JSON on stdout (like the other vendors).
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        print(f"tinm_continue: failed to parse input: {e}", file=sys.stderr)
        return 0

    result = normalize(payload)
    if result is None:
        print(
            "tinm_continue: no recognizable prompt field in payload.",
            file=sys.stderr,
        )
        return 0

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
