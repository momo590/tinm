#!/usr/bin/env python3
"""Stop-hook assistant capture: payload-on-stdin → buffer file on disk.

Reads the Claude Code Stop hook payload as a JSON object on stdin, walks
the referenced transcript JSONL backward to find the last assistant turn
that contains non-empty text content, and persists it via
`tinm_assistant_capture.write_buffer(thread_id, session_id, text)`.

Usage (from stop.sh):

    printf '%s' "$PAYLOAD_JSON" | \
        "$VENV_PY" _stop_capture_assistant.py "$THREAD_ID" "$SESSION_ID"

Why a standalone file, not `python - << HEREDOC`:
    `python -` reads the SCRIPT from stdin, so a heredoc clobbers the
    pipe and the JSON payload arrives as the empty string. write_buffer
    therefore never ran in production — zero assistant_capture buffer
    files existed on disk before this fix. See stop.sh comment for the
    minimal repro.

Exit codes:
    0 — buffer written, OR nothing to write (no transcript / empty
        assistant turn / tool-only turn). Best-effort: a Stop hook must
        never block the session.
    The script never raises uncaught exceptions; any failure is logged
    to /tmp/tinm_hook.log via stderr (which stop.sh appends).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import traceback


def _log(msg: str) -> None:
    """Best-effort stderr log — stop.sh redirects this to /tmp/tinm_hook.log."""
    try:
        sys.stderr.write(msg + "\n")
    except Exception:
        pass


def _extract_assistant_text(transcript_path: str) -> str:
    """Walk the transcript backward; return the last assistant turn's text.

    Per E9 (Adversarial-review §12.4 finding #4) we skip tool-only turns
    by requiring non-empty text content. Empty string → caller treats as
    "nothing to capture".
    """
    if not transcript_path:
        return ""
    p = pathlib.Path(transcript_path)
    if not p.is_file():
        return ""

    try:
        with p.open() as f:
            lines = [line for line in f if line.strip()]
    except OSError as exc:
        _log(f"_stop_capture_assistant: transcript read failed: {exc!r}")
        return ""

    for line in reversed(lines):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Claude Code transcripts wrap each message in {"message": {...}, ...}.
        inner = msg.get("message") if isinstance(msg.get("message"), dict) else msg
        if not isinstance(inner, dict) or inner.get("role") != "assistant":
            continue
        content = inner.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = []
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    t = blk.get("text", "")
                    if t:
                        parts.append(t)
            if parts:
                return "\n".join(parts)
    return ""


def _load_write_buffer():
    """Import tinm_assistant_capture.write_buffer from the installed skill dir.

    The script lives in `mvp/hooks/`, but tinm_assistant_capture lives in
    the skill dir which is whatever `$HOME/.claude/skills/tinm` points at
    (a symlink to mvp/skill/ in dev, the real install path in production).
    We honour the same SKILL_DIR convention stop.sh uses.
    """
    skill_dir = os.environ.get("TINM_SKILL_DIR") or str(
        pathlib.Path.home() / ".claude" / "skills" / "tinm"
    )
    if skill_dir not in sys.path:
        sys.path.insert(0, skill_dir)
    from tinm_assistant_capture import write_buffer  # type: ignore[import-not-found]
    return write_buffer


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        _log("_stop_capture_assistant: usage: _stop_capture_assistant.py <thread_id> <session_id>")
        return 0  # best-effort: never block the session

    thread_id = argv[1].strip()
    session_id = argv[2].strip()
    if not thread_id or not session_id:
        return 0

    try:
        raw = sys.stdin.read()
    except Exception as exc:
        _log(f"_stop_capture_assistant: stdin read failed: {exc!r}")
        return 0

    if not raw.strip():
        return 0

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log(f"_stop_capture_assistant: payload JSON decode failed: {exc!r}")
        return 0

    if not isinstance(payload, dict):
        return 0

    transcript_path = payload.get("transcript_path") or ""
    if not isinstance(transcript_path, str):
        return 0

    text = _extract_assistant_text(transcript_path)
    if not text:
        return 0

    try:
        write_buffer = _load_write_buffer()
    except Exception as exc:
        _log(f"_stop_capture_assistant: import write_buffer failed: {exc!r}")
        _log(traceback.format_exc())
        return 0

    try:
        write_buffer(thread_id, session_id, text)
    except Exception as exc:
        _log(f"_stop_capture_assistant: write_buffer failed: {exc!r}")
        _log(traceback.format_exc())
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
