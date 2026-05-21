"""Tests for tinm_assistant_capture — telemetry coverage of score_and_flush.

Bug #2 fix: the four exit paths of score_and_flush used to emit telemetry
on only the last two (approval_signal_classified + assistant_turn_captured).
The first three (no_buffer / stale_discard / thread_mismatch) returned
silently, so production "zero events" could mean any of four things.

This module verifies that all four paths now emit observable telemetry, and
that the existing approval_signal_classified + assistant_turn_captured
events are still emitted on the happy path.

All tests redirect:
  - TINM_HOME / BUFFER_DIR via monkeypatch (so we don't touch ~/.tinm/)
  - tinm_telemetry.TELEMETRY_FILE / CONFIG_FILE into tmp_path
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# Path bootstrap — same convention as the other tests in this dir.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "skill"))

import tinm_assistant_capture as tac  # noqa: E402
import tinm_telemetry  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def tinm_tmp(monkeypatch, tmp_path):
    """Redirect every TINM IO surface used by score_and_flush into tmp_path.

    Redirects:
      - The BUFFER_DIR imported into tinm_assistant_capture (per-session
        buffer file lives under here).
      - The PCP store dirs imported into tinm_assistant_capture
        (ASSISTANT_LOG_DIR, REJECTED_DIR, TINM_PCP_DIR — only used on
        the non-early-exit paths but kept consistent so the happy-path
        test doesn't pollute ~/.tinm/).
      - tinm_telemetry's TELEMETRY_FILE + CONFIG_FILE so log_event writes
        into tmp_path instead of ~/.tinm/.
      - Enables telemetry so log_event actually writes (default is OFF).
    """
    buffer_dir = tmp_path / "buffer"
    pcp_dir = tmp_path / "pcp"
    assistant_log_dir = pcp_dir / "assistant_log"
    rejected_dir = pcp_dir / "rejected"
    buffer_dir.mkdir(parents=True, exist_ok=True)
    pcp_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(tac, "BUFFER_DIR", buffer_dir)
    monkeypatch.setattr(tac, "ASSISTANT_LOG_DIR", assistant_log_dir)
    monkeypatch.setattr(tac, "REJECTED_DIR", rejected_dir)
    monkeypatch.setattr(tac, "TINM_PCP_DIR", pcp_dir)

    monkeypatch.setattr(tinm_telemetry, "TELEMETRY_FILE", tmp_path / "telemetry.jsonl")
    monkeypatch.setattr(
        tinm_telemetry, "CONFIG_FILE", tmp_path / "telemetry.config.json"
    )
    tinm_telemetry.enable()

    return {
        "tmp_path": tmp_path,
        "buffer_dir": buffer_dir,
        "telemetry_file": tmp_path / "telemetry.jsonl",
    }


def _read_telemetry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ===========================================================================
# Bug #2 — silent early-exit telemetry coverage
# ===========================================================================


def test_no_buffer_emits_capture_pipeline_exit(tinm_tmp):
    """No buffer file → exactly one capture_pipeline_exit with reason='no_buffer'."""
    result = tac.score_and_flush(
        thread_id="thread-abc",
        session_id="session-xyz-no-buffer",
        user_prompt="anything",
    )
    assert result["decision"] == "no_buffer"

    events = _read_telemetry(tinm_tmp["telemetry_file"])
    exit_events = [e for e in events if e["type"] == "capture_pipeline_exit"]
    assert len(exit_events) == 1, events
    assert exit_events[0]["reason"] == "no_buffer"
    assert exit_events[0]["thread_id"] == "thread-abc"

    # And the two downstream events must NOT have fired — early exit means
    # we never reached the scoring stage.
    assert not [e for e in events if e["type"] == "approval_signal_classified"]
    assert not [e for e in events if e["type"] == "assistant_turn_captured"]


def test_stale_buffer_emits_capture_pipeline_exit(tinm_tmp):
    """Buffer older than BUFFER_STALE_SECONDS → reason='stale_discard'."""
    thread_id = "thread-stale"
    session_id = "session-stale"
    # Write a buffer the normal way first…
    assert tac.write_buffer(thread_id, session_id, "some assistant text") is True
    # …then rewrite it with a deliberately stale ts.
    buf_path = tac._buffer_path(session_id)
    payload = json.loads(buf_path.read_text())
    payload["ts"] = time.time() - 999  # >> BUFFER_STALE_SECONDS (300)
    buf_path.write_text(json.dumps(payload))

    result = tac.score_and_flush(
        thread_id=thread_id,
        session_id=session_id,
        user_prompt="ok cool",
    )
    assert result["decision"] == "stale_discard"
    # Buffer must have been cleared.
    assert not buf_path.exists()

    events = _read_telemetry(tinm_tmp["telemetry_file"])
    exit_events = [e for e in events if e["type"] == "capture_pipeline_exit"]
    assert len(exit_events) == 1, events
    assert exit_events[0]["reason"] == "stale_discard"
    assert exit_events[0]["thread_id"] == thread_id

    # Downstream events must NOT fire on stale path.
    assert not [e for e in events if e["type"] == "approval_signal_classified"]
    assert not [e for e in events if e["type"] == "assistant_turn_captured"]


def test_thread_mismatch_emits_capture_pipeline_exit(tinm_tmp):
    """Buffer thread_id != current thread_id → reason='thread_mismatch'."""
    session_id = "session-mismatch"
    # Buffer was written under thread A…
    assert tac.write_buffer("thread-A", session_id, "assistant text from A") is True
    # …but now we score against thread B.
    result = tac.score_and_flush(
        thread_id="thread-B",
        session_id=session_id,
        user_prompt="parfait",
    )
    assert result["decision"] == "thread_mismatch"
    # Buffer must have been cleared.
    assert not tac._buffer_path(session_id).exists()

    events = _read_telemetry(tinm_tmp["telemetry_file"])
    exit_events = [e for e in events if e["type"] == "capture_pipeline_exit"]
    assert len(exit_events) == 1, events
    assert exit_events[0]["reason"] == "thread_mismatch"
    assert exit_events[0]["thread_id"] == "thread-B"

    # Downstream events must NOT fire.
    assert not [e for e in events if e["type"] == "approval_signal_classified"]
    assert not [e for e in events if e["type"] == "assistant_turn_captured"]


# ===========================================================================
# Existing telemetry events still fire on the happy path
# ===========================================================================


def test_rejection_path_still_emits_signal_and_captured(tinm_tmp):
    """A score_and_flush on the rejection branch still emits the existing
    two telemetry events. (Uses the rejection branch, not the capture
    branch, because the capture branch goes through cmd_add which depends
    on a pre-seeded artifacts/<thread>.json — out of scope here.)"""
    thread_id = "thread-rej"
    session_id = "session-rej"
    assert tac.write_buffer(thread_id, session_id, "Here is the answer.") is True

    # "non" matches the "correction" pattern (w=-0.8) → rejection branch.
    result = tac.score_and_flush(
        thread_id=thread_id,
        session_id=session_id,
        user_prompt="non c'est pas ça",
        next_user_turn=42,
    )
    assert result["decision"] == "rejected"

    events = _read_telemetry(tinm_tmp["telemetry_file"])
    signal_events = [e for e in events if e["type"] == "approval_signal_classified"]
    captured_events = [e for e in events if e["type"] == "assistant_turn_captured"]
    exit_events = [e for e in events if e["type"] == "capture_pipeline_exit"]

    assert len(signal_events) == 1, events
    assert signal_events[0]["thread_id"] == thread_id
    assert signal_events[0]["label"]  # whatever the classifier returned

    assert len(captured_events) == 1, events
    assert captured_events[0]["thread_id"] == thread_id
    assert captured_events[0]["decision"] == "rejected"

    # No early-exit event on this path.
    assert exit_events == []


def test_neutral_path_still_emits_signal_and_captured(tinm_tmp):
    """Neutral user prompts hit the assistant_log branch — telemetry
    must still be the same two events as the happy path (no early exit)."""
    thread_id = "thread-neutral"
    session_id = "session-neutral"
    assert tac.write_buffer(thread_id, session_id, "Here is the answer.") is True

    # Plain question → score returns "question" (w=0.0), neither capture
    # nor rejection → assistant_log branch.
    result = tac.score_and_flush(
        thread_id=thread_id,
        session_id=session_id,
        user_prompt="how do I do X?",
    )
    assert result["decision"] == "neutral"

    events = _read_telemetry(tinm_tmp["telemetry_file"])
    assert len([e for e in events if e["type"] == "approval_signal_classified"]) == 1
    assert len([e for e in events if e["type"] == "assistant_turn_captured"]) == 1
    assert not [e for e in events if e["type"] == "capture_pipeline_exit"]


# ===========================================================================
# Aggregation includes capture_pipeline_exit breakdown
# ===========================================================================


def test_aggregates_break_down_capture_pipeline_exits_by_reason(tinm_tmp):
    """export_aggregates surfaces a {reason: count} dict for the new event."""
    # Trigger each of the three early-exit reasons.
    tac.score_and_flush("t1", "s-no-buf-1", "ok")
    tac.score_and_flush("t1", "s-no-buf-2", "ok")

    # stale
    tac.write_buffer("t1", "s-stale", "answer")
    p = tac._buffer_path("s-stale")
    payload = json.loads(p.read_text())
    payload["ts"] = time.time() - 999
    p.write_text(json.dumps(payload))
    tac.score_and_flush("t1", "s-stale", "ok")

    # mismatch
    tac.write_buffer("t-A", "s-mismatch", "answer")
    tac.score_and_flush("t-B", "s-mismatch", "ok")

    agg = tinm_telemetry.export_aggregates()
    assert agg["capture_pipeline_exits"] == {
        "no_buffer": 2,
        "stale_discard": 1,
        "thread_mismatch": 1,
    }
    assert agg["event_counts"].get("capture_pipeline_exit") == 4
