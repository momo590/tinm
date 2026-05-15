"""Tests for F7: tinm_digest.py and tinm_compaction_detect.py.

Tests are pure-Python (no subprocess, no model calls, no network).
All file I/O is scoped to tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

import tinm_digest as td
import tinm_compaction_detect as tcd


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_buffer(tmp_path, monkeypatch):
    """Redirect BUFFER_DIR to tmp_path for all tests."""
    buf = tmp_path / "buffer"
    buf.mkdir()
    monkeypatch.setattr(td, "BUFFER_DIR", buf)
    return buf


@pytest.fixture(autouse=True)
def isolated_pcp(tmp_path, monkeypatch):
    """Redirect TINM_PCP_DIR to tmp_path for compaction detect tests."""
    import tinm_paths
    monkeypatch.setattr(tinm_paths, "TINM_PCP_DIR", tmp_path / "pcp")
    monkeypatch.setattr(tcd, "TINM_PCP_DIR", tmp_path / "pcp")
    (tmp_path / "pcp").mkdir(parents=True, exist_ok=True)
    return tmp_path / "pcp"


def _make_transcript(tmp_path: Path, n_lines: int) -> Path:
    """Create a fake transcript JSONL file with n_lines non-empty lines."""
    p = tmp_path / "transcript.jsonl"
    for i in range(n_lines):
        p.open("a").write(json.dumps({"role": "user", "text": f"turn {i}"}) + "\n")
    return p


def _make_digest_file(buf: Path, session_id: str, turn_count: int, text: str = "digest text") -> Path:
    """Write a valid digest file to the buffer directory."""
    f = buf / f"digest-{session_id}-{turn_count}.txt"
    f.write_text(
        f"TINM_DIGEST_V1\n"
        f"session_id: {session_id}\n"
        f"turn_count: {turn_count}\n"
        f"---\n"
        f"{text}\n"
    )
    return f


# ---------------------------------------------------------------------------
# _estimate_tokens_from_transcript
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_file_returns_zero(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        assert td._estimate_tokens_from_transcript(str(p)) == 0

    def test_ten_lines_returns_4000(self, tmp_path):
        p = _make_transcript(tmp_path, 10)
        assert td._estimate_tokens_from_transcript(str(p)) == 4000  # 10 * 400

    def test_missing_file_returns_zero(self, tmp_path):
        assert td._estimate_tokens_from_transcript(str(tmp_path / "nonexistent.jsonl")) == 0

    def test_blank_lines_not_counted(self, tmp_path):
        p = tmp_path / "sparse.jsonl"
        p.write_text("\n\n\n" + json.dumps({"role": "user", "text": "hello"}) + "\n\n")
        # Only 1 non-empty line
        assert td._estimate_tokens_from_transcript(str(p)) == 400


# ---------------------------------------------------------------------------
# should_trigger_digest
# ---------------------------------------------------------------------------

class TestShouldTriggerDigest:
    def test_below_threshold_returns_false(self, tmp_path):
        """Small transcript (< threshold) → False."""
        p = _make_transcript(tmp_path, 5)  # 5 * 400 = 2000 << 40000
        assert td.should_trigger_digest(str(p), "sess1") is False

    def test_above_threshold_returns_true(self, tmp_path, isolated_buffer):
        """Large transcript (> threshold) with no pending digest → True."""
        # threshold = 40000, so we need > 100 lines (100 * 400 = 40000 = boundary)
        p = _make_transcript(tmp_path, 110)  # 110 * 400 = 44000 > 40000
        assert td.should_trigger_digest(str(p), "sess2") is True

    def test_already_pending_returns_false(self, tmp_path, isolated_buffer):
        """If a digest file already exists for this session, do not re-trigger."""
        p = _make_transcript(tmp_path, 110)
        # Plant a pending digest
        _make_digest_file(isolated_buffer, "sess3", 5)
        assert td.should_trigger_digest(str(p), "sess3") is False

    def test_custom_threshold_respected(self, tmp_path, isolated_buffer):
        """Custom threshold via parameter is respected."""
        p = _make_transcript(tmp_path, 3)  # 3 * 400 = 1200
        # threshold = 1000, so 1200 > 1000 → should trigger
        assert td.should_trigger_digest(str(p), "sess4", threshold=1000) is True

    def test_at_threshold_boundary_returns_true(self, tmp_path, isolated_buffer):
        """Exactly at threshold (100 lines * 400 = 40000 == threshold).
        The check is `est < threshold`, so at the boundary est is NOT less than
        threshold — digest IS triggered (>= threshold triggers).
        """
        p = _make_transcript(tmp_path, 100)  # 100 * 400 = 40000 == threshold
        assert td.should_trigger_digest(str(p), "sess5") is True


# ---------------------------------------------------------------------------
# get_pending_digest
# ---------------------------------------------------------------------------

class TestGetPendingDigest:
    def test_valid_digest_returns_text_and_deletes_file(self, tmp_path, isolated_buffer):
        """Valid digest file for current_turn-1 → returns text and removes file."""
        _make_digest_file(isolated_buffer, "sess10", turn_count=5, text="Key decisions:\n- Used Haiku")
        result = td.get_pending_digest("sess10", current_turn=6)
        assert result is not None
        assert "Key decisions" in result
        # File should be deleted after read
        assert not (isolated_buffer / "digest-sess10-5.txt").exists()

    def test_no_file_returns_none(self, tmp_path, isolated_buffer):
        """No digest file → returns None."""
        result = td.get_pending_digest("sess_missing", current_turn=3)
        assert result is None

    def test_stale_turn_count_returns_none(self, tmp_path, isolated_buffer):
        """Digest file with wrong turn_count → returns None (stale)."""
        _make_digest_file(isolated_buffer, "sess11", turn_count=3)
        # current_turn=6 → expects turn_count=5, but file has 3
        result = td.get_pending_digest("sess11", current_turn=6)
        assert result is None
        # Stale file should NOT be deleted (we leave it for debugging)
        assert (isolated_buffer / "digest-sess11-3.txt").exists()

    def test_malformed_no_v1_header_returns_none(self, tmp_path, isolated_buffer):
        """File missing TINM_DIGEST_V1 header → returns None."""
        bad = isolated_buffer / "digest-sess12-4.txt"
        bad.write_text("garbage content\nturn_count: 4\n---\ntext\n")
        result = td.get_pending_digest("sess12", current_turn=5)
        assert result is None

    def test_malformed_no_separator_returns_none(self, tmp_path, isolated_buffer):
        """File with header but no '---' separator → returns None."""
        bad = isolated_buffer / "digest-sess13-2.txt"
        bad.write_text("TINM_DIGEST_V1\nsession_id: sess13\nturn_count: 2\nno separator here\n")
        result = td.get_pending_digest("sess13", current_turn=3)
        assert result is None

    def test_empty_file_returns_none(self, tmp_path, isolated_buffer):
        """Empty file → returns None."""
        bad = isolated_buffer / "digest-sess14-1.txt"
        bad.write_text("")
        result = td.get_pending_digest("sess14", current_turn=2)
        assert result is None

    def test_digest_text_extraction_multiline(self, tmp_path, isolated_buffer):
        """Multiline digest text is returned intact."""
        multiline = "- Decision 1\n- Decision 2\nCurrent goal: Fix the bug"
        _make_digest_file(isolated_buffer, "sess15", turn_count=7, text=multiline)
        result = td.get_pending_digest("sess15", current_turn=8)
        assert "Decision 1" in result
        assert "Decision 2" in result
        assert "Current goal" in result


# ---------------------------------------------------------------------------
# tinm_compaction_detect
# ---------------------------------------------------------------------------

class TestCompactionDetect:
    def test_native_marker_present_returns_true(self, tmp_path, isolated_pcp):
        """Native compaction marker file → native_compaction_fired returns True."""
        markers_dir = isolated_pcp / "compaction_markers"
        markers_dir.mkdir(parents=True)
        marker = markers_dir / "session_abc.json"
        marker.write_text(json.dumps({"session_id": "session_abc", "ts": "2026-05-15T00:00:00Z"}))
        assert tcd.native_compaction_fired("session_abc") is True

    def test_native_marker_absent_returns_false(self, isolated_pcp):
        """No marker file → native_compaction_fired returns False."""
        assert tcd.native_compaction_fired("session_xyz") is False

    def test_heuristic_transcript_halved_returns_true(self, tmp_path, isolated_pcp):
        """transcript line count drops >= 50% → heuristic_compaction_fired returns True."""
        log = isolated_pcp / "transcript_size.jsonl"
        # Two entries: 100 lines then 40 lines (60% drop)
        log.write_text(
            json.dumps({"session_id": "sessH", "n_messages": 100}) + "\n" +
            json.dumps({"session_id": "sessH", "n_messages": 40}) + "\n"
        )
        assert tcd.heuristic_compaction_fired("sessH", current_line_count=40) is True

    def test_heuristic_no_drop_returns_false(self, tmp_path, isolated_pcp):
        """No significant drop → heuristic_compaction_fired returns False."""
        log = isolated_pcp / "transcript_size.jsonl"
        log.write_text(
            json.dumps({"session_id": "sessI", "n_messages": 100}) + "\n" +
            json.dumps({"session_id": "sessI", "n_messages": 80}) + "\n"
        )
        assert tcd.heuristic_compaction_fired("sessI", current_line_count=80) is False

    def test_heuristic_only_one_entry_returns_false(self, tmp_path, isolated_pcp):
        """Only one entry for session → cannot detect heuristic compaction."""
        log = isolated_pcp / "transcript_size.jsonl"
        log.write_text(json.dumps({"session_id": "sessJ", "n_messages": 100}) + "\n")
        assert tcd.heuristic_compaction_fired("sessJ", current_line_count=40) is False

    def test_heuristic_no_log_returns_false(self, isolated_pcp):
        """No transcript_size.jsonl file → heuristic returns False."""
        assert tcd.heuristic_compaction_fired("sessK", current_line_count=5) is False

    def test_heuristic_wrong_session_id_ignored(self, tmp_path, isolated_pcp):
        """Entries for other sessions don't affect the target session."""
        log = isolated_pcp / "transcript_size.jsonl"
        log.write_text(
            json.dumps({"session_id": "other_sess", "n_messages": 100}) + "\n" +
            json.dumps({"session_id": "other_sess", "n_messages": 10}) + "\n"
        )
        # sessNew has no entries at all → False
        assert tcd.heuristic_compaction_fired("sessNew", current_line_count=10) is False

    def test_compaction_active_native_takes_precedence(self, tmp_path, isolated_pcp):
        """compaction_active returns True when native marker is present."""
        markers_dir = isolated_pcp / "compaction_markers"
        markers_dir.mkdir(parents=True)
        (markers_dir / "sess_combo.json").write_text("{}")
        assert tcd.compaction_active("sess_combo", transcript_line_count=100) is True

    def test_compaction_active_heuristic_path(self, tmp_path, isolated_pcp):
        """compaction_active returns True via heuristic when no native marker."""
        log = isolated_pcp / "transcript_size.jsonl"
        log.write_text(
            json.dumps({"session_id": "sessHeur", "n_messages": 200}) + "\n" +
            json.dumps({"session_id": "sessHeur", "n_messages": 80}) + "\n"
        )
        assert tcd.compaction_active("sessHeur", transcript_line_count=80) is True

    def test_compaction_active_neither_returns_false(self, isolated_pcp):
        """compaction_active returns False when no marker and no heuristic signal."""
        assert tcd.compaction_active("sessClean", transcript_line_count=50) is False

    def test_heuristic_boundary_exactly_50_percent_not_triggered(self, tmp_path, isolated_pcp):
        """Exactly 50% drop (not strictly less than 50%) → boundary behavior.
        The check is current < prev * 0.5, so 50 < 100 * 0.5 = 50.0 is False.
        """
        log = isolated_pcp / "transcript_size.jsonl"
        log.write_text(
            json.dumps({"session_id": "sessBound", "n_messages": 100}) + "\n" +
            json.dumps({"session_id": "sessBound", "n_messages": 50}) + "\n"
        )
        # 50 < 100 * 0.5 = 50.0 → False (not triggered at exact boundary)
        assert tcd.heuristic_compaction_fired("sessBound", current_line_count=50) is False

    def test_heuristic_just_below_50_percent_triggers(self, tmp_path, isolated_pcp):
        """49% drop → heuristic fires."""
        log = isolated_pcp / "transcript_size.jsonl"
        log.write_text(
            json.dumps({"session_id": "sessJB", "n_messages": 100}) + "\n" +
            json.dumps({"session_id": "sessJB", "n_messages": 49}) + "\n"
        )
        assert tcd.heuristic_compaction_fired("sessJB", current_line_count=49) is True
