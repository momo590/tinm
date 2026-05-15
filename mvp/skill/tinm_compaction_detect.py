"""Detect whether Claude Code's native compaction has fired for a session.

Two detection mechanisms:
1. Marker file: PreCompact hook writes ~/.tinm/pcp/compaction_markers/<session_id>.json
2. Heuristic: transcript line count drops >= 50% turn-over-turn (from transcript_size.jsonl)
"""
from __future__ import annotations

import json
from pathlib import Path

from tinm_paths import TINM_PCP_DIR


def native_compaction_fired(session_id: str) -> bool:
    """True if PreCompact hook wrote a marker for this session."""
    marker = TINM_PCP_DIR / "compaction_markers" / f"{session_id}.json"
    return marker.is_file()


def heuristic_compaction_fired(session_id: str, current_line_count: int) -> bool:
    """True if transcript shrank >= 50% since last recorded line count."""
    log = TINM_PCP_DIR / "transcript_size.jsonl"
    if not log.is_file():
        return False
    try:
        entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        session_entries = [e for e in entries if e.get("session_id") == session_id]
        if len(session_entries) < 2:
            return False
        prev_count = session_entries[-2].get("n_messages", 0)
        return prev_count > 0 and current_line_count < prev_count * 0.5
    except Exception:
        return False


def compaction_active(session_id: str, transcript_line_count: int = 0) -> bool:
    """Combined check: native marker OR heuristic."""
    return native_compaction_fired(session_id) or heuristic_compaction_fired(
        session_id, transcript_line_count
    )
