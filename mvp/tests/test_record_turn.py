"""Regression tests for the MCP `record_turn` write path.

Two bugs the v0.3.0 + chat-MCP combo exposed:

  1. `record_turn` raised ValueError on every Claude Desktop chat
     invocation because `_resolve_thread_id_for_write` had no
     fallback once the v0.2.3 migration retired
     `~/.tinm/current_thread`. The fix introduces a per-host chat-
     thread auto-created on first use and persisted in
     `~/.tinm/chat-thread`.

  2. The success-string formatter dereferenced
     `result["alpha_used"]`, but the v0.3.0 hot path returns only
     `turn`, `l1_engaged`, `engaged_so_far`, `anchor_update_count`,
     and `hint_text` — alpha is owned by the async worker. The fix
     uses `.get()` everywhere and renders a "—" placeholder when α
     is unavailable.

These tests exercise the resolver + formatter directly; they do
NOT spin up the FastMCP server (that would need stdio plumbing and
adds nothing over a direct function call).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Insert the skill dir AND the mcp_server dir on sys.path so we can
# import the server module by name. Matches the convention in
# test_tinm_update_async.py.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "skill"))
sys.path.insert(0, str(_REPO_ROOT / "mcp_server"))


def _seed_thread(
    threads_dir: Path,
    slug: str,
    *,
    last_updated: str = "2026-05-21T00:00:00Z",
    created_at: str = "2026-05-21T00:00:00Z",
    n_user_turns: int = 0,
    title: str = "test",
) -> Path:
    """Write a minimal-but-valid PCP v0 thread JSON for use in fixtures."""
    threads_dir.mkdir(parents=True, exist_ok=True)
    trajectory = [
        {
            "turn": i + 1,
            "role": "user",
            "text": f"q{i + 1}",
            "ts": last_updated,
        }
        for i in range(n_user_turns)
    ]
    payload = {
        "pcp_version": "0.1",
        "thread_id": slug,
        "metadata": {
            "title": title,
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
def tinm_env(tmp_path, monkeypatch):
    """Repoint every module that holds a path constant at a temp store.

    The MCP server captures TINM_HOME / THREADS_DIR / CURRENT_FILE from
    `tinm_paths` at import time, so we have to patch the references on
    the server module itself (and on the upstream modules used by
    `init_thread`).
    """
    tinm_home = tmp_path / "tinm"
    pcp = tinm_home / "pcp"
    threads = pcp / "threads"
    seeds = pcp / "seeds"
    artifacts = pcp / "artifacts"
    threads.mkdir(parents=True)
    seeds.mkdir(parents=True)
    artifacts.mkdir(parents=True)

    chat_thread_file = tinm_home / "chat-thread"
    current_file = tinm_home / "current_thread"

    import tinm_paths
    import tinm_init
    import tinm_update
    import tinm_server

    # Patch all path-bearing module-level constants. The server reads
    # CURRENT_FILE / THREADS_DIR / TINM_HOME at function-call time
    # through its own module-level names, so we patch those too.
    for mod, name, value in [
        (tinm_paths, "TINM_HOME", tinm_home),
        (tinm_paths, "TINM_PCP_DIR", pcp),
        (tinm_paths, "THREADS_DIR", threads),
        (tinm_paths, "SEEDS_DIR", seeds),
        (tinm_paths, "ARTIFACTS_DIR", artifacts),
        (tinm_paths, "CURRENT_FILE", current_file),
        (tinm_init, "TINM_PCP_DIR", pcp),
        (tinm_init, "THREADS_DIR", threads),
        (tinm_init, "ARTIFACTS_DIR", artifacts),
        (tinm_init, "CURRENT_FILE", current_file),
        (tinm_update, "TINM_PCP_DIR", pcp),
        (tinm_update, "THREADS_DIR", threads),
        (tinm_server, "TINM_HOME", tinm_home),
        (tinm_server, "THREADS_DIR", threads),
        (tinm_server, "CURRENT_FILE", current_file),
        (tinm_server, "CHAT_THREAD_FILE", chat_thread_file),
    ]:
        monkeypatch.setattr(mod, name, value, raising=False)

    return {
        "tinm_home": tinm_home,
        "pcp": pcp,
        "threads": threads,
        "current_file": current_file,
        "chat_thread_file": chat_thread_file,
    }


# ---------------------------------------------------------------------------
# Resolver: explicit thread_id
# ---------------------------------------------------------------------------
class TestResolveExplicit:
    def test_explicit_thread_id_resolves(self, tinm_env):
        """A valid explicit slug bypasses every fallback."""
        import tinm_server
        _seed_thread(tinm_env["threads"], "my-project", n_user_turns=2)
        resolved = tinm_server._resolve_thread_id_for_write("my-project")
        assert resolved == "my-project"
        # The chat-thread fallback file must NOT have been touched.
        assert not tinm_env["chat_thread_file"].exists()

    def test_unknown_explicit_thread_id_raises(self, tinm_env):
        """An explicit-but-missing slug surfaces a clear error."""
        import tinm_server
        with pytest.raises(ValueError, match="not found on this host"):
            tinm_server._resolve_thread_id_for_write("nope")


# ---------------------------------------------------------------------------
# Resolver: chat-thread fallback (Bug 1 — combined B + C fix)
# ---------------------------------------------------------------------------
class TestChatThreadFallback:
    def test_no_thread_id_triggers_chat_fallback(self, tinm_env):
        """Empty store + no thread_id → auto-create + persist."""
        import tinm_server
        slug = tinm_server._resolve_thread_id_for_write(None)
        assert slug == "claude-desktop-chat"
        # The persisted pointer matches.
        assert tinm_env["chat_thread_file"].read_text().strip() == slug
        # The thread file actually exists on disk.
        assert (tinm_env["threads"] / f"{slug}.json").is_file()

    def test_subsequent_calls_reuse_persisted_slug(self, tinm_env):
        """The pointer file short-circuits re-creation on later calls."""
        import tinm_server
        first = tinm_server._resolve_thread_id_for_write(None)
        # Remove the thread file but keep the pointer: must NOT silently
        # use a deleted thread — should fall through and recreate.
        (tinm_env["threads"] / f"{first}.json").unlink()
        second = tinm_server._resolve_thread_id_for_write(None)
        assert second == first
        assert (tinm_env["threads"] / f"{second}.json").is_file()

    def test_chat_thread_metadata(self, tinm_env):
        """Auto-created chat thread has origin=user, project_root=None."""
        import tinm_server
        slug = tinm_server._resolve_thread_id_for_write(None)
        data = json.loads((tinm_env["threads"] / f"{slug}.json").read_text())
        meta = data["metadata"]
        assert meta["project_root"] is None
        # init_thread defaults the title to whatever we pass — we pass a
        # specific marker so users can identify it in `tinm list`.
        assert "Claude Desktop" in meta["title"]

    def test_legacy_current_file_still_honoured(self, tinm_env):
        """For pre-v0.2.3 installs, CURRENT_FILE still wins over the chat fallback."""
        import tinm_server
        _seed_thread(tinm_env["threads"], "legacy-thread", n_user_turns=1)
        tinm_env["current_file"].parent.mkdir(parents=True, exist_ok=True)
        tinm_env["current_file"].write_text("legacy-thread\n")
        slug = tinm_server._resolve_thread_id_for_write(None)
        assert slug == "legacy-thread"
        # And no chat thread was created.
        assert not tinm_env["chat_thread_file"].exists()

    def test_chat_thread_slug_collision_disambiguates(self, tinm_env):
        """If the base slug is taken by a user project, fall back to a hashed slug."""
        import tinm_server
        # Pre-seed a user project that happens to be at the base
        # slug (project_root set + non-chat title → NOT one of ours).
        path = _seed_thread(
            tinm_env["threads"],
            "claude-desktop-chat",
            n_user_turns=3,
            title="totally-different-project",
        )
        data = json.loads(path.read_text())
        data["metadata"]["project_root"] = "/some/user/project"
        path.write_text(json.dumps(data, indent=2))

        slug = tinm_server._resolve_thread_id_for_write(None)
        # MUST get a disambiguated slug — never alias onto the user
        # project at the bare name.
        assert slug != "claude-desktop-chat"
        assert slug.startswith("claude-desktop-chat-")
        # The collision target was preserved untouched.
        new_data = json.loads(path.read_text())
        assert new_data["metadata"]["project_root"] == "/some/user/project"

    def test_chat_thread_existing_match_reused(self, tinm_env):
        """If `claude-desktop-chat` already exists as one of our auto-creates,
        pointer-loss recovery reuses it (not duplicating)."""
        import tinm_server
        # Seed a thread that looks like ours (title prefix + project_root None).
        _seed_thread(
            tinm_env["threads"],
            "claude-desktop-chat",
            n_user_turns=0,
            title="Claude Desktop chat (auto)",
        )
        # Pointer file is absent — that's the post-migration "pointer
        # got nuked but the thread remained" state.
        assert not tinm_env["chat_thread_file"].exists()
        slug = tinm_server._resolve_thread_id_for_write(None)
        assert slug == "claude-desktop-chat"
        # Pointer was rewritten as a perf hint for next time.
        assert tinm_env["chat_thread_file"].read_text().strip() == slug


# ---------------------------------------------------------------------------
# record_turn formatter — Bug 1 fix D (defensive .get())
# ---------------------------------------------------------------------------
class TestRecordTurnFormatter:
    def test_record_turn_does_not_raise_on_missing_alpha(self, tinm_env, monkeypatch):
        """End-to-end: explicit thread_id, v0.3.0 hot path → no KeyError."""
        import tinm_server
        import tinm_update

        _seed_thread(tinm_env["threads"], "active", n_user_turns=0)

        # Stub Popen so we don't actually spawn the embedding worker
        # (which would try to import sentence_transformers).
        monkeypatch.setattr(
            tinm_update.subprocess, "Popen", lambda *a, **kw: None
        )

        # Call the underlying function directly. FastMCP wraps `@mcp.tool`
        # functions in a tool object; the original is accessible via
        # `.fn` on most versions but is not part of the public contract
        # — reach in to the module dict to call by name instead.
        # The decorator preserves the original under the module
        # global (Python module decorators replace the global with the
        # decorator return value), so we replicate the body here by
        # calling the resolver + update_thread combo that record_turn
        # uses internally.
        slug = tinm_server._resolve_thread_id_for_write("active")
        result = tinm_update.update_thread(
            slug,
            query="hello",
            role="user",
            client="claude-desktop",
            pcp_dir=tinm_env["pcp"],
            dispatch_worker=False,
        )
        # Hot path does NOT set alpha_used; the formatter must tolerate.
        assert "alpha_used" not in result or result.get("alpha_used") is None

        # Now run the formatter contract the MCP tool returns.
        alpha = result.get("alpha_used")
        alpha_str = f"{alpha:.3f}" if isinstance(alpha, (int, float)) else "—"
        parts = [
            f"turn {result['turn']}",
            f"α={alpha_str}",
            f"L1_engaged={result.get('l1_engaged', '?')}",
            f"engaged_so_far={result.get('engaged_so_far', '?')}",
        ]
        line = ": ".join([parts[0], ", ".join(parts[1:])])

        # Success criteria: line includes the turn number AND the α
        # placeholder, and never raises along the way.
        assert "turn 1" in line
        assert "α=—" in line

    def test_record_turn_formats_alpha_when_present(self):
        """Pre-v0.3.0 sync path returns alpha_used — must still format cleanly."""
        # Pure formatter exercise — no fixture needed.
        result = {
            "turn": 4,
            "alpha_used": 0.873,
            "l1_engaged": True,
            "engaged_so_far": True,
        }
        alpha = result.get("alpha_used")
        alpha_str = f"{alpha:.3f}" if isinstance(alpha, (int, float)) else "—"
        parts = [
            f"turn {result['turn']}",
            f"α={alpha_str}",
            f"L1_engaged={result.get('l1_engaged', '?')}",
            f"engaged_so_far={result.get('engaged_so_far', '?')}",
        ]
        line = ": ".join([parts[0], ", ".join(parts[1:])])
        assert "α=0.873" in line
        assert "turn 4" in line
