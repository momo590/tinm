"""Tests for the _anchor_worker.py background script.

The worker is the heavy lift (sentence-transformers cold-import + encode)
moved off the hot path in v0.3.0. These tests:

  1. Run the worker as a standalone callable, mocking sentence_transformers
     and numpy so the test stays fast (< 100ms each).
  2. Verify the pending-hint file structure is what the hot path expects.
  3. Verify that worker errors don't surface — fire-and-forget contract.

The integration test in test_tinm_update_async.py exercises the real
subprocess pathway end-to-end.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

# Add the skill directory to sys.path so internal imports resolve.
SKILL_DIR = Path(__file__).parent.parent / "skill"
sys.path.insert(0, str(SKILL_DIR))


# ---------------------------------------------------------------------------
# Sentence-transformers mock — installed into sys.modules so any worker
# call that does `from sentence_transformers import SentenceTransformer`
# gets a deterministic fake without touching the real model.
# ---------------------------------------------------------------------------
class _FakeModel:
    def encode(self, text, convert_to_numpy=True, show_progress_bar=False):
        import numpy as np
        # Deterministic hash-based pseudo-embedding, dim 384.
        seed = sum(ord(c) for c in text) % 999983
        rng = np.random.default_rng(seed)
        return rng.standard_normal(384).astype("float32")


@pytest.fixture
def fake_sentence_transformers(monkeypatch):
    """Install a fake sentence_transformers module."""
    fake_mod = types.ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = lambda name: _FakeModel()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)
    # Reset cached model so each test gets a fresh load.
    import _anchor_worker
    _anchor_worker._MODEL = None
    yield


@pytest.fixture
def pcp_store(tmp_path):
    """Build a minimal PCP store with one initialised thread."""
    pcp = tmp_path / "pcp"
    threads = pcp / "threads"
    threads.mkdir(parents=True)
    thread_id = "worker-test"
    thread = {
        "pcp_version": "0.1",
        "thread_id": thread_id,
        "metadata": {
            "title": "Worker Test",
            "created_at": "2026-05-17T00:00:00Z",
            "last_updated": "2026-05-17T00:00:00Z",
            "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
            "embedding_dim": 384,
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
        "trajectory": [
            {"turn": 1, "role": "user", "text": "first user query", "ts": "2026-05-17T00:00:01Z"},
            {"turn": 2, "role": "user", "text": "second one", "ts": "2026-05-17T00:00:02Z"},
            {"turn": 3, "role": "user", "text": "what is it that we discussed earlier",
             "ts": "2026-05-17T00:00:03Z"},
        ],
    }
    (threads / f"{thread_id}.json").write_text(json.dumps(thread, indent=2))
    return {"pcp_dir": pcp, "thread_id": thread_id, "threads_dir": threads}


# ---------------------------------------------------------------------------
# Test 1: worker runs end-to-end with a fake model
# ---------------------------------------------------------------------------
class TestWorkerStandalone:
    def test_worker_updates_anchor_vector(self, fake_sentence_transformers, pcp_store):
        """After the worker runs, the thread's anchor.vector is populated."""
        import _anchor_worker
        rc = _anchor_worker.run_worker(
            pcp_store["thread_id"],
            pcp_store["pcp_dir"],
            "session-x",
            target_turn=3,
        )
        assert rc == 0
        thread_path = pcp_store["threads_dir"] / f"{pcp_store['thread_id']}.json"
        thread = json.loads(thread_path.read_text())
        assert thread["anchor"]["vector"] is not None
        assert len(thread["anchor"]["vector"]) == 384
        assert thread["anchor"]["update_count"] == 1
        # First update: alpha_used = fixed_alpha (anchor_vec was None).
        assert thread["anchor"]["alpha_used"] == pytest.approx(0.85)
        assert "last_async_update_ts" in thread["anchor"]

    def test_worker_ema_second_call(self, fake_sentence_transformers, pcp_store):
        """Second worker call applies EMA against the prior anchor."""
        import _anchor_worker
        # Run for turn 2 first to seed the vector, then turn 3.
        _anchor_worker.run_worker(
            pcp_store["thread_id"], pcp_store["pcp_dir"], "s", target_turn=2,
        )
        thread_path = pcp_store["threads_dir"] / f"{pcp_store['thread_id']}.json"
        first = json.loads(thread_path.read_text())
        assert first["anchor"]["update_count"] == 1
        first_vec = first["anchor"]["vector"][:5]

        _anchor_worker.run_worker(
            pcp_store["thread_id"], pcp_store["pcp_dir"], "s", target_turn=3,
        )
        second = json.loads(thread_path.read_text())
        assert second["anchor"]["update_count"] == 2
        # Adaptive alpha kicks in; vector should differ.
        second_vec = second["anchor"]["vector"][:5]
        assert first_vec != second_vec

    def test_worker_writes_hint_for_anaphoric_turn(
        self, fake_sentence_transformers, pcp_store
    ):
        """Anaphoric query → pending-hint file appears, schema matches."""
        import _anchor_worker
        # Engage L1 by setting engaged_so_far first (the worker writes hint
        # only if anchor.engaged_so_far is True; the hot path normally
        # sets that before invoking the worker).
        thread_path = pcp_store["threads_dir"] / f"{pcp_store['thread_id']}.json"
        thread = json.loads(thread_path.read_text())
        thread["anchor"]["engaged_so_far"] = True
        thread["anchor"]["top_terms"] = ["discussed", "earlier"]
        thread_path.write_text(json.dumps(thread, indent=2))

        _anchor_worker.run_worker(
            pcp_store["thread_id"], pcp_store["pcp_dir"], "session-y",
            target_turn=3,
        )

        hint_file = pcp_store["threads_dir"] / f".pending_hint-{pcp_store['thread_id']}.json"
        assert hint_file.exists(), "anaphoric turn should drop a pending hint file"
        payload = json.loads(hint_file.read_text())
        assert payload["thread_id"] == pcp_store["thread_id"]
        assert payload["session_id"] == "session-y"
        assert payload["turn_marker"] == 3
        assert "hint_md" in payload
        assert payload["hint_md"]  # non-empty
        assert "TINM" in payload["hint_md"]
        assert "ts" in payload

    def test_worker_skips_hint_when_no_anaphora(
        self, fake_sentence_transformers, pcp_store
    ):
        """Non-anaphoric query → no pending-hint file written."""
        import _anchor_worker
        # Replace target turn with a non-anaphoric query.
        thread_path = pcp_store["threads_dir"] / f"{pcp_store['thread_id']}.json"
        thread = json.loads(thread_path.read_text())
        thread["trajectory"][-1]["text"] = "build a new database schema"
        thread["anchor"]["engaged_so_far"] = True
        thread_path.write_text(json.dumps(thread, indent=2))

        _anchor_worker.run_worker(
            pcp_store["thread_id"], pcp_store["pcp_dir"], "s", target_turn=3,
        )
        hint_file = pcp_store["threads_dir"] / f".pending_hint-{pcp_store['thread_id']}.json"
        assert not hint_file.exists()

    def test_worker_swallows_missing_thread(self, tmp_path):
        """Missing thread file → worker exits 0, no crash."""
        import _anchor_worker
        rc = _anchor_worker.run_worker(
            "ghost-thread", tmp_path / "pcp", "s", target_turn=1,
        )
        assert rc == 0

    def test_worker_swallows_model_load_failure(self, monkeypatch, pcp_store):
        """If sentence-transformers blows up, worker exits 0 (fire-and-forget)."""
        # Force a broken import.
        import _anchor_worker
        _anchor_worker._MODEL = None

        bad_mod = types.ModuleType("sentence_transformers")
        def _bad(name):
            raise RuntimeError("simulated model load failure")
        bad_mod.SentenceTransformer = _bad
        monkeypatch.setitem(sys.modules, "sentence_transformers", bad_mod)

        rc = _anchor_worker.run_worker(
            pcp_store["thread_id"], pcp_store["pcp_dir"], "s", target_turn=2,
        )
        # MUST be 0 — never blocks the user.
        assert rc == 0


# ---------------------------------------------------------------------------
# Test 2: pending_hint_path schema
# ---------------------------------------------------------------------------
class TestPendingHintPath:
    def test_path_under_threads_dir(self, tmp_path):
        import _anchor_worker
        p = _anchor_worker.pending_hint_path(tmp_path, "my-thread")
        assert p == tmp_path / "threads" / ".pending_hint-my-thread.json"

    def test_path_is_per_thread(self, tmp_path):
        """Each thread has its own hint file."""
        import _anchor_worker
        p1 = _anchor_worker.pending_hint_path(tmp_path, "thread-a")
        p2 = _anchor_worker.pending_hint_path(tmp_path, "thread-b")
        assert p1 != p2
