"""Regression tests for `_most_recent_thread` + `_thread_relevance_score`.

Bug 2 root causes (see PR fix/mcp-record-turn-and-thread-selection):

  2a. SessionStart auto-creates from cron / autopilot runs in
      `/root` or `/tmp` were beating the user's actual working
      thread because they had the freshest `last_updated` despite
      having zero substantive turns.

  2b. Two threads sharing the same `last_updated` second fell back
      to `Path.glob()` order, which on ext4 is hash-table order —
      non-deterministic across runs.

The fix introduces a substance gate (n_user_turns ≥ 1 AND
last_updated ≠ created_at) and a deterministic tiebreaker tuple.
This file covers the resulting `_thread_relevance_score` helper and
the resolver that consumes it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "skill"))
sys.path.insert(0, str(_REPO_ROOT / "mcp_server"))


def _seed(
    threads_dir: Path,
    slug: str,
    *,
    last_updated: str,
    created_at: str | None = None,
    n_user_turns: int = 0,
) -> Path:
    """Write a minimal PCP v0 thread JSON used as a fixture."""
    if created_at is None:
        created_at = last_updated  # default: never written after creation
    threads_dir.mkdir(parents=True, exist_ok=True)
    trajectory = [
        {"turn": i + 1, "role": "user", "text": f"q{i + 1}", "ts": last_updated}
        for i in range(n_user_turns)
    ]
    payload = {
        "pcp_version": "0.1",
        "thread_id": slug,
        "metadata": {
            "title": slug,
            "created_at": created_at,
            "last_updated": last_updated,
            "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
            "embedding_dim": 384,
            "project_root": None,
            "client_history": [],
        },
        "anchor": {
            "vector": None,
            "alpha_used": 0.85,
            "update_count": 0,
            "last_updated_turn": 0,
            "engaged_so_far": False,
            "top_terms": [],
        },
        "trajectory": trajectory,
    }
    path = threads_dir / f"{slug}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


@pytest.fixture
def threads_env(tmp_path, monkeypatch):
    """Patch the server's THREADS_DIR pointer at a temp directory."""
    pcp = tmp_path / "pcp"
    threads = pcp / "threads"
    threads.mkdir(parents=True)
    import tinm_paths
    import tinm_server
    monkeypatch.setattr(tinm_paths, "THREADS_DIR", threads, raising=False)
    monkeypatch.setattr(tinm_server, "THREADS_DIR", threads, raising=False)
    return {"pcp": pcp, "threads": threads}


# ---------------------------------------------------------------------------
# _thread_relevance_score pure tests
# ---------------------------------------------------------------------------
class TestThreadRelevanceScore:
    def test_empty_autocreate_scores_zero_substance(self):
        """A thread with no user turns AND last_updated == created_at is non-substantive."""
        import tinm_server
        meta_only = {
            "metadata": {
                "last_updated": "2026-05-21T00:00:00Z",
                "created_at": "2026-05-21T00:00:00Z",
            },
            "trajectory": [],
        }
        score = tinm_server._thread_relevance_score(meta_only, "ghost")
        assert score[0] == 0

    def test_thread_with_user_turn_is_substantive(self):
        import tinm_server
        thread = {
            "metadata": {
                "last_updated": "2026-05-21T00:01:00Z",
                "created_at": "2026-05-21T00:00:00Z",
            },
            "trajectory": [
                {"turn": 1, "role": "user", "text": "hello", "ts": "..."},
            ],
        }
        score = tinm_server._thread_relevance_score(thread, "real")
        assert score[0] == 1

    def test_assistant_only_thread_is_not_substantive(self):
        """A trajectory of assistant-only turns shouldn't count as substantive."""
        import tinm_server
        thread = {
            "metadata": {
                "last_updated": "2026-05-21T00:01:00Z",
                "created_at": "2026-05-21T00:00:00Z",
            },
            "trajectory": [
                {"turn": 1, "role": "assistant", "text": "noise", "ts": "..."},
            ],
        }
        # n_user_turns is 0, so substance gate is closed.
        assert tinm_server._thread_relevance_score(thread, "x")[0] == 0

    def test_tuple_ordering_substantive_beats_recent_empty(self):
        """A substantive thread wins over a more-recent empty autocreate."""
        import tinm_server
        substantive = {
            "metadata": {
                "last_updated": "2026-05-21T00:00:00Z",
                "created_at": "2026-05-20T00:00:00Z",
            },
            "trajectory": [{"turn": 1, "role": "user", "text": "q"}],
        }
        empty = {
            "metadata": {
                "last_updated": "2026-05-21T12:00:00Z",  # more recent
                "created_at": "2026-05-21T12:00:00Z",
            },
            "trajectory": [],
        }
        s_sub = tinm_server._thread_relevance_score(substantive, "real")
        s_empty = tinm_server._thread_relevance_score(empty, "ghost")
        assert s_sub > s_empty

    def test_tiebreaker_prefers_higher_user_turn_count(self):
        """Same last_updated → more user turns wins."""
        import tinm_server
        ts = "2026-05-21T10:00:00Z"
        created = "2026-05-20T00:00:00Z"
        a = {
            "metadata": {"last_updated": ts, "created_at": created},
            "trajectory": [
                {"turn": 1, "role": "user", "text": "q"},
            ],
        }
        b = {
            "metadata": {"last_updated": ts, "created_at": created},
            "trajectory": [
                {"turn": 1, "role": "user", "text": "q"},
                {"turn": 2, "role": "user", "text": "q"},
                {"turn": 3, "role": "user", "text": "q"},
            ],
        }
        assert tinm_server._thread_relevance_score(b, "b") > \
            tinm_server._thread_relevance_score(a, "a")

    def test_tiebreaker_is_deterministic_by_slug(self):
        """Two identical-score threads break the tie on slug (descending)."""
        import tinm_server
        meta = {
            "metadata": {
                "last_updated": "2026-05-21T10:00:00Z",
                "created_at": "2026-05-20T00:00:00Z",
            },
            "trajectory": [{"turn": 1, "role": "user", "text": "q"}],
        }
        s_a = tinm_server._thread_relevance_score(meta, "alpha")
        s_b = tinm_server._thread_relevance_score(meta, "beta")
        # Slug is the last element; higher tuple wins → "beta" > "alpha".
        assert s_b > s_a


# ---------------------------------------------------------------------------
# _most_recent_thread integration tests
# ---------------------------------------------------------------------------
class TestMostRecentThread:
    def test_substantive_beats_more_recent_empty_autocreate(self, threads_env):
        """Bug 2a: empty autocreate from /root cron must not steal selection."""
        import tinm_server
        _seed(
            threads_env["threads"],
            "real-work",
            last_updated="2026-05-21T09:00:00Z",
            created_at="2026-05-20T00:00:00Z",
            n_user_turns=4,
        )
        # An autopilot run in /root spawned an empty thread one second
        # later — that previously beat the real one.
        _seed(
            threads_env["threads"],
            "root-autocreate",
            last_updated="2026-05-21T12:00:00Z",
            created_at="2026-05-21T12:00:00Z",
            n_user_turns=0,
        )
        assert tinm_server._most_recent_thread() == "real-work"

    def test_same_second_more_user_turns_wins(self, threads_env):
        """Bug 2b: identical last_updated → multi-turn thread wins, deterministic."""
        import tinm_server
        ts = "2026-05-21T10:00:00Z"
        _seed(
            threads_env["threads"],
            "multi-turn",
            last_updated=ts,
            created_at="2026-05-20T00:00:00Z",
            n_user_turns=5,
        )
        _seed(
            threads_env["threads"],
            "single-turn",
            last_updated=ts,
            created_at="2026-05-20T00:00:00Z",
            n_user_turns=1,
        )
        assert tinm_server._most_recent_thread() == "multi-turn"

    def test_only_autocreates_falls_back_to_most_recent(self, threads_env):
        """All non-substantive → still pick a thread; never return None when files exist."""
        import tinm_server
        _seed(
            threads_env["threads"],
            "old-autocreate",
            last_updated="2026-05-20T00:00:00Z",
            n_user_turns=0,
        )
        _seed(
            threads_env["threads"],
            "new-autocreate",
            last_updated="2026-05-21T00:00:00Z",
            n_user_turns=0,
        )
        # Both are non-substantive; the most recent wins as last resort.
        assert tinm_server._most_recent_thread() == "new-autocreate"

    def test_empty_threads_dir_returns_none(self, threads_env):
        """No threads on disk → None (not a crash, not a stale pointer)."""
        import tinm_server
        assert tinm_server._most_recent_thread() is None

    def test_missing_threads_dir_returns_none(self, tmp_path, monkeypatch):
        """Threads dir doesn't even exist → None."""
        import tinm_paths
        import tinm_server
        ghost = tmp_path / "does-not-exist" / "threads"
        monkeypatch.setattr(tinm_paths, "THREADS_DIR", ghost, raising=False)
        monkeypatch.setattr(tinm_server, "THREADS_DIR", ghost, raising=False)
        assert tinm_server._most_recent_thread() is None

    def test_corrupt_thread_skipped(self, threads_env):
        """A JSON-corrupt thread does not crash the resolver."""
        import tinm_server
        _seed(
            threads_env["threads"],
            "valid",
            last_updated="2026-05-21T00:00:00Z",
            created_at="2026-05-20T00:00:00Z",
            n_user_turns=2,
        )
        (threads_env["threads"] / "garbage.json").write_text("not json {")
        assert tinm_server._most_recent_thread() == "valid"
