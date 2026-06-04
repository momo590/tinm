"""Regressions for _hot_path_session_start.

Covers two bugs discovered 2026-05-21:

  Bug #4 — `_append_journal_entry` counted top-level keys of the PCP
           artifacts envelope (always 3) instead of the artifact array
           length. Wrong number has been in every journal entry since
           2026-05-17 and masked a downstream artifact-loading bug.

  Bug #3 — SessionStart context injection (the actual cross-session
           memory value path) had zero telemetry. We now emit
           `session_context_injected` after a non-empty ctx is appended
           to the output chunks, carrying metadata only (never raw text).
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

# Path bootstrap — same convention as test_tinm_hint.py / test_telemetry.py.
SKILL_DIR = Path(__file__).resolve().parent.parent / "skill"
sys.path.insert(0, str(SKILL_DIR))

import _hot_path_session_start as hp  # noqa: E402
import tinm_telemetry  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pcp_tree(tmp_path, monkeypatch):
    """A PCP-shaped tree under tmp_path with TINM_PCP_DIR pointing at it."""
    pcp = tmp_path / "pcp"
    (pcp / "threads").mkdir(parents=True)
    (pcp / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(hp, "TINM_PCP_DIR", pcp)
    monkeypatch.setattr(hp, "TINM_HOME", tmp_path)
    return pcp


def _write_thread(pcp: Path, thread_id: str, *, turns: int = 2, anchor_terms=("a", "b")):
    payload = {
        "pcp_version": "0.2",
        "thread_id": thread_id,
        "trajectory": [
            {"role": "user", "text": f"u{i}"} if i % 2 == 0 else {"role": "assistant", "text": f"a{i}"}
            for i in range(turns)
        ],
        "anchor": {"top_terms": list(anchor_terms)},
    }
    (pcp / "threads" / f"{thread_id}.json").write_text(json.dumps(payload))


def _write_artifacts(pcp: Path, thread_id: str, n_artifacts: int):
    """Write a PCP v0 artifacts envelope with `n_artifacts` entries."""
    payload = {
        "pcp_version": "0.2",
        "thread_id": thread_id,
        "artifacts": [
            {"id": f"art-{i}", "name": f"name-{i}", "summary": "x"}
            for i in range(n_artifacts)
        ],
    }
    (pcp / "artifacts" / f"{thread_id}.json").write_text(json.dumps(payload))


# ===========================================================================
# Bug #4 — journal counter
# ===========================================================================

def _read_journal_entry(pcp: Path) -> dict:
    """The hook writes journal-<host>.jsonl; there is exactly one here."""
    files = sorted(pcp.glob("journal-*.jsonl"))
    assert len(files) == 1, files
    lines = files[0].read_text().splitlines()
    assert len(lines) == 1, lines
    return json.loads(lines[0])


@pytest.mark.parametrize("n_artifacts", [0, 1, 5])
def test_append_journal_entry_counts_artifact_array_not_envelope_keys(
    pcp_tree, n_artifacts
):
    """Pre-fix this always wrote 3 (count of envelope keys). Post-fix it
    must write the actual artifact array length, including 0."""
    thread_id = "t-jcount"
    _write_thread(pcp_tree, thread_id, turns=4)
    _write_artifacts(pcp_tree, thread_id, n_artifacts=n_artifacts)

    hp._append_journal_entry(thread_id)

    entry = _read_journal_entry(pcp_tree)
    assert entry["artifacts"] == n_artifacts, entry
    assert entry["thread"] == thread_id
    assert entry["event"] == "session_start"
    # Sanity: turns counted from user-role messages.
    assert entry["turns"] == 2  # 4 trajectory entries, user role on i=0,2


def test_append_journal_entry_handles_missing_artifacts_file(pcp_tree):
    """No artifacts file → n_art=0, no exception. Same behaviour as before;
    asserted to prevent regressing the OSError-swallowed path."""
    thread_id = "t-no-art"
    _write_thread(pcp_tree, thread_id, turns=2)
    # Deliberately do NOT write artifacts file.

    hp._append_journal_entry(thread_id)

    entry = _read_journal_entry(pcp_tree)
    assert entry["artifacts"] == 0


# ===========================================================================
# Bug #3 — session_context_injected telemetry
# ===========================================================================

@pytest.fixture
def telemetry_tmp(tmp_path, monkeypatch):
    """Redirect telemetry IO into tmp_path so tests never touch ~/.tinm."""
    monkeypatch.setattr(
        tinm_telemetry, "TELEMETRY_FILE", tmp_path / "telemetry.jsonl"
    )
    monkeypatch.setattr(
        tinm_telemetry, "CONFIG_FILE", tmp_path / "telemetry.config.json"
    )
    tinm_telemetry.enable()
    return tmp_path / "telemetry.jsonl"


def _read_telemetry_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_session_context_injected_event_registered_in_schema():
    """Sanity: the event type is in EVENT_FIELDS and validation accepts it."""
    assert "session_context_injected" in tinm_telemetry.EVENT_FIELDS
    fields = tinm_telemetry.EVENT_FIELDS["session_context_injected"]
    assert fields == {
        "thread_id",
        "ctx_chars",
        "trajectory_turns",
        "anchor_terms_count",
        "has_artifacts",
    }


def test_emit_context_injected_telemetry_writes_event(
    pcp_tree, telemetry_tmp
):
    """When a non-empty ctx is injected, we log a session_context_injected
    event with the expected metadata. Payload must NOT include raw ctx."""
    thread_id = "t-ctx"
    _write_thread(pcp_tree, thread_id, turns=6, anchor_terms=("alpha", "beta", "gamma"))
    _write_artifacts(pcp_tree, thread_id, n_artifacts=2)

    ctx = "## TINM context\nrendered summary here\nmore lines"
    hp._emit_context_injected_telemetry(thread_id, ctx)

    events = _read_telemetry_events(telemetry_tmp)
    assert len(events) == 1, events
    ev = events[0]
    assert ev["type"] == "session_context_injected"
    assert ev["thread_id"] == thread_id  # short enough to survive truncation
    assert ev["ctx_chars"] == len(ctx)
    assert ev["trajectory_turns"] == 6
    assert ev["anchor_terms_count"] == 3
    assert ev["has_artifacts"] is True
    # Defense in depth: raw ctx must not appear under any key.
    flat = json.dumps(ev)
    assert "rendered summary here" not in flat
    assert "## TINM context" not in flat


def test_emit_context_injected_telemetry_marks_no_artifacts(
    pcp_tree, telemetry_tmp
):
    thread_id = "t-no-art-tel"
    _write_thread(pcp_tree, thread_id, turns=2)
    _write_artifacts(pcp_tree, thread_id, n_artifacts=0)

    hp._emit_context_injected_telemetry(thread_id, "ctx")

    events = _read_telemetry_events(telemetry_tmp)
    assert len(events) == 1
    assert events[0]["has_artifacts"] is False


def test_emit_context_injected_telemetry_no_thread_file_does_not_raise(
    pcp_tree, telemetry_tmp
):
    """If the thread file is missing the helper logs zeros but never raises."""
    hp._emit_context_injected_telemetry("missing-thread-id", "ctx")
    events = _read_telemetry_events(telemetry_tmp)
    assert len(events) == 1
    ev = events[0]
    assert ev["trajectory_turns"] == 0
    assert ev["anchor_terms_count"] == 0
    assert ev["has_artifacts"] is False


def test_main_emits_telemetry_when_ctx_loaded(
    pcp_tree, telemetry_tmp, monkeypatch, capsys
):
    """End-to-end: main() should call _emit_context_injected_telemetry
    exactly when _load_thread_context returns a non-empty string."""
    thread_id = "t-main"
    _write_thread(pcp_tree, thread_id, turns=4, anchor_terms=("k1", "k2"))
    _write_artifacts(pcp_tree, thread_id, n_artifacts=1)

    monkeypatch.setattr(hp, "_resolve_thread", lambda: thread_id)
    monkeypatch.setattr(hp, "_maybe_emit_upgrade_notif", lambda sid: None)
    monkeypatch.setattr(hp, "_run_writable_gate", lambda tid: None)
    monkeypatch.setattr(
        hp, "_load_thread_context", lambda tid: "## ctx body\n... details ..."
    )
    # No stdin payload.
    monkeypatch.setattr("sys.stdin", _FakeTTY())

    rc = hp.main()
    assert rc == 0

    events = _read_telemetry_events(telemetry_tmp)
    types = [e["type"] for e in events]
    assert "session_context_injected" in types
    sci = next(e for e in events if e["type"] == "session_context_injected")
    assert sci["thread_id"] == thread_id
    assert sci["ctx_chars"] == len("## ctx body\n... details ...")
    assert sci["trajectory_turns"] == 4
    assert sci["anchor_terms_count"] == 2
    assert sci["has_artifacts"] is True


def test_main_does_not_emit_telemetry_when_ctx_empty(
    pcp_tree, telemetry_tmp, monkeypatch
):
    """If load_thread returns falsy, no session_context_injected event."""
    thread_id = "t-main-empty"
    _write_thread(pcp_tree, thread_id, turns=2)
    _write_artifacts(pcp_tree, thread_id, n_artifacts=0)

    monkeypatch.setattr(hp, "_resolve_thread", lambda: thread_id)
    monkeypatch.setattr(hp, "_maybe_emit_upgrade_notif", lambda sid: None)
    monkeypatch.setattr(hp, "_run_writable_gate", lambda tid: None)
    monkeypatch.setattr(hp, "_load_thread_context", lambda tid: None)
    monkeypatch.setattr("sys.stdin", _FakeTTY())

    rc = hp.main()
    assert rc == 0

    events = _read_telemetry_events(telemetry_tmp)
    assert not any(e["type"] == "session_context_injected" for e in events)


def test_export_aggregates_counts_session_context_injections(
    telemetry_tmp,
):
    """Aggregator surfaces a new top-level counter for the value-path event."""
    for i in range(3):
        tinm_telemetry.log_event(
            "session_context_injected",
            {
                "thread_id": f"t-{i}",
                "ctx_chars": 1234,
                "trajectory_turns": 7,
                "anchor_terms_count": 4,
                "has_artifacts": True,
            },
        )
    agg = tinm_telemetry.export_aggregates()
    assert agg["session_context_injections"] == 3
    assert agg["event_counts"]["session_context_injected"] == 3
    # No raw thread_id leakage in aggregates.
    assert "t-0" not in json.dumps(agg)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class _FakeTTY:
    """Stand-in for sys.stdin in main() — isatty() True → no stdin read."""
    def isatty(self):
        return True

    def read(self):
        return ""


# ---------------------------------------------------------------------------
# Bug A (2026-06-04) — SessionStart must maintain ~/.tinm/current_thread so
# the Stop hook's documented fallback actually works for turns >= 2. Before
# this, nothing wrote current_thread; on hosts missing it the Stop hook got
# an empty thread after turn 1 and silently dropped every buffer write, so
# capture went producer-side silent.
# ---------------------------------------------------------------------------

def test_persist_thread_handoff_writes_session_file(pcp_tree, tmp_path):
    hp._persist_thread_handoff("sess-abc", "root-94a6b4")
    session_file = tmp_path / "session-sess-abc.thread"
    assert session_file.exists()
    assert session_file.read_text().strip() == "root-94a6b4"


def test_persist_thread_handoff_writes_current_thread_fallback(pcp_tree, tmp_path):
    """The legacy fallback the Stop hook reads on turns >= 2."""
    current = tmp_path / "current_thread"
    assert not current.exists()
    hp._persist_thread_handoff("sess-abc", "root-94a6b4")
    assert current.exists()
    assert current.read_text().strip() == "root-94a6b4"


def test_persist_thread_handoff_refreshes_stale_current_thread(pcp_tree, tmp_path):
    """A pre-existing current_thread pointing elsewhere is overwritten."""
    (tmp_path / "current_thread").write_text("some-old-thread\n")
    hp._persist_thread_handoff("sess-xyz", "root-94a6b4")
    assert (tmp_path / "current_thread").read_text().strip() == "root-94a6b4"
