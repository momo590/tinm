"""Tests for tinm_next_action — L4 next-action signal extractor (v1 MVP).

Coverage targets (from the L4 wedge spec):
  - each of the 6 signal types detected (FR + EN)
  - signals JSONL persisted correctly (one line per signal, ts/turn/role attached)
  - surface block reads back from sidecar
  - K-turn window respected (no signals older than K → no block)
  - empty trajectory → next_action_block returns None
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def tinm_tmp(monkeypatch, tmp_path):
    """Isolate TINM storage to a tmp dir and reload modules that cached paths."""
    monkeypatch.setenv("TINM_HOME", str(tmp_path))
    monkeypatch.setenv("TINM_PCP_DIR", str(tmp_path / "pcp"))
    for mod in ["tinm_paths", "tinm_next_action"]:
        sys.modules.pop(mod, None)
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# Pure detection — each of the 6 signal types, FR + EN
# ─────────────────────────────────────────────────────────────────────────────


def _types(turn_text: str, role: str = "user") -> set[str]:
    from tinm_next_action import extract_signals

    return {s["signal_type"] for s in extract_signals(turn_text, role)}


def test_task_done_english_with_t_identifier(tinm_tmp):
    assert "task_done" in _types("T8 is done, tests green")


def test_task_done_french(tinm_tmp):
    assert "task_done" in _types("T12 fini hier soir, on peut passer à la suite")


def test_task_done_lane(tinm_tmp):
    assert "task_done" in _types("Lane C tested and merged ✅")


def test_task_done_pr_merged(tinm_tmp):
    assert "task_done" in _types("PR #82 mergé sur main")


def test_task_done_checkmark_only(tinm_tmp):
    """Bare '✅ T8' should still register — it's our most common ack form."""
    assert "task_done" in _types("✅ T8")


def test_task_done_step_french(tinm_tmp):
    assert "task_done" in _types("étape 3 terminée, on passe à 4")


def test_unblock_english(tinm_tmp):
    assert "unblock" in _types("All tests passing, finally unblocked")


def test_unblock_french(tinm_tmp):
    assert "unblock" in _types("Le bug est débloqué, prêt à merger")


def test_pending_english(tinm_tmp):
    sigs = _types("Waiting for Jordan to merge PR #82")
    assert "pending_ack" in sigs


def test_pending_french(tinm_tmp):
    sigs = _types("En attente de la revue de Jordan")
    assert "pending_ack" in sigs


def test_pending_captures_what(tinm_tmp):
    """The match field for pending_ack should include *what* we wait on."""
    from tinm_next_action import extract_signals

    sigs = extract_signals("Waiting for Jordan to merge PR #82", "user")
    pending = [s for s in sigs if s["signal_type"] == "pending_ack"]
    assert pending
    assert "jordan" in pending[0]["match"].lower()


def test_next_explicit_english(tinm_tmp):
    sigs = _types("Next step: ship v0.2.3 tag")
    assert "next_explicit" in sigs


def test_next_explicit_french(tinm_tmp):
    sigs = _types("Prochaine étape : merger la PR #82")
    assert "next_explicit" in sigs


def test_next_explicit_captures_tail(tinm_tmp):
    from tinm_next_action import extract_signals

    sigs = extract_signals("Next: ship v0.2.3", "user")
    nxt = [s for s in sigs if s["signal_type"] == "next_explicit"]
    assert nxt
    assert "v0.2.3" in nxt[0]["match"]


def test_decision_english(tinm_tmp):
    assert "decision_made" in _types("Decided to go with option B")


def test_decision_french(tinm_tmp):
    assert "decision_made" in _types("On choisit l'option B, on va sur sidecar JSONL")


def test_decision_locked(tinm_tmp):
    assert "decision_made" in _types("Architecture verrouillée — on lock l'API")


def test_failure_english(tinm_tmp):
    assert "failure" in _types("Build failed with a stack trace in tinm_load.py")


def test_failure_french(tinm_tmp):
    assert "failure" in _types("Tout est cassé après le rebase, erreur d'import")


def test_failure_reverted(tinm_tmp):
    assert "failure" in _types("Reverted the change, broken in prod")


def test_empty_input_returns_empty(tinm_tmp):
    from tinm_next_action import extract_signals

    assert extract_signals("", "user") == []
    assert extract_signals("   \n  ", "user") == []


def test_innocuous_text_no_signals(tinm_tmp):
    """Plain conversation should produce no signals (low FP rate)."""
    sigs = _types("Hello, how are you today? The weather is nice.")
    assert sigs == set()


def test_multiple_signals_in_one_turn(tinm_tmp):
    """One turn can carry several signals — they should all be detected."""
    text = (
        "T8 done, all tests passing. Next: ship v0.2.3 tag. "
        "Waiting for Jordan to merge PR #82."
    )
    sigs = _types(text)
    assert {"task_done", "unblock", "next_explicit", "pending_ack"} <= sigs


def test_dedup_within_turn(tinm_tmp):
    """Same (signal_type, match) within one turn → emitted once."""
    from tinm_next_action import extract_signals

    sigs = extract_signals("done T8. done T8. done T8.", "user")
    task_done = [s for s in sigs if s["signal_type"] == "task_done"]
    assert len(task_done) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Persistence — JSONL written correctly
# ─────────────────────────────────────────────────────────────────────────────


def test_record_signals_persists_jsonl(tinm_tmp):
    from tinm_next_action import record_signals, signals_path

    n = record_signals(
        "scratch", 5, "user", "T8 done. Waiting for Jordan.", ts="2026-05-17T10:00:00Z"
    )
    assert n >= 2

    path = signals_path("scratch")
    assert path.exists()
    raws = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(raws) == n
    for entry in raws:
        assert entry["ts"] == "2026-05-17T10:00:00Z"
        assert entry["turn"] == 5
        assert entry["role"] == "user"
        assert entry["signal_type"] in {
            "task_done",
            "unblock",
            "pending_ack",
            "next_explicit",
            "decision_made",
            "failure",
        }
        assert entry["match"]
        assert entry["raw_context"]


def test_record_signals_appends_not_truncates(tinm_tmp):
    from tinm_next_action import record_signals, signals_path

    record_signals("scratch", 1, "user", "T1 done", ts="2026-05-17T10:00:00Z")
    record_signals("scratch", 2, "user", "T2 done", ts="2026-05-17T10:05:00Z")

    lines = [
        line for line in signals_path("scratch").read_text().splitlines() if line.strip()
    ]
    assert len(lines) == 2
    turns = [json.loads(line)["turn"] for line in lines]
    assert turns == [1, 2]


def test_record_signals_no_signals_writes_nothing(tinm_tmp):
    from tinm_next_action import record_signals, signals_path

    n = record_signals("scratch", 1, "user", "hello world how are you")
    assert n == 0
    assert not signals_path("scratch").exists()


def test_record_signals_swallows_io_errors(tinm_tmp, monkeypatch):
    """Best-effort: an unwritable sidecar must NOT raise — return 0."""
    from tinm_next_action import record_signals

    def _boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(pathlib.Path, "mkdir", _boom)
    n = record_signals("scratch", 1, "user", "T1 done")
    assert n == 0


# ─────────────────────────────────────────────────────────────────────────────
# Read-time surface — next_action_block
# ─────────────────────────────────────────────────────────────────────────────


def _write_thread_with_turns(tinm_tmp, thread_id: str, max_turn: int) -> None:
    """Plant a minimal trajectory file so _max_turn_for resolves."""
    from tinm_paths import THREADS_DIR

    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    thread = {
        "pcp_version": "0.1",
        "thread_id": thread_id,
        "metadata": {
            "title": "T",
            "created_at": "2026-05-17T00:00:00Z",
            "last_updated": "2026-05-17T00:00:00Z",
        },
        "anchor": {"update_count": 0, "engaged_so_far": False},
        "trajectory": [
            {
                "turn": i,
                "role": "user",
                "text": f"turn {i}",
                "ts": f"2026-05-17T0{i}:00:00Z",
            }
            for i in range(1, max_turn + 1)
        ],
    }
    (THREADS_DIR / f"{thread_id}.json").write_text(json.dumps(thread))


def test_next_action_block_returns_none_when_no_signals(tinm_tmp):
    from tinm_next_action import next_action_block

    assert next_action_block("never-seen") is None


def test_next_action_block_renders_signals(tinm_tmp):
    from tinm_next_action import next_action_block, record_signals

    _write_thread_with_turns(tinm_tmp, "scratch", max_turn=10)

    record_signals(
        "scratch", 8, "user", "T8 done, all tests passing", ts="2026-05-17T08:00:00Z"
    )
    record_signals(
        "scratch", 9, "user", "Waiting for Jordan to merge PR #82",
        ts="2026-05-17T09:00:00Z",
    )
    record_signals(
        "scratch", 10, "user", "Next: ship v0.2.3 tag", ts="2026-05-17T10:00:00Z"
    )

    block = next_action_block("scratch", window=20, max_signals=5)
    assert block is not None
    assert "Picking up from" in block
    # Should contain at least one of each
    assert "Done" in block or "✅" in block
    assert "Waiting on" in block or "⏳" in block
    assert "Next mentioned" in block or "🎯" in block
    # Turn numbers should be in there
    assert "turn 8" in block or "turn 9" in block or "turn 10" in block


def test_next_action_block_window_excludes_old_signals(tinm_tmp):
    """K-turn window respected — old signals are filtered out."""
    from tinm_next_action import next_action_block, record_signals

    _write_thread_with_turns(tinm_tmp, "scratch", max_turn=100)

    # Plant a signal at turn 5 (old) and at turn 90 (recent).
    record_signals("scratch", 5, "user", "T1 done", ts="2026-05-17T05:00:00Z")

    block = next_action_block("scratch", window=20)
    # cur=100, floor=81 → turn 5 is filtered → block should be None
    assert block is None

    # Add a fresh one at turn 95 → should now surface.
    record_signals("scratch", 95, "user", "T2 done", ts="2026-05-17T09:00:00Z")
    block = next_action_block("scratch", window=20)
    assert block is not None
    assert "turn 95" in block
    # Old signal must NOT be in the block
    assert "turn 5" not in block


def test_next_action_block_respects_max_signals(tinm_tmp):
    from tinm_next_action import next_action_block, record_signals

    _write_thread_with_turns(tinm_tmp, "scratch", max_turn=10)

    # Plant 4 distinct signal types across turns 7-10.
    record_signals("scratch", 7, "user", "T1 done", ts="2026-05-17T07:00:00Z")
    record_signals("scratch", 8, "user", "T2 done", ts="2026-05-17T08:00:00Z")
    record_signals("scratch", 9, "user", "T3 done", ts="2026-05-17T09:00:00Z")
    record_signals("scratch", 10, "user", "T4 done", ts="2026-05-17T10:00:00Z")

    block = next_action_block("scratch", window=20, max_signals=2)
    assert block is not None
    # Header + 2 bullets = 3 lines
    bullets = [ln for ln in block.splitlines() if ln.startswith("- ")]
    assert len(bullets) == 2


def test_next_action_block_skips_malformed_jsonl_lines(tinm_tmp):
    """A corrupt JSONL line (partial peer-host sync) must not crash the reader."""
    from tinm_next_action import next_action_block, record_signals, signals_path

    _write_thread_with_turns(tinm_tmp, "scratch", max_turn=10)
    record_signals("scratch", 9, "user", "T8 done", ts="2026-05-17T09:00:00Z")

    path = signals_path("scratch")
    # Append a broken line in the middle.
    with path.open("a", encoding="utf-8") as f:
        f.write("{this is not json\n")
    record_signals("scratch", 10, "user", "T9 done", ts="2026-05-17T10:00:00Z")

    block = next_action_block("scratch")
    assert block is not None
    # Both valid signals should surface; the malformed line is skipped.
    assert "turn 9" in block
    assert "turn 10" in block


def test_next_action_block_empty_trajectory_no_signals_returns_none(tinm_tmp):
    """Empty thread + empty signals → None."""
    from tinm_next_action import next_action_block

    _write_thread_with_turns(tinm_tmp, "scratch", max_turn=0)
    assert next_action_block("scratch") is None


def test_next_action_block_handles_missing_thread_file(tinm_tmp):
    """Signals exist but trajectory JSON is gone — fall back to signals' max turn."""
    from tinm_next_action import next_action_block, record_signals

    record_signals("orphan", 50, "user", "T1 done", ts="2026-05-17T10:00:00Z")
    block = next_action_block("orphan", window=20)
    assert block is not None
    assert "turn 50" in block


# ─────────────────────────────────────────────────────────────────────────────
# Surface ordering
# ─────────────────────────────────────────────────────────────────────────────


def test_surface_orders_by_signal_priority(tinm_tmp):
    """Done should appear above failure even when failure is fresher."""
    from tinm_next_action import next_action_block, record_signals

    _write_thread_with_turns(tinm_tmp, "scratch", max_turn=10)

    record_signals("scratch", 9, "user", "T8 done", ts="2026-05-17T09:00:00Z")
    record_signals("scratch", 10, "user", "Build failed", ts="2026-05-17T10:00:00Z")

    block = next_action_block("scratch")
    assert block is not None
    bullets = [ln for ln in block.splitlines() if ln.startswith("- ")]
    # task_done (priority 0) should come before failure (priority 5)
    assert any("Done" in bullets[0] or "✅" in bullets[0] for _ in [0])
    assert any("Failure" in bullets[-1] or "❌" in bullets[-1] for _ in [0])
