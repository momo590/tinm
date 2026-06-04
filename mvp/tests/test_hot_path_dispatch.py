"""Tests for the background dispatcher ordering contract.

Regression guard for the 2026-06-04 diagnosis: capture went silent from
2026-05-21 because the dispatcher ran the heavy anchor (sentence-transformers
load, ~10s / ~600MB) BEFORE the cheap score_and_flush. On a memory-tight host
the detached process was killed mid-anchor, so capture (which ran afterwards)
never executed for turns >= 2.

Contract now: score_and_flush runs BEFORE the anchor, so the assistant turn
is flushed even if the anchor later dies.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SKILL_DIR = Path(__file__).parent.parent / "skill"
sys.path.insert(0, str(SKILL_DIR))

import _hot_path_dispatch as disp  # noqa: E402


class TestDispatchOrdering:
    def test_score_and_flush_runs_before_anchor(self):
        """The cheap, high-value capture must execute before the heavy anchor."""
        calls: list[str] = []
        with patch.object(disp, "_run_score_and_flush",
                          side_effect=lambda *a, **k: calls.append("score")), \
             patch.object(disp, "_run_anchor",
                          side_effect=lambda *a, **k: calls.append("anchor")), \
             patch.object(disp, "_run_push",
                          side_effect=lambda *a, **k: calls.append("push")):
            disp.main([
                "--anchor", "root-x", "12",
                "--session-id", "sess-1",
                "--score-and-flush", "root-x", "sess-1", "looks good thanks",
                "--next-turn", "12",
            ])
        assert calls.index("score") < calls.index("anchor"), (
            f"capture must precede anchor; got order {calls}"
        )

    def test_capture_still_runs_when_anchor_would_die(self):
        """If the anchor crashes the whole process, capture already happened.

        We can't kill the process in a unit test, but we prove capture is
        sequenced first: make the anchor raise and assert score still ran.
        (main() calls _run_anchor after _run_score_and_flush, so the raise
        cannot retroactively skip capture.)
        """
        ran = {"score": False}

        def _boom(*a, **k):
            raise RuntimeError("anchor OOM / crash")

        with patch.object(disp, "_run_score_and_flush",
                          side_effect=lambda *a, **k: ran.__setitem__("score", True)), \
             patch.object(disp, "_run_anchor", side_effect=_boom), \
             patch.object(disp, "_run_push", side_effect=lambda *a, **k: None):
            with pytest.raises(RuntimeError):
                disp.main([
                    "--anchor", "root-x", "12",
                    "--score-and-flush", "root-x", "sess-1", "great",
                ])
        assert ran["score"] is True

    def test_only_requested_actions_run(self):
        """Flags are optional — absent flags must not trigger their action."""
        calls: list[str] = []
        with patch.object(disp, "_run_score_and_flush",
                          side_effect=lambda *a, **k: calls.append("score")), \
             patch.object(disp, "_run_anchor",
                          side_effect=lambda *a, **k: calls.append("anchor")), \
             patch.object(disp, "_run_push",
                          side_effect=lambda *a, **k: calls.append("push")):
            disp.main([
                "--score-and-flush", "root-x", "sess-1", "hello",
            ])
        assert calls == ["score"]


# ---------------------------------------------------------------------------
# B2 (2026-06-04) — the in-process anchor in the dispatch path must respect
# single-flight, so 600MB model loads don't stack and OOM a tight host.
# ---------------------------------------------------------------------------

import os  # noqa: E402

import _anchor_worker as aw  # noqa: E402


@pytest.fixture
def tinm_home_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("TINM_HOME", str(tmp_path))
    return tmp_path


class TestAnchorSingleFlight:
    def test_alive_false_when_no_pidfile(self, tinm_home_tmp):
        assert aw.anchor_worker_alive("root-x") is False

    def test_alive_false_for_own_pid(self, tinm_home_tmp):
        aw._pidfile_path("root-x").write_text(str(os.getpid()))
        assert aw.anchor_worker_alive("root-x") is False

    def test_alive_false_for_dead_pid(self, tinm_home_tmp):
        # PID 2^31-1 is effectively never a live process.
        aw._pidfile_path("root-x").write_text("2147483646")
        assert aw.anchor_worker_alive("root-x") is False

    def test_alive_false_for_corrupt_pidfile(self, tinm_home_tmp):
        aw._pidfile_path("root-x").write_text("not-a-pid")
        assert aw.anchor_worker_alive("root-x") is False

    def test_claim_succeeds_when_free_and_writes_pid(self, tinm_home_tmp):
        assert aw.claim_pidfile("root-x") is True
        assert aw._pidfile_path("root-x").read_text().strip() == str(os.getpid())

    def test_claim_fails_when_live_worker_owns_slot(self, tinm_home_tmp, monkeypatch):
        monkeypatch.setattr(aw, "anchor_worker_alive", lambda tid: True)
        assert aw.claim_pidfile("root-x") is False

    def test_clear_removes_pidfile(self, tinm_home_tmp):
        aw.claim_pidfile("root-x")
        assert aw._pidfile_path("root-x").exists()
        aw._clear_pidfile("root-x")
        assert not aw._pidfile_path("root-x").exists()


class TestRunAnchorGuard:
    def test_run_anchor_skips_when_not_claimed(self, monkeypatch):
        called = {"worker": False}
        monkeypatch.setattr(aw, "claim_pidfile", lambda tid: False)
        monkeypatch.setattr(aw, "run_worker",
                            lambda *a, **k: called.__setitem__("worker", True))
        disp._run_anchor("root-x", 5, "sess-1")
        assert called["worker"] is False

    def test_run_anchor_runs_and_clears_when_claimed(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(aw, "claim_pidfile", lambda tid: True)
        monkeypatch.setattr(aw, "run_worker",
                            lambda *a, **k: events.append("worker"))
        monkeypatch.setattr(aw, "_clear_pidfile",
                            lambda tid: events.append("clear"))
        disp._run_anchor("root-x", 5, "sess-1")
        assert events == ["worker", "clear"]

    def test_run_anchor_clears_pidfile_even_if_worker_raises(self, monkeypatch):
        events: list[str] = []
        monkeypatch.setattr(aw, "claim_pidfile", lambda tid: True)

        def _boom(*a, **k):
            raise RuntimeError("model OOM")

        monkeypatch.setattr(aw, "run_worker", _boom)
        monkeypatch.setattr(aw, "_clear_pidfile",
                            lambda tid: events.append("clear"))
        disp._run_anchor("root-x", 5, "sess-1")  # must not raise
        assert events == ["clear"]
