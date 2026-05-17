"""Tests for the v0.3.0 async-dispatch hot path in tinm_update.py.

Critical contract: the hot path must NEVER import sentence_transformers
at module top-level (the import alone takes ~6s cold). Heavy lifting
goes to _anchor_worker.py via subprocess.Popen(start_new_session=True).

Tests:
  1. Inspection: `import tinm_update` does not pull in sentence_transformers.
  2. Hot path returns in < 100ms (mocking subprocess.Popen so we measure
     just the JSON/append/lock work).
  3. read_pending_hint() ignores hints whose thread_id doesn't match.
  4. End-to-end: turn N hot path triggers Popen; pre-existing hint from
     "turn N-1 worker" is consumed and emitted; new dispatch happens
     for the turn that was just appended.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Insert skill on sys.path BEFORE importing tinm_update.
SKILL_DIR = Path(__file__).parent.parent / "skill"
sys.path.insert(0, str(SKILL_DIR))


# ---------------------------------------------------------------------------
# Test 1: import-time inspection
# ---------------------------------------------------------------------------
class TestHotPathDoesNotImportSentenceTransformers:
    def test_module_does_not_pull_sentence_transformers(self):
        """`import tinm_update` MUST NOT trigger sentence_transformers import.

        The cold import of sentence_transformers alone takes ~6s — if it
        ends up at the top level, every hook invocation pays that cost.
        """
        # Drop any cached imports of either module so we start clean.
        for mod in list(sys.modules):
            if mod == "sentence_transformers" or mod.startswith("sentence_transformers."):
                del sys.modules[mod]
            if mod == "tinm_update":
                del sys.modules[mod]

        importlib.import_module("tinm_update")

        # After importing tinm_update, sentence_transformers must NOT be in sys.modules.
        loaded = [m for m in sys.modules if m.startswith("sentence_transformers")]
        assert not loaded, (
            f"tinm_update import pulled in sentence_transformers ({loaded}). "
            "Move embedding to _anchor_worker.py."
        )

    def test_module_source_has_no_top_level_st_import(self):
        """Static check: no `import sentence_transformers` at module top-level."""
        src = (SKILL_DIR / "tinm_update.py").read_text()
        # Strip everything inside function/method bodies (cheap regex check:
        # we look for any line that begins with whitespace AND contains the
        # forbidden import — those are allowed because they're nested).
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped == line and (
                stripped.startswith("import sentence_transformers")
                or stripped.startswith("from sentence_transformers")
            ):
                pytest.fail(
                    f"Top-level forbidden import in tinm_update.py: {line!r}"
                )


# ---------------------------------------------------------------------------
# PCP store fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def fresh_thread(tmp_path, monkeypatch):
    """Initialize an empty thread in a temp PCP store + patch TINM_PCP_DIR."""
    pcp = tmp_path / "pcp"
    threads = pcp / "threads"
    artifacts = pcp / "artifacts"
    threads.mkdir(parents=True)
    artifacts.mkdir(parents=True)
    thread_id = "async-hot-path"
    thread = {
        "pcp_version": "0.1",
        "thread_id": thread_id,
        "metadata": {
            "title": "Async test",
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
        "trajectory": [],
    }
    (threads / f"{thread_id}.json").write_text(json.dumps(thread, indent=2))

    # Patch the module-level constants so update_thread's default
    # path resolution lands inside the temp dir.
    import tinm_update
    monkeypatch.setattr(tinm_update, "TINM_PCP_DIR", pcp)
    monkeypatch.setattr(tinm_update, "THREADS_DIR", threads)

    return {"pcp_dir": pcp, "thread_id": thread_id, "threads_dir": threads}


# ---------------------------------------------------------------------------
# Test 2: hot path performance
# ---------------------------------------------------------------------------
class TestHotPathLatency:
    def test_hot_path_returns_under_100ms(self, fresh_thread, monkeypatch):
        """Hot path completes in well under 100ms when Popen is stubbed."""
        import tinm_update

        # Stub Popen so we measure ONLY the JSON/append/lock work.
        calls = []
        class _StubPopen:
            def __init__(self, *args, **kwargs):
                calls.append((args, kwargs))
        monkeypatch.setattr(tinm_update.subprocess, "Popen", _StubPopen)

        # Warm filesystem cache.
        tinm_update.update_thread(
            fresh_thread["thread_id"],
            query="warmup",
            pcp_dir=fresh_thread["pcp_dir"],
        )

        # Measure the SECOND call (turn 2) — gives us cold-cache-free reads
        # but the thread is now non-empty (realistic).
        t0 = time.perf_counter()
        tinm_update.update_thread(
            fresh_thread["thread_id"],
            query="real query measurement",
            pcp_dir=fresh_thread["pcp_dir"],
        )
        dt_ms = (time.perf_counter() - t0) * 1000.0
        # Loose gate (we just need to prove the heavy work is gone). The
        # real bench measures shell overhead too.
        assert dt_ms < 100.0, f"hot path took {dt_ms:.1f}ms; should be < 100ms"
        # And the worker was dispatched.
        assert len(calls) == 2, f"expected 2 Popen calls, got {len(calls)}"

    def test_hot_path_dispatches_worker_via_popen(self, fresh_thread, monkeypatch):
        """`update_thread` calls subprocess.Popen with start_new_session=True."""
        import tinm_update

        captured = {}
        class _StubPopen:
            def __init__(self, args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
        monkeypatch.setattr(tinm_update.subprocess, "Popen", _StubPopen)

        tinm_update.update_thread(
            fresh_thread["thread_id"],
            query="some query",
            session_id="sess-1",
            pcp_dir=fresh_thread["pcp_dir"],
        )
        assert "args" in captured
        cmd = captured["args"]
        # Worker script in argv.
        assert any("_anchor_worker.py" in str(a) for a in cmd)
        assert "--thread-id" in cmd and fresh_thread["thread_id"] in cmd
        assert "--target-turn" in cmd
        assert captured["kwargs"].get("start_new_session") is True
        # stdout/stderr DEVNULL to avoid blocking on a slow shell pipe.
        import subprocess
        assert captured["kwargs"].get("stdout") == subprocess.DEVNULL
        assert captured["kwargs"].get("stderr") == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# Test 3: pending-hint consumption logic
# ---------------------------------------------------------------------------
class TestReadPendingHint:
    def test_returns_empty_when_no_file(self, tmp_path):
        from tinm_update import read_pending_hint
        out = read_pending_hint("thread-x", pcp_dir=tmp_path)
        assert out == ""

    def test_returns_hint_when_thread_matches(self, tmp_path):
        from tinm_update import read_pending_hint, _pending_hint_path
        threads = tmp_path / "threads"
        threads.mkdir()
        p = _pending_hint_path("thread-x", pcp_dir=tmp_path)
        p.write_text(json.dumps({
            "thread_id": "thread-x",
            "session_id": "s1",
            "turn_marker": 5,
            "hint_md": "Hi Claude, here is the hint.",
            "ts": "2026-05-17T00:00:00Z",
        }))
        out = read_pending_hint("thread-x", pcp_dir=tmp_path)
        assert out == "Hi Claude, here is the hint."
        # consume=True default → file gone.
        assert not p.exists()

    def test_ignores_hint_from_different_thread(self, tmp_path):
        """Defensive: file under thread-A's name with thread-B's payload is ignored."""
        from tinm_update import read_pending_hint, _pending_hint_path
        threads = tmp_path / "threads"
        threads.mkdir()
        p = _pending_hint_path("thread-x", pcp_dir=tmp_path)
        p.write_text(json.dumps({
            "thread_id": "different-thread",
            "session_id": "s",
            "turn_marker": 1,
            "hint_md": "should not surface",
            "ts": "2026-05-17T00:00:00Z",
        }))
        out = read_pending_hint("thread-x", pcp_dir=tmp_path)
        assert out == ""
        # File NOT consumed — another reader might want it.
        assert p.exists()

    def test_corrupt_file_returns_empty_and_cleans_up(self, tmp_path):
        from tinm_update import read_pending_hint, _pending_hint_path
        threads = tmp_path / "threads"
        threads.mkdir()
        p = _pending_hint_path("thread-x", pcp_dir=tmp_path)
        p.write_text("not valid json {{")
        out = read_pending_hint("thread-x", pcp_dir=tmp_path, consume=True)
        assert out == ""
        # Corrupt file is removed so we don't keep tripping over it.
        assert not p.exists()

    def test_consume_false_preserves_file(self, tmp_path):
        from tinm_update import read_pending_hint, _pending_hint_path
        threads = tmp_path / "threads"
        threads.mkdir()
        p = _pending_hint_path("thread-x", pcp_dir=tmp_path)
        p.write_text(json.dumps({
            "thread_id": "thread-x",
            "session_id": "s",
            "turn_marker": 1,
            "hint_md": "keepme",
            "ts": "2026-05-17T00:00:00Z",
        }))
        out = read_pending_hint("thread-x", pcp_dir=tmp_path, consume=False)
        assert out == "keepme"
        assert p.exists()


# ---------------------------------------------------------------------------
# Test 4: end-to-end with mocked subprocess + pre-seeded pending hint
# ---------------------------------------------------------------------------
class TestEndToEndAsyncFlow:
    def test_prior_hint_consumed_and_new_dispatch(self, fresh_thread, monkeypatch):
        """Hot path on turn N: emits prior hint, dispatches new worker."""
        import tinm_update

        # Seed a "previous turn's worker output".
        hint_path = tinm_update._pending_hint_path(
            fresh_thread["thread_id"], pcp_dir=fresh_thread["pcp_dir"],
        )
        hint_path.write_text(json.dumps({
            "thread_id": fresh_thread["thread_id"],
            "session_id": "s",
            "turn_marker": 0,
            "hint_md": "PRIOR_HINT_PAYLOAD",
            "ts": "2026-05-17T00:00:00Z",
        }))

        # Stub Popen to count calls.
        popen_calls = []
        class _StubPopen:
            def __init__(self, *args, **kwargs):
                popen_calls.append(args)
        monkeypatch.setattr(tinm_update.subprocess, "Popen", _StubPopen)

        result = tinm_update.update_thread(
            fresh_thread["thread_id"],
            query="please continue",
            emit_hint=True,
            pcp_dir=fresh_thread["pcp_dir"],
        )

        assert result["hint_text"] == "PRIOR_HINT_PAYLOAD"
        # Worker dispatched for the new turn.
        assert len(popen_calls) == 1
        # Old hint file consumed.
        assert not hint_path.exists()
        # Trajectory appended.
        thread = json.loads(
            (fresh_thread["threads_dir"] / f"{fresh_thread['thread_id']}.json").read_text()
        )
        assert len(thread["trajectory"]) == 1
        assert thread["trajectory"][0]["text"] == "please continue"

    def test_turn_count_grows_monotonically(self, fresh_thread, monkeypatch):
        """5 consecutive hot-path calls = 5 trajectory entries, anchor unchanged."""
        import tinm_update
        monkeypatch.setattr(tinm_update.subprocess, "Popen",
                            lambda *a, **kw: None)
        for i in range(5):
            tinm_update.update_thread(
                fresh_thread["thread_id"],
                query=f"query {i}",
                pcp_dir=fresh_thread["pcp_dir"],
            )
        thread = json.loads(
            (fresh_thread["threads_dir"] / f"{fresh_thread['thread_id']}.json").read_text()
        )
        assert len(thread["trajectory"]) == 5
        # Anchor vector is None — worker never ran (we mocked Popen).
        assert thread["anchor"]["vector"] is None
        # But engaged_so_far flipped to True by turn 3.
        assert thread["anchor"]["engaged_so_far"] is True
        assert thread["anchor"]["top_terms"]  # non-empty

    def test_worker_owned_fields_preserved_under_concurrent_write(
        self, fresh_thread, monkeypatch
    ):
        """If the worker writes the anchor vector between our read and lock,
        the hot path's write must not clobber it."""
        import tinm_update
        # First, simulate one hot-path call that does NOT dispatch a worker.
        monkeypatch.setattr(tinm_update.subprocess, "Popen",
                            lambda *a, **kw: None)
        tinm_update.update_thread(
            fresh_thread["thread_id"],
            query="first turn",
            pcp_dir=fresh_thread["pcp_dir"],
        )
        # Now simulate the worker having written the vector between this
        # call and the next hot path. We do this by manually patching the
        # thread file.
        thread_path = fresh_thread["threads_dir"] / f"{fresh_thread['thread_id']}.json"
        thread = json.loads(thread_path.read_text())
        thread["anchor"]["vector"] = [0.5] * 384
        thread["anchor"]["alpha_used"] = 0.42
        thread["anchor"]["update_count"] = 1
        thread["anchor"]["last_async_update_ts"] = "2026-05-17T00:00:00Z"
        thread_path.write_text(json.dumps(thread, indent=2))

        # Next hot-path call must NOT zero out the vector/alpha_used.
        tinm_update.update_thread(
            fresh_thread["thread_id"],
            query="second turn",
            pcp_dir=fresh_thread["pcp_dir"],
        )
        after = json.loads(thread_path.read_text())
        assert after["anchor"]["vector"] == [0.5] * 384
        assert after["anchor"]["alpha_used"] == 0.42
        assert after["anchor"]["update_count"] == 1
        # And we appended the new turn.
        assert len(after["trajectory"]) == 2
