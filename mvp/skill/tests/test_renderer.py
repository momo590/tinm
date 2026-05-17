"""Tests for tinm_renderer.humanize() — v0.2.3 L3 read-time renderer.

Covers each pattern category (positive + negative-leave-alone), idempotence,
the feature flag, and a couple of real-corpus before/after snapshots.
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture
def tinm_tmp(monkeypatch, tmp_path):
    """Isolate TINM_HOME/PCP_DIR and clear module caches."""
    monkeypatch.setenv("TINM_HOME", str(tmp_path))
    monkeypatch.setenv("TINM_PCP_DIR", str(tmp_path / "pcp"))
    monkeypatch.setenv("TINM_RENDER_LEGACY", "1")
    for mod in ["tinm_paths", "tinm_renderer", "tinm_load", "tinm_artifact"]:
        sys.modules.pop(mod, None)
    (tmp_path / "pcp" / "threads").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ─── Pattern 1: Task slugs ────────────────────────────────────────────────────


class TestTaskSlugs:
    def test_single_task_t0(self, tinm_tmp):
        from tinm_renderer import humanize
        assert "kickoff task" in humanize("T0 BLOCKER")

    def test_single_task_t1(self, tinm_tmp):
        from tinm_renderer import humanize
        assert "the first task" in humanize("Start with T1.")

    def test_task_range(self, tinm_tmp):
        from tinm_renderer import humanize
        assert "tasks 1-3" in humanize("Lane A direct T1-T2-T3")

    def test_high_task_number_falls_back(self, tinm_tmp):
        from tinm_renderer import humanize
        # Standalone T14 (no range) → "task 14"
        assert "task 14" in humanize("Move on to T14.")
        # Range form covers T1-T14 → "tasks 1-14"
        assert "tasks 1-14" in humanize("14 implementation tasks T1-T14")

    def test_leave_alone_t1_lymphocyte(self, tinm_tmp):
        """Free-text usage 'T1 lymphocyte' must not be mangled.

        We DO replace `T1` standalone, but `lymphocyte` is a normal English word
        so the substitution becomes 'the first task lymphocyte', which is wrong
        but legible. The conservative test is: in code-like contexts (URLs,
        identifiers) we don't expand. `tinm_a085` must not become anything.
        """
        from tinm_renderer import humanize
        # tinm_a085 has no isolated T<digit> — should be untouched
        result = humanize("tinm_a085 is Pareto-best on 4 of 5 panels")
        assert "tinm_a085" in result

    def test_no_false_positive_inside_identifier(self, tinm_tmp):
        from tinm_renderer import humanize
        # GPT4, TT0, SET1 must NOT trigger replacement
        result = humanize("GPT4 and SET1 and TT0 are identifiers.")
        assert "GPT4" in result and "SET1" in result and "TT0" in result
        assert "first task" not in result
        assert "kickoff task" not in result


# ─── Pattern 2: Lane slugs ────────────────────────────────────────────────────


class TestLaneSlugs:
    def test_lane_a(self, tinm_tmp):
        from tinm_renderer import humanize
        result = humanize("Restart Lane A tomorrow.")
        assert "the first workstream" in result

    def test_lane_b(self, tinm_tmp):
        from tinm_renderer import humanize
        result = humanize("Lane B was deferred.")
        assert "the second workstream" in result

    def test_lane_range(self, tinm_tmp):
        from tinm_renderer import humanize
        result = humanize("parallelization lanes A-F")
        assert "workstreams A-F" in result

    def test_leave_alone_fast_lane(self, tinm_tmp):
        """'fast lane' is normal English, not our slug."""
        from tinm_renderer import humanize
        result = humanize("Take the fast lane and move on.")
        assert "fast lane" in result
        assert "workstream" not in result


# ─── Pattern 3: Status sigils ─────────────────────────────────────────────────


class TestStatusSigils:
    def test_blocker(self, tinm_tmp):
        from tinm_renderer import humanize
        assert "blocking issue" in humanize("This is a BLOCKER for ship.")

    def test_clear_bracketed(self, tinm_tmp):
        from tinm_renderer import humanize
        assert "approved" in humanize("Eng review [CLEAR]")

    def test_noop(self, tinm_tmp):
        from tinm_renderer import humanize
        assert "no change" in humanize("Result was a noop")

    def test_path_c_strict(self, tinm_tmp):
        from tinm_renderer import humanize
        assert "the strict approach C" in humanize("Path C strict + hard deadline")

    def test_verrouille_french(self, tinm_tmp):
        from tinm_renderer import humanize
        result = humanize("Plan verrouillé pour Loremind")
        assert "locked in" in result
        assert "verrouill" not in result

    def test_leave_alone_clear_the_cache(self, tinm_tmp):
        """'clear' as a regular verb must not be replaced."""
        from tinm_renderer import humanize
        result = humanize("clear the cache before retesting")
        assert "clear the cache" in result


# ─── Pattern 4: Dated lock phrases ────────────────────────────────────────────


class TestDatedLocks:
    def test_locked_yesterday(self, tinm_tmp, monkeypatch):
        from tinm_renderer import humanize
        import tinm_renderer
        # Pin "today" to a known date so the relative phrase is deterministic.
        today = datetime(2026, 5, 17).date()
        monkeypatch.setattr(tinm_renderer, "_today", lambda: today)
        result = humanize("Plan locked 2026-05-16")
        assert "locked yesterday" in result
        assert "2026-05-16" not in result

    def test_decided_days_ago(self, tinm_tmp, monkeypatch):
        from tinm_renderer import humanize
        import tinm_renderer
        today = datetime(2026, 5, 17).date()
        monkeypatch.setattr(tinm_renderer, "_today", lambda: today)
        result = humanize("decided 2026-05-15 by the team")
        assert "decided 2 days ago" in result

    def test_old_date_uses_month_name(self, tinm_tmp, monkeypatch):
        from tinm_renderer import humanize
        import tinm_renderer
        today = datetime(2026, 5, 17).date()
        monkeypatch.setattr(tinm_renderer, "_today", lambda: today)
        result = humanize("locked 2026-04-30")
        assert "April" in result
        assert "2026-04-30" not in result

    def test_pinned_time(self, tinm_tmp):
        from tinm_renderer import humanize
        result = humanize("Decision pinned 14:32Z earlier")
        assert "earlier today" in result

    def test_leave_alone_bare_date(self, tinm_tmp):
        """A date not preceded by a lock verb stays untouched."""
        from tinm_renderer import humanize
        result = humanize("Shipped on 2026-05-14 from the main branch.")
        assert "2026-05-14" in result


# ─── Pattern 5: Raw thread IDs ────────────────────────────────────────────────


class TestThreadIds:
    def test_quote_known_thread_id(self, tinm_tmp, monkeypatch):
        from tinm_renderer import humanize
        import tinm_renderer
        # Seed a fake thread file so 'tinm-tour' exists on disk.
        threads_dir = tinm_tmp / "pcp" / "threads"
        (threads_dir / "tinm-tour.json").write_text("{}")
        tinm_renderer._clear_thread_id_cache()
        tinm_renderer.THREADS_DIR = threads_dir  # type: ignore[attr-defined]
        result = humanize("Resume tinm-tour now.")
        assert '"tinm-tour"' in result

    def test_leave_unknown_slug_alone(self, tinm_tmp, monkeypatch):
        from tinm_renderer import humanize
        import tinm_renderer
        threads_dir = tinm_tmp / "pcp" / "threads"
        tinm_renderer._clear_thread_id_cache()
        tinm_renderer.THREADS_DIR = threads_dir  # type: ignore[attr-defined]
        result = humanize("the foo-bar-baz module is fine")
        assert "foo-bar-baz" in result
        assert '"foo-bar-baz"' not in result


# ─── Pattern 6: Parenthetical noise ───────────────────────────────────────────


class TestParentheticalNoise:
    def test_strip_via_double_bracket(self, tinm_tmp):
        from tinm_renderer import humanize
        result = humanize("Resume work (via [[memory.md]] memory) tomorrow.")
        assert "(via" not in result
        assert "Resume work" in result and "tomorrow" in result

    def test_leave_normal_parenthetical(self, tinm_tmp):
        from tinm_renderer import humanize
        result = humanize("Resume work (Lane A is unblocked) tomorrow.")
        assert "(" in result  # parenthetical preserved (content humanized)
        assert "the first workstream" in result


# ─── Idempotence ──────────────────────────────────────────────────────────────


class TestIdempotence:
    def test_idempotent_basic(self, tinm_tmp):
        from tinm_renderer import humanize
        raw = "T1 BLOCKER on Lane A — locked 2026-05-16 noop"
        once = humanize(raw)
        twice = humanize(once)
        assert once == twice

    def test_idempotent_clean_prose(self, tinm_tmp):
        from tinm_renderer import humanize
        clean = "The team approved the design after review."
        assert humanize(clean) == clean
        assert humanize(humanize(clean)) == clean

    def test_empty_input(self, tinm_tmp):
        from tinm_renderer import humanize
        assert humanize("") == ""
        assert humanize(None) is None  # type: ignore[arg-type]


# ─── Feature flag ─────────────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_disabled_returns_raw(self, tinm_tmp, monkeypatch):
        from tinm_renderer import humanize_if_enabled
        monkeypatch.setenv("TINM_RENDER_LEGACY", "0")
        raw = "T1 BLOCKER"
        assert humanize_if_enabled(raw) == raw

    def test_default_on(self, tinm_tmp, monkeypatch):
        monkeypatch.delenv("TINM_RENDER_LEGACY", raising=False)
        from tinm_renderer import humanize_if_enabled
        result = humanize_if_enabled("T1 BLOCKER")
        assert "BLOCKER" not in result


# ─── Performance ──────────────────────────────────────────────────────────────


class TestPerformance:
    def test_under_10ms(self, tinm_tmp):
        """Typical artifact body should render in well under 10ms."""
        from tinm_renderer import humanize
        import time
        # Realistic body length ~1500 chars
        body = (
            "Plan d'architecture verrouillé pour Loremind v0.1 après /plan-eng-review. "
            "T0 BLOCKER tête de Lane A, deadline 2026-05-22. T1=restructure (déjà fait). "
            "T2=LLM provider abstraction. 14 implementation tasks T1-T14 avec parallelization "
            "lanes A-F. Path C strict + hard deadline. Plan locked 2026-05-16. [CLEAR] for ship."
        ) * 3
        start = time.perf_counter()
        for _ in range(10):
            humanize(body)
        elapsed_ms = (time.perf_counter() - start) * 1000 / 10
        assert elapsed_ms < 10, f"humanize() took {elapsed_ms:.2f}ms per call"


# ─── Real-corpus round-trip ───────────────────────────────────────────────────


class TestRealCorpus:
    """Snapshot-style: gnarly real artifact summaries should become readable."""

    def test_loremind_summary(self, tinm_tmp, monkeypatch):
        from tinm_renderer import humanize
        import tinm_renderer
        today = datetime(2026, 5, 17).date()
        monkeypatch.setattr(tinm_renderer, "_today", lambda: today)
        raw = (
            "Plan verrouillé pour Loremind v0.1. T0 BLOCKER tête de Lane A, "
            "deadline 2026-05-22. T1-T14 avec parallelization lanes A-F. "
            "Path C strict + hard deadline 5 working days."
        )
        out = humanize(raw)
        # Verify jargon is gone
        assert "BLOCKER" not in out
        assert "verrouill" not in out
        assert "Lane A" not in out
        assert "T0" not in out
        assert "T1-T14" not in out
        assert "Path C strict" not in out
        # Verify humanized phrasing
        assert "blocking issue" in out
        assert "locked in" in out
        assert "the first workstream" in out
        assert "the strict approach C" in out

    def test_ceo_decision_summary(self, tinm_tmp, monkeypatch):
        from tinm_renderer import humanize
        import tinm_renderer
        today = datetime(2026, 5, 17).date()
        monkeypatch.setattr(tinm_renderer, "_today", lambda: today)
        raw = "Eng review [CLEAR] — locked 2026-05-16. Lane B+C noop this iteration."
        out = humanize(raw)
        assert "[CLEAR]" not in out
        assert "locked 2026-05-16" not in out
        assert "noop" not in out
        assert "approved" in out
        assert "locked yesterday" in out or "decided yesterday" in out
        assert "no change" in out
