"""Smoke tests for tinm_next_action signal extraction + read-time surface.

The comprehensive coverage lives in mvp/skill/tests/test_next_action.py
(co-located with the skill). This file is the minimal additive set the
v0.3.0 audit asked for: happy path, empty input, explicit next: signal,
and a regex-level sanity check on SIGNAL_PATTERNS. Tests are pure-Python
and scope all I/O under tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))


@pytest.fixture
def isolated_tinm(monkeypatch, tmp_path):
    """Point TINM storage at tmp_path and refresh cached path constants."""
    monkeypatch.setenv("TINM_HOME", str(tmp_path))
    monkeypatch.setenv("TINM_PCP_DIR", str(tmp_path / "pcp"))
    for mod in ("tinm_paths", "tinm_next_action"):
        sys.modules.pop(mod, None)
    return tmp_path


def test_happy_path_extracts_multiple_signals(isolated_tinm):
    from tinm_next_action import extract_signals

    text = "T8 done, tests passing. Next: ship v0.3.0. Waiting for Jordan."
    sigs = extract_signals(text, "user")
    types = {s["signal_type"] for s in sigs}
    assert {"task_done", "unblock", "next_explicit", "pending_ack"} <= types


def test_empty_input_returns_empty_list(isolated_tinm):
    from tinm_next_action import extract_signals

    assert extract_signals("", "user") == []
    assert extract_signals("   \n\t ", "user") == []


def test_explicit_next_marker_takes_priority_in_block(isolated_tinm):
    """A `Next:` line should surface in the rendered block."""
    from tinm_next_action import next_action_block, record_signals
    from tinm_paths import THREADS_DIR

    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    thread = {
        "pcp_version": "0.1",
        "thread_id": "smoke",
        "metadata": {"title": "T", "created_at": "2026-05-17T00:00:00Z",
                     "last_updated": "2026-05-17T00:00:00Z"},
        "anchor": {"update_count": 0, "engaged_so_far": False},
        "trajectory": [
            {"turn": 1, "role": "user", "text": "x", "ts": "2026-05-17T01:00:00Z"}
        ],
    }
    (THREADS_DIR / "smoke.json").write_text(json.dumps(thread))

    n = record_signals("smoke", 1, "user", "Next: ship v0.3.0 tag",
                       ts="2026-05-17T01:00:00Z")
    assert n >= 1

    block = next_action_block("smoke", window=20, max_signals=5)
    assert block is not None
    assert "v0.3.0" in block
    assert "Picking up from" in block


def test_signal_patterns_table_well_formed(isolated_tinm):
    """Each SIGNAL_PATTERNS entry is a (str, compiled_regex, callable)."""
    import re as _re

    from tinm_next_action import SIGNAL_PATTERNS

    assert len(SIGNAL_PATTERNS) >= 6
    for entry in SIGNAL_PATTERNS:
        sig_type, pattern, labeller = entry
        assert isinstance(sig_type, str) and sig_type
        assert isinstance(pattern, _re.Pattern)
        assert callable(labeller)


def test_innocuous_text_yields_no_signals(isolated_tinm):
    """Plain conversation must not over-match (low false-positive rate)."""
    from tinm_next_action import extract_signals

    sigs = extract_signals("Hello, how are you today?", "user")
    assert sigs == []
