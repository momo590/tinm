"""TINM assistant-turn capture flow.

Stop hook side:
    write_buffer(thread_id, session_id, assistant_text, transcript_offset)
        → ~/.tinm/buffer/<host>-<session_id>.json (ephemeral, per-session)

UserPromptSubmit hook side:
    score_and_flush(thread_id, session_id, user_prompt)
        → scores user_prompt against buffered assistant via tinm_approval.score()
        → if w >= 0.7: cmd_add(source="approved_exchange", approval={...})
        → if w <= -0.3: append to pcp/rejected/<thread>.jsonl (observe-only v0.2.1)
        → else: append to pcp/assistant_log/<thread>.jsonl (rolling N=20)
        → always: clear buffer

Spec ref: design/root-tinm-design-20260514-approval-weighted-capture.md §2 + §12.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from lockfile import pcp_lock
from tinm_approval import is_capture, is_rejection, score
from tinm_artifact import cmd_add
from tinm_paths import (
    ASSISTANT_LOG_DIR,
    BUFFER_DIR,
    REJECTED_DIR,
    TINM_PCP_DIR,
)
from tinm_telemetry import log_event

BUFFER_STALE_SECONDS = 300
REJECTED_LOG_CAP = 100
ASSISTANT_LOG_CAP = 20
SUMMARY_CHARS = 500
THINKING_BLOCK_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
HOST = socket.gethostname().replace(".", "-")


# ---------------------------------------------------------------------------
# Buffer (per-session file under ~/.tinm/buffer/)
# ---------------------------------------------------------------------------


def _buffer_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "default")
    return BUFFER_DIR / f"{HOST}-{safe}.json"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, indent=2))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def extract_final_text(transcript_text: str) -> str:
    """Strip thinking blocks; keep prose only.

    Adversarial-review §12.4 finding #4: explicit rule. The Stop hook passes
    the assistant turn's text content (post-final-tool-call). We strip
    <thinking>...</thinking> blocks if present and trim. Empty result → "".
    """
    if not transcript_text:
        return ""
    clean = THINKING_BLOCK_RE.sub("", transcript_text).strip()
    return clean


def write_buffer(thread_id: str, session_id: str, assistant_text: str,
                 anchor_terms: list[str] | None = None) -> bool:
    """Persist the assistant turn so the next UserPromptSubmit can score it.

    Returns True if the buffer was written, False if assistant_text was
    empty (tool-only turn — E9).
    """
    clean = extract_final_text(assistant_text)
    if not clean:
        return False
    payload = {
        "thread_id": thread_id,
        "session_id": session_id,
        "host": HOST,
        "ts": time.time(),
        "ts_iso": _utcnow_iso(),
        "assistant_text": clean,
        "anchor_terms": list(anchor_terms or []),
    }
    _atomic_write(_buffer_path(session_id), payload)
    return True


def read_buffer(session_id: str) -> dict | None:
    p = _buffer_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def clear_buffer(session_id: str) -> None:
    p = _buffer_path(session_id)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Logs (rejected + assistant_log, both per-thread JSONL with rolling cap)
# ---------------------------------------------------------------------------


def _append_with_cap(path: Path, entry: dict, cap: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pcp_lock(TINM_PCP_DIR):
        existing = []
        if path.exists():
            try:
                existing = [
                    json.loads(line)
                    for line in path.read_text().splitlines()
                    if line.strip()
                ]
            except json.JSONDecodeError:
                existing = []
        existing.append(entry)
        if len(existing) > cap:
            existing = existing[-cap:]
        path.write_text("\n".join(json.dumps(e) for e in existing) + "\n")


def append_rejected(thread_id: str, entry: dict) -> None:
    path = REJECTED_DIR / f"{thread_id}.jsonl"
    _append_with_cap(path, entry, REJECTED_LOG_CAP)


def append_assistant_log(thread_id: str, entry: dict) -> None:
    path = ASSISTANT_LOG_DIR / f"{thread_id}.jsonl"
    _append_with_cap(path, entry, ASSISTANT_LOG_CAP)


# ---------------------------------------------------------------------------
# Score-and-flush — the main entrypoint called by UserPromptSubmit hook
# ---------------------------------------------------------------------------


def _summary_for_artifact(text: str) -> str:
    text = text.strip()
    return text[:SUMMARY_CHARS] + ("…" if len(text) > SUMMARY_CHARS else "")


def _artifact_id_for(thread_id: str, ts_iso: str) -> str:
    safe = re.sub(r"[^a-z0-9]", "", ts_iso.lower())
    return f"auto_{thread_id[:16]}_{safe[:14]}"


def score_and_flush(thread_id: str, session_id: str, user_prompt: str,
                    next_user_turn: int | None = None) -> dict:
    """Read buffer, score user_prompt, act, clear buffer.

    Returns a dict {decision, signal_w, label}. Caller uses for telemetry.
    Always clears the buffer (success or noop).
    """
    buf = read_buffer(session_id)
    if buf is None:
        log_event("capture_pipeline_exit", {
            "reason": "no_buffer",
            "thread_id": (thread_id or "")[:32],
        })
        return {"decision": "no_buffer", "signal_w": 0.0, "label": "no_buffer"}

    age = time.time() - float(buf.get("ts", 0))
    if age > BUFFER_STALE_SECONDS:
        clear_buffer(session_id)
        log_event("capture_pipeline_exit", {
            "reason": "stale_discard",
            "thread_id": (thread_id or "")[:32],
        })
        return {"decision": "stale_discard", "signal_w": 0.0,
                "label": "stale", "age_s": age}

    if buf.get("thread_id") and thread_id and buf["thread_id"] != thread_id:
        clear_buffer(session_id)
        log_event("capture_pipeline_exit", {
            "reason": "thread_mismatch",
            "thread_id": (thread_id or "")[:32],
        })
        return {"decision": "thread_mismatch", "signal_w": 0.0,
                "label": "orphan"}

    signal = score(user_prompt)
    log_event("approval_signal_classified", {
        "label": signal.label,
        "signal_w": signal.w,
        "thread_id": thread_id[:32],
    })
    decision = "neutral"

    if is_capture(signal):
        decision = "approved"
        ts_iso = _utcnow_iso()
        try:
            cmd_add(
                thread_id,
                artifact_id=_artifact_id_for(thread_id, ts_iso),
                name=f"Approved exchange: {_summary_for_artifact(buf['assistant_text'])[:80]}",
                ref=f"thread://{thread_id}/turn/{next_user_turn or '?'}",
                summary=_summary_for_artifact(buf["assistant_text"]),
                source="approved_exchange",
                approval={
                    "signal_w": signal.w,
                    "label": signal.label,
                    "trigger_phrase": user_prompt.strip()[:60],
                    "next_user_turn": next_user_turn,
                },
            )
        except (ValueError, RuntimeError):
            decision = "approve_failed"
    elif is_rejection(signal):
        decision = "rejected"
        append_rejected(thread_id, {
            "ts": _utcnow_iso(),
            "thread": thread_id,
            "rejected_assistant_text": _summary_for_artifact(buf["assistant_text"]),
            "rejection_reason": user_prompt.strip()[:200],
            "signal_w": signal.w,
            "label": signal.label,
            "next_user_turn": next_user_turn,
        })
    else:
        append_assistant_log(thread_id, {
            "ts": _utcnow_iso(),
            "label": signal.label,
            "signal_w": signal.w,
            "head": buf["assistant_text"][:120],
        })

    clear_buffer(session_id)
    log_event("assistant_turn_captured", {
        "decision": decision,
        "signal_w": signal.w,
        "thread_id": thread_id[:32],
    })
    return {"decision": decision, "signal_w": signal.w, "label": signal.label}


# ---------------------------------------------------------------------------
# CLI entrypoint (used by hooks)
# ---------------------------------------------------------------------------


def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    bw = sub.add_parser("write_buffer")
    bw.add_argument("--thread-id", required=True)
    bw.add_argument("--session-id", required=True)
    bw.add_argument("--text", required=True)

    sf = sub.add_parser("score_and_flush")
    sf.add_argument("--thread-id", required=True)
    sf.add_argument("--session-id", required=True)
    sf.add_argument("--prompt", required=True)
    sf.add_argument("--next-turn", type=int, default=None)

    cb = sub.add_parser("clear_buffer")
    cb.add_argument("--session-id", required=True)

    args = p.parse_args()
    if args.cmd == "write_buffer":
        ok = write_buffer(args.thread_id, args.session_id, args.text)
        print(json.dumps({"written": ok}))
    elif args.cmd == "score_and_flush":
        r = score_and_flush(args.thread_id, args.session_id, args.prompt,
                            next_user_turn=args.next_turn)
        print(json.dumps(r))
    elif args.cmd == "clear_buffer":
        clear_buffer(args.session_id)
        print(json.dumps({"cleared": True}))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
