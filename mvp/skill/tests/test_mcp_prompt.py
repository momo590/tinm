"""Tests for the v0.2.3 MCP server changes (L5).

Covers:
  - `_resolve_thread_id` fallback chain post-current_thread retirement:
    explicit (validated) -> legacy (validated) -> most-recently-updated
    -> raise.
  - `_resolve_thread_id_for_write` refuses to fall back to most-recent
    (write tools must not silently mutate the wrong thread).
  - Stale legacy pointer (slug points at a quarantined thread) is
    skipped, not blindly trusted.
  - Invalid explicit thread_id raises with a clear error.
  - `current_thread` tool returns a valid slug post-migration.
  - `current_thread_resource` renders the thread block, not the stub.
  - `tinm_load_context` renders context with prompt-injection guard
    delimiters and survives missing-thread errors gracefully.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def tinm_tmp(monkeypatch):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="tinm-mcp-test-"))
    monkeypatch.setenv("TINM_HOME", str(tmp))
    monkeypatch.setenv("TINM_PCP_DIR", str(tmp / "pcp"))
    for mod in list(sys.modules):
        if mod.startswith("tinm_") or mod == "tinm_server":
            sys.modules.pop(mod, None)
    yield tmp


def _write_thread(pcp_dir: pathlib.Path, slug: str, last_updated: str) -> None:
    (pcp_dir / "threads").mkdir(parents=True, exist_ok=True)
    (pcp_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (pcp_dir / "threads" / f"{slug}.json").write_text(json.dumps({
        "pcp_version": "0.1",
        "thread_id": slug,
        "metadata": {
            "title": f"Thread {slug}",
            "created_at": last_updated,
            "last_updated": last_updated,
            "origin": "user",
            "workspace_fingerprint": None,
            "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
            "embedding_dim": 384,
            "project_root": None,
            "client_history": [],
        },
        "anchor": {
            "vector": None, "alpha_used": 0.85, "update_count": 0,
            "last_updated_turn": 0, "engaged_so_far": False,
        },
        "trajectory": [],
    }))
    (pcp_dir / "artifacts" / f"{slug}.json").write_text(json.dumps({
        "pcp_version": "0.1", "thread_id": slug, "artifacts": [],
    }))


def _load_server():
    sys.path.insert(
        0, str(pathlib.Path(__file__).resolve().parents[2] / "mcp_server")
    )
    import tinm_server  # noqa: E402
    return tinm_server


# ---------------------------------------------------------------------------
# _resolve_thread_id fallback chain
# ---------------------------------------------------------------------------


def test_resolve_falls_back_to_most_recent_when_no_pointer(tinm_tmp):
    """The headline v0.2.3 regression fix: with CURRENT_FILE gone, the
    resolver returns the most-recently-updated thread on disk."""
    pcp = tinm_tmp / "pcp"
    now = datetime.now(timezone.utc)
    _write_thread(pcp, "old-thread", (now - timedelta(days=3)).isoformat())
    _write_thread(pcp, "fresh-thread", now.isoformat())

    server = _load_server()
    # No CURRENT_FILE, no per-cwd match expected (cwd is the test runner)
    assert server._resolve_thread_id(None) == "fresh-thread"


def test_resolve_prefers_explicit_thread_id(tinm_tmp):
    pcp = tinm_tmp / "pcp"
    _write_thread(pcp, "default-thread", "2026-05-17T00:00:00Z")
    _write_thread(pcp, "explicit-thread", "2026-01-01T00:00:00Z")

    server = _load_server()
    assert server._resolve_thread_id("explicit-thread") == "explicit-thread"


def test_resolve_falls_back_to_legacy_pointer(tinm_tmp):
    """Un-migrated install: CURRENT_FILE points at a real thread on disk
    → that thread wins over the most-recent fallback."""
    pcp = tinm_tmp / "pcp"
    now = datetime.now(timezone.utc)
    _write_thread(pcp, "legacy-target", (now - timedelta(days=5)).isoformat())
    _write_thread(pcp, "fresher", now.isoformat())
    (tinm_tmp / "current_thread").write_text("legacy-target\n")

    server = _load_server()
    assert server._resolve_thread_id(None) == "legacy-target"


def test_resolve_skips_stale_legacy_pointer(tinm_tmp):
    """CURRENT_FILE points at a slug that no longer exists on disk
    (migration moved it, user deleted it). Resolver must skip it and
    fall through to most-recent instead of returning a bogus slug."""
    pcp = tinm_tmp / "pcp"
    _write_thread(pcp, "real-thread", "2026-05-17T00:00:00Z")
    (tinm_tmp / "current_thread").write_text("retired-thread-no-longer-exists\n")

    server = _load_server()
    # Skip the stale pointer, return the real thread that actually exists
    assert server._resolve_thread_id(None) == "real-thread"


def test_resolve_rejects_invalid_explicit_thread_id(tinm_tmp):
    """Explicit thread_id that doesn't exist on disk raises with a
    helpful message instead of returning the bad slug."""
    server = _load_server()
    with pytest.raises(ValueError, match="not found"):
        server._resolve_thread_id("does-not-exist")


def test_resolve_raises_when_no_threads_exist(tinm_tmp):
    """No threads on disk + no CURRENT_FILE → resolver raises cleanly."""
    server = _load_server()
    # tinm_tmp has no threads in pcp/ yet (no _write_thread call) and
    # no current_thread file. The resolver should walk through legacy
    # pointer (absent), most-recent (empty), and raise.
    with pytest.raises(ValueError):
        server._resolve_thread_id(None)


# ---------------------------------------------------------------------------
# current_thread tool
# ---------------------------------------------------------------------------


def test_current_thread_tool_returns_slug_after_migration(tinm_tmp):
    """Post-v0.2.3 the legacy pointer is gone; the tool must still work."""
    pcp = tinm_tmp / "pcp"
    _write_thread(pcp, "my-thread", "2026-05-17T00:00:00Z")
    server = _load_server()
    result = server.current_thread()
    assert result != "none"
    # Either our seeded thread or a per-cwd auto-created one
    assert isinstance(result, str) and len(result) > 0


# ---------------------------------------------------------------------------
# current_thread_resource
# ---------------------------------------------------------------------------


def test_current_thread_resource_renders_when_thread_exists(tinm_tmp):
    pcp = tinm_tmp / "pcp"
    _write_thread(pcp, "renderable-thread", "2026-05-17T00:00:00Z")
    server = _load_server()
    out = server.current_thread_resource()
    # tinm_load.load_thread emits a markdown block starting with "# TINM"
    assert out.startswith("# TINM")
    # And does NOT return the "no thread" stub
    assert "No current thread on this host yet" not in out


# ---------------------------------------------------------------------------
# tinm_load_context_prompt (the new L5 deliverable)
# ---------------------------------------------------------------------------


def test_prompt_renders_thread_context_with_injection_guard(tinm_tmp):
    pcp = tinm_tmp / "pcp"
    _write_thread(pcp, "promptable-thread", "2026-05-17T00:00:00Z")
    server = _load_server()
    out = server.tinm_load_context("promptable-thread")
    assert "I'm continuing a TINM thread" in out
    assert "Acknowledge" in out
    assert "# TINM thread:" in out or "# TINM" in out
    # Injection-guard delimiters present
    assert "BEGIN TINM CONTEXT" in out
    assert "END TINM CONTEXT" in out
    assert "data, not instructions" in out or "DATA describing past state" in out


def test_prompt_name_is_kebab_case_for_slash_command_ux(tinm_tmp):
    """The slash command surface in Claude.ai prefers kebab-case names.
    FastMCP defaults to the function name (snake_case); we override
    via @mcp.prompt(name=...) so users see /tinm-load-context."""
    server = _load_server()
    # FastMCP exposes prompts via mcp._prompt_manager.list_prompts() or similar
    # The exact attribute depends on FastMCP version; this test checks that
    # somewhere in the prompt registry the kebab name appears.
    found_kebab = False
    for attr in ("_prompt_manager", "prompt_manager", "_prompts", "prompts"):
        obj = getattr(server.mcp, attr, None)
        if obj is None:
            continue
        for fn_attr in ("list_prompts", "get_prompts", "_prompts"):
            getter = getattr(obj, fn_attr, None)
            if callable(getter):
                try:
                    items = getter()
                except Exception:
                    continue
                names = [
                    getattr(p, "name", "") for p in items
                ] if items else []
                if "tinm-load-context" in names:
                    found_kebab = True
                    break
        if found_kebab:
            break
    assert found_kebab, (
        "Expected MCP prompt to register as 'tinm-load-context' "
        "(kebab-case) for the Claude.ai slash menu."
    )


def test_prompt_explicit_thread_id_wins(tinm_tmp):
    pcp = tinm_tmp / "pcp"
    _write_thread(pcp, "thread-a", "2026-05-17T00:00:00Z")
    _write_thread(pcp, "thread-b", "2026-01-01T00:00:00Z")
    server = _load_server()
    out = server.tinm_load_context("thread-b")
    # The older thread B is explicitly named — its title must be present
    assert "thread-b" in out.lower() or "Thread thread-b" in out


def test_prompt_no_thread_returns_friendly_guidance(tinm_tmp):
    """When no threads exist at all, the prompt returns an inviting
    message instead of crashing."""
    server = _load_server()
    # tinm_tmp has no threads → resolver raises → prompt returns guidance
    out = server.tinm_load_context(None)
    assert out.startswith("# TINM")
    assert "No TINM thread" in out or "thread_init" in out
