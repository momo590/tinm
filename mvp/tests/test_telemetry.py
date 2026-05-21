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


# ===========================================================================
# Era segmentation — BUG #5 fix
# ===========================================================================
#
# Rationale: pre-v0.3.0 hot-path refactor, latency p95 for user_prompt_submit
# was ~5-7 SECONDS (synchronous embedding). Post-refactor, p95 dropped to ~5ms.
# A combined aggregate hides that regression behind misleading tail latency.
# Era boundaries (derived from the existing `ts` field) let us split the
# series without changing the schema or capturing new content.

def _write_raw_event(tmp_path, *, ts: float, type_: str, **payload):
    """Write a raw event to the telemetry file, bypassing the enable gate.

    Tests need to plant events at specific timestamps spanning the boundary,
    which `log_event` (which stamps `ts = time.time()`) cannot do directly.
    This mirrors the on-disk format exactly.
    """
    import json as _json
    entry = {
        "v": tinm_telemetry.SCHEMA_VERSION,
        "ts": ts,
        "type": type_,
        "install_id": "test-install",
        **payload,
    }
    f = tmp_path / "telemetry.jsonl"
    with f.open("a") as fh:
        fh.write(_json.dumps(entry) + "\n")


# A clean, easy-to-reason-about boundary: midnight UTC on 2026-05-17.
# Pre-era ts: 2026-05-16T00:00:00Z; post-era ts: 2026-05-17T12:00:00Z.
import datetime as _dt
_BOUNDARY_ISO = "2026-05-17T00:00:00Z"
_BOUNDARY_TS = _dt.datetime(2026, 5, 17, 0, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
_PRE_TS = _dt.datetime(2026, 5, 16, 0, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
_POST_TS = _dt.datetime(2026, 5, 17, 12, 0, 0, tzinfo=_dt.timezone.utc).timestamp()


def test_export_by_era_segments_latency_correctly(monkeypatch, tmp_path):
    """Synthetic dataset with 5 pre-boundary slow events and 5 post-boundary
    fast events — era aggregates must reflect the regression cleanly."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    # 5 slow events (5000 ms) before the boundary.
    for ms in [5000.0, 5100.0, 5200.0, 5300.0, 6000.0]:
        _write_raw_event(tmp_path, ts=_PRE_TS, type_="latency_added_ms",
                         hook="user_prompt_submit", duration_ms=ms)
    # 5 fast events (5 ms) after the boundary.
    for ms in [3.0, 4.0, 5.0, 6.0, 7.0]:
        _write_raw_event(tmp_path, ts=_POST_TS, type_="latency_added_ms",
                         hook="user_prompt_submit", duration_ms=ms)

    out = tinm_telemetry.export_aggregates_by_era(boundaries=[_BOUNDARY_ISO])

    assert out["boundaries"] == [_BOUNDARY_ISO]
    assert len(out["eras"]) == 2

    pre, post = out["eras"]
    assert pre["name"] == "pre-2026-05-17"
    assert post["name"] == "post-2026-05-17"

    pre_lat = pre["latency_by_hook"]["user_prompt_submit"]
    post_lat = post["latency_by_hook"]["user_prompt_submit"]

    assert pre_lat["count"] == 5
    assert post_lat["count"] == 5
    # Pre-era p50 = 5200 ms, p95 = 6000 ms (sorted [5000, 5100, 5200, 5300, 6000]).
    assert pre_lat["p50_ms"] == 5200.0
    assert pre_lat["p95_ms"] == 6000.0
    # Post-era p50 = 5 ms, p95 = 7 ms (sorted [3, 4, 5, 6, 7]).
    assert post_lat["p50_ms"] == 5.0
    assert post_lat["p95_ms"] == 7.0


def test_export_by_era_event_counts(monkeypatch, tmp_path):
    """Per-era event_counts must sum back to the combined total."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    # 3 pre-boundary cross_session_hit events.
    for _ in range(3):
        _write_raw_event(tmp_path, ts=_PRE_TS, type_="cross_session_hit",
                         thread_id="t1", k_results=2, top_score=0.5)
    # 7 post-boundary cross_session_hit events.
    for _ in range(7):
        _write_raw_event(tmp_path, ts=_POST_TS, type_="cross_session_hit",
                         thread_id="t2", k_results=3, top_score=0.8)

    out = tinm_telemetry.export_aggregates_by_era(boundaries=[_BOUNDARY_ISO])
    pre, post = out["eras"]
    assert pre["cross_session_hits"] == 3
    assert post["cross_session_hits"] == 7
    assert out["combined"]["cross_session_hits"] == 10
    assert out["combined"]["events_total"] == 10


def test_combined_matches_export_aggregates(monkeypatch, tmp_path):
    """Regression test: the `combined` block of the era export must equal
    the classic `export_aggregates()` payload byte-for-byte (modulo dict
    ordering). Existing consumers must keep working."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    # Mixed dataset spanning the boundary.
    _write_raw_event(tmp_path, ts=_PRE_TS, type_="latency_added_ms",
                     hook="hook_a", duration_ms=100.0)
    _write_raw_event(tmp_path, ts=_POST_TS, type_="latency_added_ms",
                     hook="hook_a", duration_ms=10.0)
    _write_raw_event(tmp_path, ts=_PRE_TS, type_="cross_session_hit",
                     thread_id="t", k_results=1, top_score=0.5)
    _write_raw_event(tmp_path, ts=_POST_TS, type_="user_explicit_action",
                     action="save")
    _write_raw_event(tmp_path, ts=_POST_TS, type_="tokens_saved_estimated",
                     tokens_injected=50, tokens_avoided_estimated=300, source="x")

    classic = tinm_telemetry.export_aggregates()
    era_out = tinm_telemetry.export_aggregates_by_era(boundaries=[_BOUNDARY_ISO])

    assert era_out["combined"] == classic


def test_export_aggregates_unchanged_without_boundaries(monkeypatch, tmp_path):
    """`export_aggregates()` keeps its v0.2 shape — additive change only."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event("user_explicit_action", {"action": "save"})
    tinm_telemetry.log_event("latency_added_ms", {"hook": "h", "duration_ms": 1.0})

    agg = tinm_telemetry.export_aggregates()
    # No new top-level keys leaked from the era refactor.
    # session_context_injections + capture_pipeline_exits added by Bug #3 / Bug #2
    # instrumentation work (not by the era refactor — they are additive counters).
    expected_keys = {
        "install_id",
        "schema_version",
        "events_total",
        "event_counts",
        "tokens_saved_total",
        "cross_session_hits",
        "digest_injections",
        "digest_turns_compressed_total",
        "session_context_injections",
        "capture_pipeline_exits",
        "latency_by_hook",
        "user_actions",
    }
    assert set(agg.keys()) == expected_keys


def test_export_by_era_three_eras(monkeypatch, tmp_path):
    """Two boundaries → three eras, in chronological order, with correct
    placement of events near the boundary edges (`ts < b` falls into the
    EARLIER era)."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    b1 = _dt.datetime(2026, 5, 1, 0, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
    b2 = _dt.datetime(2026, 6, 1, 0, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
    # Era 0: ts < b1
    _write_raw_event(tmp_path, ts=b1 - 1.0, type_="user_explicit_action", action="a")
    # Era 1: b1 <= ts < b2  (boundary edge: ts == b1 goes to era 1)
    _write_raw_event(tmp_path, ts=b1, type_="user_explicit_action", action="b")
    _write_raw_event(tmp_path, ts=b2 - 1.0, type_="user_explicit_action", action="b")
    # Era 2: ts >= b2  (boundary edge: ts == b2 goes to era 2)
    _write_raw_event(tmp_path, ts=b2, type_="user_explicit_action", action="c")

    out = tinm_telemetry.export_aggregates_by_era(boundaries=[
        "2026-06-01T00:00:00Z",  # provided out of order on purpose
        "2026-05-01T00:00:00Z",
    ])
    # Boundaries are sorted ascending.
    assert out["boundaries"] == ["2026-05-01T00:00:00Z", "2026-06-01T00:00:00Z"]
    assert len(out["eras"]) == 3
    e0, e1, e2 = out["eras"]
    assert e0["events_total"] == 1
    assert e1["events_total"] == 2
    assert e2["events_total"] == 1
    assert e0["user_actions"] == {"a": 1}
    assert e1["user_actions"] == {"b": 2}
    assert e2["user_actions"] == {"c": 1}


def test_export_by_era_no_boundaries_returns_single_era(monkeypatch, tmp_path):
    """Zero boundaries = one era named "all" containing every event."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event("user_explicit_action", {"action": "save"})
    tinm_telemetry.log_event("user_explicit_action", {"action": "find"})

    out = tinm_telemetry.export_aggregates_by_era(boundaries=None)
    assert out["boundaries"] == []
    assert len(out["eras"]) == 1
    assert out["eras"][0]["name"] == "all"
    assert out["eras"][0]["events_total"] == 2
    assert out["combined"]["events_total"] == 2


def test_export_by_era_handles_empty_telemetry(monkeypatch, tmp_path):
    """No telemetry file = empty eras + the legacy `events_total: 0` combined."""
    _setup(monkeypatch, tmp_path)
    out = tinm_telemetry.export_aggregates_by_era(boundaries=[_BOUNDARY_ISO])
    assert out["combined"] == {"events_total": 0}
    assert len(out["eras"]) == 2
    for era in out["eras"]:
        assert era["events_total"] == 0


def test_export_by_era_handles_iso_timezone_offsets(monkeypatch, tmp_path):
    """Boundaries with `+00:00` and `Z` suffixes both parse, and naive ISO
    strings are treated as UTC."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    _write_raw_event(tmp_path, ts=_PRE_TS, type_="user_explicit_action", action="pre")
    _write_raw_event(tmp_path, ts=_POST_TS, type_="user_explicit_action", action="post")

    # All three of these refer to the same instant.
    for boundary in [
        "2026-05-17T00:00:00Z",
        "2026-05-17T00:00:00+00:00",
        "2026-05-17T00:00:00",  # naive, treated as UTC
    ]:
        out = tinm_telemetry.export_aggregates_by_era(boundaries=[boundary])
        pre, post = out["eras"]
        assert pre["user_actions"] == {"pre": 1}, boundary
        assert post["user_actions"] == {"post": 1}, boundary


def test_export_by_era_invalid_boundary_raises(monkeypatch, tmp_path):
    """Garbage boundary strings raise ValueError immediately — silent
    parse failure would produce silently-wrong eras."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    with pytest.raises(ValueError):
        tinm_telemetry.export_aggregates_by_era(boundaries=["not-a-date"])


def test_schema_version_unchanged(monkeypatch, tmp_path):
    """Era segmentation is derived from the existing `ts` field — no new
    event metadata captured. SCHEMA_VERSION must remain 1 to honor the
    additive-only privacy contract for this work."""
    assert tinm_telemetry.SCHEMA_VERSION == 1


# ===========================================================================
# CLI tests
# ===========================================================================

def test_cli_export_without_boundaries(monkeypatch, tmp_path, capsys):
    """`export <path>` (no flags) writes the classic payload — no `eras` key."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event("user_explicit_action", {"action": "save"})

    out_file = tmp_path / "out.json"
    rc = tinm_telemetry.cli(["export", str(out_file)])
    assert rc == 0
    payload = json.loads(out_file.read_text())
    assert "eras" not in payload
    assert payload["events_total"] == 1


def test_cli_export_with_era_boundary(monkeypatch, tmp_path):
    """`export <path> --era-boundary <iso>` writes the era-segmented payload."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    _write_raw_event(tmp_path, ts=_PRE_TS, type_="latency_added_ms",
                     hook="h", duration_ms=5000.0)
    _write_raw_event(tmp_path, ts=_POST_TS, type_="latency_added_ms",
                     hook="h", duration_ms=5.0)

    out_file = tmp_path / "out.json"
    rc = tinm_telemetry.cli(["export", str(out_file), "--era-boundary", _BOUNDARY_ISO])
    assert rc == 0
    payload = json.loads(out_file.read_text())
    assert "eras" in payload
    assert "combined" in payload
    assert payload["boundaries"] == [_BOUNDARY_ISO]
    assert len(payload["eras"]) == 2
    assert payload["eras"][0]["latency_by_hook"]["h"]["p50_ms"] == 5000.0
    assert payload["eras"][1]["latency_by_hook"]["h"]["p50_ms"] == 5.0


def test_cli_export_with_multiple_era_boundaries(monkeypatch, tmp_path):
    """Multiple `--era-boundary` flags produce N+1 eras."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event("user_explicit_action", {"action": "save"})

    out_file = tmp_path / "out.json"
    rc = tinm_telemetry.cli([
        "export", str(out_file),
        "--era-boundary", "2026-05-01T00:00:00Z",
        "--era-boundary", "2026-06-01T00:00:00Z",
    ])
    assert rc == 0
    payload = json.loads(out_file.read_text())
    assert len(payload["eras"]) == 3


def test_cli_export_rejects_invalid_boundary(monkeypatch, tmp_path, capsys):
    """Garbage `--era-boundary` value exits non-zero with a clear error."""
    _setup(monkeypatch, tmp_path)
    out_file = tmp_path / "out.json"
    rc = tinm_telemetry.cli(["export", str(out_file), "--era-boundary", "not-a-date"])
    assert rc == 2
    assert not out_file.exists()


def test_cli_export_rejects_dangling_era_boundary(monkeypatch, tmp_path):
    """`--era-boundary` with no following value exits non-zero."""
    _setup(monkeypatch, tmp_path)
    out_file = tmp_path / "out.json"
    rc = tinm_telemetry.cli(["export", str(out_file), "--era-boundary"])
    assert rc == 2


def test_cli_export_era_boundary_equals_form(monkeypatch, tmp_path):
    """`--era-boundary=<iso>` form also works."""
    _setup(monkeypatch, tmp_path)
    tinm_telemetry.enable()
    tinm_telemetry.log_event("user_explicit_action", {"action": "save"})
    out_file = tmp_path / "out.json"
    rc = tinm_telemetry.cli([
        "export", str(out_file), f"--era-boundary={_BOUNDARY_ISO}",
    ])
    assert rc == 0
    payload = json.loads(out_file.read_text())
    assert payload["boundaries"] == [_BOUNDARY_ISO]
