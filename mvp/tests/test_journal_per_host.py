"""Tests for the per-host journal split (tinm-journal-race-risk fix).

Validates that:
  1. `JOURNAL_FILE` is per-host (name contains hostname, ends in .jsonl).
  2. `JOURNAL_GLOB` matches all per-host files.
  3. `tinm_journal.load_journal()` merges entries from 2+ hosts into one
     chronologically-sorted stream.
  4. The migrated legacy file (`journal-legacy-pre-*.jsonl`) is included
     by the aggregation glob.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

# Path bootstrap — match the existing test convention in test_tinm_hint.py.
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

import tinm_paths  # noqa: E402
import tinm_journal  # noqa: E402


# ---------------------------------------------------------------------------
# 1) Naming
# ---------------------------------------------------------------------------

def test_journal_file_is_per_host():
    """JOURNAL_FILE must be of the form `journal-<hostname>.jsonl`."""
    name = tinm_paths.JOURNAL_FILE.name
    assert name.startswith("journal-"), name
    assert name.endswith(".jsonl"), name
    # Body is non-empty and contains no `.` (would have been replaced).
    body = name[len("journal-"):-len(".jsonl")]
    assert body, name
    assert "." not in body, body


def test_journal_glob_pattern():
    """JOURNAL_GLOB is the canonical match pattern."""
    assert tinm_paths.JOURNAL_GLOB == "journal-*.jsonl"


def test_journal_glob_matches_per_host_and_legacy(tmp_path):
    """The glob picks up both per-host and legacy-migrated files."""
    (tmp_path / "journal-MacBook.jsonl").write_text("")
    (tmp_path / "journal-vps1.jsonl").write_text("")
    (tmp_path / "journal-legacy-pre-2026-05-14.jsonl").write_text("")
    (tmp_path / "unrelated.jsonl").write_text("")

    matches = sorted(p.name for p in tmp_path.glob(tinm_paths.JOURNAL_GLOB))
    assert matches == [
        "journal-MacBook.jsonl",
        "journal-legacy-pre-2026-05-14.jsonl",
        "journal-vps1.jsonl",
    ]


# ---------------------------------------------------------------------------
# 2) Aggregation
# ---------------------------------------------------------------------------

def _entry(ts: str, thread: str, turns: int = 1, artifacts: int = 0) -> str:
    return json.dumps({
        "ts": ts,
        "event": "session_start",
        "thread": thread,
        "turns": turns,
        "artifacts": artifacts,
        "anchor": [],
    }) + "\n"


def test_aggregation_merges_multi_host(tmp_path, monkeypatch):
    """Two hosts' journals merge into a single ordered stream."""
    (tmp_path / "journal-host1.jsonl").write_text(
        _entry("2026-05-14T10:00:00Z", "t1")
        + _entry("2026-05-14T12:00:00Z", "t1")
    )
    (tmp_path / "journal-host2.jsonl").write_text(
        _entry("2026-05-14T11:00:00Z", "t2")
    )

    monkeypatch.setattr(tinm_journal, "PCP_DIR", tmp_path)

    entries = tinm_journal.load_journal(days=365)
    assert len(entries) == 3
    # Must be sorted chronologically by ts.
    assert [e["ts"] for e in entries] == [
        "2026-05-14T10:00:00Z",
        "2026-05-14T11:00:00Z",
        "2026-05-14T12:00:00Z",
    ]
    assert [e["thread"] for e in entries] == ["t1", "t2", "t1"]


def test_aggregation_includes_legacy_file(tmp_path, monkeypatch):
    """A migrated legacy file is read by the aggregator."""
    (tmp_path / "journal-legacy-pre-2026-05-14.jsonl").write_text(
        _entry("2026-04-01T09:00:00Z", "old-thread")
    )
    (tmp_path / "journal-MacBook.jsonl").write_text(
        _entry("2026-05-14T15:00:00Z", "new-thread")
    )

    monkeypatch.setattr(tinm_journal, "PCP_DIR", tmp_path)

    entries = tinm_journal.load_journal(days=365)
    threads = {e["thread"] for e in entries}
    assert threads == {"old-thread", "new-thread"}


def test_aggregation_respects_days_cutoff(tmp_path, monkeypatch):
    """Entries older than the cutoff are excluded."""
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    (tmp_path / "journal-host1.jsonl").write_text(
        _entry(old, "old-thread") + _entry(recent, "recent-thread")
    )

    monkeypatch.setattr(tinm_journal, "PCP_DIR", tmp_path)

    entries = tinm_journal.load_journal(days=7)
    assert len(entries) == 1
    assert entries[0]["thread"] == "recent-thread"


def test_aggregation_handles_empty_pcp_dir(tmp_path, monkeypatch):
    """No journal files → empty list, not error."""
    monkeypatch.setattr(tinm_journal, "PCP_DIR", tmp_path)
    assert tinm_journal.load_journal(days=7) == []
