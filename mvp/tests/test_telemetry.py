"""Tests for tinm_telemetry — privacy contract + schema invariants.

The 4 critical privacy tests at the top are the gating bar:
  1. test_disabled_logs_nothing       — opt-in works (no write when off)
  2. test_aggregates_never_contain_raw_payloads — export never leaks content
  3. test_content_string_truncated_64 — long strings can't leak via overflow
  4. test_unknown_field_dropped       — schema enforces allowlist
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Path bootstrap — same convention as test_tinm_hint.py / test_lock_concurrent.py.
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

import tinm_telemetry  # noqa: E402


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _setup(monkeypatch, tmp_path):
    """Redirect telemetry IO into tmp_path so tests don't touch ~/.tinm/."""
    monkeypatch.setattr(tinm_telemetry, "TELEMETRY_FILE", tmp_path / "telemetry.jsonl")
    monkeypatch.setattr(tinm_telemetry, "CONFIG_FILE", tmp_path / "telemetry.config.json")


# ===========================================================================
# CRITICAL PRIVACY TESTS
# ===========================================================================

def test_disabled_logs_nothing(monkeypatch, tmp_path):
    """When telemetry is disabled (default), log_event must NEVER write."""
    _setup(monkeypatch, tmp_path)
    assert tinm_telemetry.is_enabled() is False
    tinm_telemetry.log_event(
        "cross_session_hit",
        {"thread_id": "t1", "k_results": 3, "top_score": 0.9},
    )
    assert not (tmp_path / "telemetry.jsonl").exists()


def test_aggregates_never_contain_raw_payloads(monkeypatch, tmp_path):
    """Export must produce ONLY counts/aggregates, no `thread_id`, no raw values."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event(
        "cross_session_hit",
        {"thread_id": "secret-thread-name-must-not-leak", "k_results": 1, "top_score": 0.5},
    )
    tinm_telemetry.log_event(
        "user_explicit_action", {"action": "save"},
    )
    tinm_telemetry.log_event(
        "tokens_saved_estimated",
        {"tokens_injected": 100, "tokens_avoided_estimated": 500, "source": "artifact_find"},
    )

    agg = tinm_telemetry.export_aggregates()
    flat = json.dumps(agg)

    # Critical: raw thread_id MUST NOT appear in the aggregate.
    assert "secret-thread-name-must-not-leak" not in flat, flat
    # Counts and action names are aggregated → fine.
    assert agg["cross_session_hits"] == 1
    assert agg["tokens_saved_total"] == 500
    assert agg["user_actions"] == {"save": 1}
    assert agg["events_total"] == 3


def test_content_string_truncated_64(monkeypatch, tmp_path):
    """A string field longer than 64 chars is truncated — no content leakage via overflow."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    long_id = "x" * 200
    tinm_telemetry.log_event(
        "cross_session_hit",
        {"thread_id": long_id, "k_results": 1, "top_score": 0.1},
    )
    entry = json.loads((tmp_path / "telemetry.jsonl").read_text().strip())
    assert len(entry["thread_id"]) == 64
    assert entry["thread_id"] == "x" * 64


def test_unknown_field_dropped(monkeypatch, tmp_path):
    """Fields not in EVENT_FIELDS[event_type] are silently dropped."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event(
        "cross_session_hit",
        {
            "thread_id": "t1",
            "k_results": 2,
            "top_score": 0.5,
            "secret_user_query": "what is the password?",  # NOT in allowlist
            "leaked_chat_text": "very sensitive content",  # NOT in allowlist
        },
    )
    entry = json.loads((tmp_path / "telemetry.jsonl").read_text().strip())
    assert "secret_user_query" not in entry
    assert "leaked_chat_text" not in entry
    # Whitelisted fields still landed.
    assert entry["thread_id"] == "t1"
    assert entry["k_results"] == 2


# ===========================================================================
# Behavioral tests
# ===========================================================================

def test_enabled_writes_jsonl_line(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event(
        "cross_session_hit",
        {"thread_id": "t1", "k_results": 3, "top_score": 0.9},
    )
    log = (tmp_path / "telemetry.jsonl").read_text().strip()
    entry = json.loads(log)
    assert entry["type"] == "cross_session_hit"
    assert entry["v"] == tinm_telemetry.SCHEMA_VERSION
    assert entry["install_id"]


def test_unknown_event_type_raises(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    with pytest.raises(ValueError):
        tinm_telemetry.log_event("invalid_event_type", {})  # type: ignore[arg-type]


def test_purge_deletes_file(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event("user_explicit_action", {"action": "save"})
    assert (tmp_path / "telemetry.jsonl").exists()
    tinm_telemetry.purge()
    assert not (tmp_path / "telemetry.jsonl").exists()


def test_disable_stops_new_events(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event("user_explicit_action", {"action": "find"})
    tinm_telemetry.disable()
    tinm_telemetry.log_event("user_explicit_action", {"action": "save"})
    lines = (tmp_path / "telemetry.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["action"] == "find"


def test_measure_latency_records_event(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    with tinm_telemetry.measure_latency("test_hook"):
        pass
    entry = json.loads((tmp_path / "telemetry.jsonl").read_text().strip())
    assert entry["type"] == "latency_added_ms"
    assert entry["hook"] == "test_hook"
    assert entry["duration_ms"] >= 0


def test_share_aggregates_default_false(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    cfg = tinm_telemetry._read_config()
    assert cfg["share_aggregates"] is False


def test_status_reports_event_count(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    for _ in range(5):
        tinm_telemetry.log_event("user_explicit_action", {"action": "save"})
    s = tinm_telemetry.status()
    assert s["enabled"] is True
    assert s["events_logged"] == 5
    assert s["install_id"]


def test_aggregates_compute_latency_percentiles(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    for i, ms in enumerate([1.0, 2.0, 3.0, 4.0, 5.0]):
        tinm_telemetry.log_event("latency_added_ms", {"hook": "h1", "duration_ms": ms})
    agg = tinm_telemetry.export_aggregates()
    h1 = agg["latency_by_hook"]["h1"]
    assert h1["count"] == 5
    assert h1["p50_ms"] == 3.0
    assert h1["p95_ms"] == 5.0


def test_log_swallows_oserror(monkeypatch, tmp_path):
    """If disk write fails (OSError), log_event must NOT raise — telemetry never breaks user flow."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    # Point TELEMETRY_FILE.parent at a path where mkdir/write will fail.
    # We monkey-patch the file's parent to a path inside a regular file (can't mkdir into a file).
    bad = tmp_path / "regular_file"
    bad.write_text("blocking dir creation")
    monkeypatch.setattr(tinm_telemetry, "TELEMETRY_FILE", bad / "telemetry.jsonl")
    # Should not raise:
    tinm_telemetry.log_event("user_explicit_action", {"action": "save"})
