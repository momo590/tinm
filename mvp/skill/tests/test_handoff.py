"""Tests for tinm_handoff (L2: handoff-prompt generator v1).

Pure-template generator — no LLM — so the tests assert exact-ish
substrings on the rendered markdown. They never touch the user's real
TINM_HOME: each test gets its own isolated tmp dir via the `tinm_tmp`
fixture, mirroring the convention used by `test_load_recap.py` and
`test_provenance.py`.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def tinm_tmp(monkeypatch, tmp_path):
    """Isolated TINM_HOME / TINM_PCP_DIR for a single test."""
    monkeypatch.setenv("TINM_HOME", str(tmp_path))
    monkeypatch.setenv("TINM_PCP_DIR", str(tmp_path / "pcp"))
    # Force re-import so module-level Path constants pick up the env vars.
    for mod in ("tinm_paths", "tinm_handoff"):
        sys.modules.pop(mod, None)
    return tmp_path


def _write_thread(tmp_path: pathlib.Path, thread_id: str, payload: dict) -> None:
    threads_dir = tmp_path / "pcp" / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    (threads_dir / f"{thread_id}.json").write_text(json.dumps(payload))


def _write_artifacts(tmp_path: pathlib.Path, thread_id: str, payload: dict) -> None:
    arts_dir = tmp_path / "pcp" / "artifacts"
    arts_dir.mkdir(parents=True, exist_ok=True)
    (arts_dir / f"{thread_id}.json").write_text(json.dumps(payload))


def _rich_thread() -> dict:
    return {
        "pcp_version": "0.1",
        "thread_id": "rich-thread",
        "metadata": {
            "title": "Rich thread — wedge analysis",
            "created_at": "2026-05-10T10:00:00Z",
            "last_updated": "2026-05-17T12:30:00Z",
            "workspace_fingerprint": "sha256:abcdef0123456789aaaaaaaaaaaaaaaa",
        },
        "anchor": {
            "update_count": 4,
            "engaged_so_far": True,
            "top_terms": [
                "wedge",
                "handoff",
                "prompt",
                "agent",
                "karpathy",
                "next",
                "tinm",
            ],
        },
        "trajectory": [
            {"turn": 1, "role": "user", "text": "Let's design the handoff feature.",
             "ts": "2026-05-17T11:00:00Z"},
            {"turn": 2, "role": "assistant", "text": "OK, here's a sketch...",
             "ts": "2026-05-17T11:01:00Z"},
            {"turn": 3, "role": "user", "text": "Add a clipboard flag.",
             "ts": "2026-05-17T11:05:00Z"},
            {"turn": 4, "role": "assistant", "text": "Done.",
             "ts": "2026-05-17T11:06:00Z"},
            {"turn": 5, "role": "user",
             "text": "Now generate me a prompt I can paste into the next agent.",
             "ts": "2026-05-17T11:10:00Z"},
        ],
    }


def _rich_artifacts() -> dict:
    return {
        "pcp_version": "0.1",
        "thread_id": "rich-thread",
        "artifacts": [
            {
                "id": "wedge-doc",
                "name": "Prompt handoff wedge memo",
                "ref": "memory/handoff.md",
                "summary": "The wedge: TINM composes the next prompt for the next agent. Karpathy Software 3.0.",
                "source": "manual",
                "created_at": "2026-05-14T09:00:00Z",
            },
            {
                "id": "design-doc",
                "name": "L2 handoff design doc",
                "ref": "designs/L2.md",
                "summary": "v1 pure template, v2 LLM rewrite out of scope.",
                "source": "manual",
                "created_at": "2026-05-17T08:00:00Z",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Section composition
# ---------------------------------------------------------------------------


def test_rich_thread_emits_all_expected_sections(tinm_tmp):
    from tinm_handoff import render_handoff

    out = render_handoff(_rich_thread(), _rich_artifacts(), target="generic")

    assert "# Handoff — Rich thread — wedge analysis" in out
    assert "**Thread:** `rich-thread`" in out
    assert "**Last active:** 2026-05-17T12:30:00Z" in out
    assert "## Where we were" in out
    # User turns 1, 3, 5 should appear — assistant turns should NOT.
    assert "Let's design the handoff feature." in out
    assert "Add a clipboard flag." in out
    assert "Now generate me a prompt I can paste into the next agent." in out
    assert "OK, here's a sketch" not in out
    # Anchor terms — at least the top few
    assert "## Anchor (what's in working memory)" in out
    assert "`wedge`" in out
    assert "`handoff`" in out
    # Artifacts
    assert "## Named artifacts you may need" in out
    assert "Prompt handoff wedge memo" in out
    assert "L2 handoff design doc" in out
    # Suggested next prompt — deterministic composition
    assert "## Suggested next prompt" in out
    assert "Continue working on Rich thread — wedge analysis." in out
    assert "Most recent topic: wedge, handoff, prompt." in out
    # Last user turn quoted
    assert "Now generate me a prompt I can paste into the next agent." in out


def test_missing_anchor_top_terms_skips_anchor_section(tinm_tmp):
    from tinm_handoff import render_handoff

    thread = _rich_thread()
    # Drop top_terms entirely
    thread["anchor"].pop("top_terms", None)
    out = render_handoff(thread, _rich_artifacts(), target="generic")

    assert "## Anchor (what's in working memory)" not in out
    # Other sections still present
    assert "## Where we were" in out
    assert "## Named artifacts you may need" in out
    # Suggested prompt should degrade gracefully — no "Most recent topic" line
    assert "Most recent topic:" not in out
    # But the core "Continue working on ..." should still be there
    assert "Continue working on" in out


def test_empty_trajectory_returns_minimal_but_valid(tinm_tmp):
    from tinm_handoff import render_handoff

    thread = {
        "pcp_version": "0.1",
        "thread_id": "minimal",
        "metadata": {
            "title": "Empty thread",
            "created_at": "2026-05-17T00:00:00Z",
            "last_updated": "2026-05-17T00:00:00Z",
        },
        "anchor": {"update_count": 0, "engaged_so_far": False},
        "trajectory": [],
    }
    artifacts = {"artifacts": []}

    out = render_handoff(thread, artifacts, target="generic")

    # Header still there
    assert "# Handoff — Empty thread" in out
    assert "**Thread:** `minimal`" in out
    # Where-we-were section present, but with the empty-state marker
    assert "## Where we were" in out
    assert "_(no user turns yet)_" in out
    # No anchor, no artifacts
    assert "## Anchor" not in out
    assert "## Named artifacts" not in out
    # Suggested prompt is still emitted, with bare "Continue working on" line
    assert "## Suggested next prompt" in out
    assert "Continue working on Empty thread." in out
    # No "Last thing I asked" since there are no user turns
    assert "Last thing I asked:" not in out


def test_target_claude_code_adds_load_line(tinm_tmp):
    from tinm_handoff import render_handoff

    out = render_handoff(_rich_thread(), _rich_artifacts(), target="claude-code")

    # The load hint is the FIRST line so the receiving agent sees it first
    first_line = out.splitlines()[0]
    assert first_line == "/tinm load rich-thread  # if TINM is installed on this machine"
    # Header still follows
    assert "# Handoff — Rich thread — wedge analysis" in out


def test_target_openclaw_adds_bumblebee_line(tinm_tmp):
    from tinm_handoff import render_handoff

    out = render_handoff(_rich_thread(), _rich_artifacts(), target="openclaw")

    first_line = out.splitlines()[0]
    assert first_line == "@bumblebee load thread rich-thread"


def test_target_generic_has_no_extra_header(tinm_tmp):
    from tinm_handoff import render_handoff

    out = render_handoff(_rich_thread(), _rich_artifacts(), target="generic")

    # No load hint at the top
    assert not out.startswith("/tinm load")
    assert not out.startswith("@bumblebee")
    # Just the markdown title
    assert out.startswith("# Handoff —")


def test_clipboard_falls_back_to_stdout_when_no_tool(tinm_tmp, monkeypatch, capsys):
    """Neither pbcopy nor xclip nor wl-copy on PATH → just print, don't crash."""
    import tinm_handoff

    monkeypatch.setattr(tinm_handoff.shutil, "which", lambda _name: None)

    _write_thread(tinm_tmp, "rich-thread", _rich_thread())
    _write_artifacts(tinm_tmp, "rich-thread", _rich_artifacts())

    rc = tinm_handoff.main(["rich-thread", "--clipboard"])

    captured = capsys.readouterr()
    assert rc == 0
    # Rendered output goes to stdout regardless
    assert "# Handoff — Rich thread — wedge analysis" in captured.out
    # Fallback warning on stderr
    assert "clipboard unavailable" in captured.err
    assert "no clipboard tool available" in captured.err


def test_unknown_thread_id_exits_nonzero_with_error(tinm_tmp, capsys):
    import tinm_handoff

    rc = tinm_handoff.main(["does-not-exist"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "not found" in captured.err.lower()


def test_long_last_user_turn_is_truncated(tinm_tmp):
    from tinm_handoff import render_handoff, MAX_LAST_USER_TURN_CHARS

    thread = _rich_thread()
    long_text = "x" * (MAX_LAST_USER_TURN_CHARS + 50)
    thread["trajectory"].append(
        {"turn": 99, "role": "user", "text": long_text, "ts": "2026-05-17T12:00:00Z"}
    )

    out = render_handoff(thread, _rich_artifacts(), target="generic")

    # Truncated snippet ends with ellipsis in the Suggested-next-prompt line
    # (the suggested prompt line is the only place we truncate to 200 chars)
    suggested_section = out.split("## Suggested next prompt", 1)[1]
    # The truncated chunk + ellipsis appear in the suggested prompt line
    assert "x" * MAX_LAST_USER_TURN_CHARS in suggested_section
    assert "…" in suggested_section


def test_top_level_top_terms_fallback_for_legacy_threads(tinm_tmp):
    """Older seed threads store top_terms at the top level, not in anchor."""
    from tinm_handoff import render_handoff

    thread = {
        "pcp_version": "0.1",
        "thread_id": "legacy",
        "metadata": {
            "title": "Legacy seed",
            "created_at": "2026-05-01T00:00:00Z",
            "last_updated": "2026-05-01T00:00:00Z",
        },
        "anchor": {"update_count": 0, "engaged_so_far": False},
        "top_terms": ["alpha", "beta", "gamma"],
        "trajectory": [
            {"turn": 1, "role": "user", "text": "hello", "ts": "2026-05-01T00:00:00Z"},
        ],
    }
    out = render_handoff(thread, {"artifacts": []}, target="generic")

    assert "## Anchor (what's in working memory)" in out
    assert "`alpha`" in out
    assert "Most recent topic: alpha, beta, gamma." in out


def test_cli_writes_rendered_block_to_stdout(tinm_tmp, capsys):
    import tinm_handoff

    _write_thread(tinm_tmp, "rich-thread", _rich_thread())
    _write_artifacts(tinm_tmp, "rich-thread", _rich_artifacts())

    rc = tinm_handoff.main(["rich-thread", "--target", "claude-code"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("/tinm load rich-thread")
    assert "# Handoff — Rich thread — wedge analysis" in captured.out
