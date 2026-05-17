"""Smoke tests for tinm_handoff handoff-prompt composition.

The comprehensive coverage lives in mvp/skill/tests/test_handoff.py
(co-located with the skill). This file is the minimal additive set the
v0.3.0 audit asked for: happy path render, unknown thread_id error,
unsupported target rejection, and a contains-artifacts check.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))


@pytest.fixture
def isolated_tinm(monkeypatch, tmp_path):
    """Point TINM storage at tmp_path and refresh cached path constants."""
    monkeypatch.setenv("TINM_HOME", str(tmp_path))
    monkeypatch.setenv("TINM_PCP_DIR", str(tmp_path / "pcp"))
    for mod in ("tinm_paths", "tinm_handoff"):
        sys.modules.pop(mod, None)
    return tmp_path


def _thread_payload(thread_id: str = "smoke") -> dict:
    return {
        "pcp_version": "0.1",
        "thread_id": thread_id,
        "metadata": {
            "title": "Smoke handoff",
            "created_at": "2026-05-17T10:00:00Z",
            "last_updated": "2026-05-17T11:00:00Z",
            "workspace_fingerprint": "sha256:abcdef0123456789aaaaaaaaaaaaaaaa",
        },
        "anchor": {
            "update_count": 1,
            "engaged_so_far": True,
            "top_terms": ["handoff", "smoke", "v0.3.0"],
        },
        "trajectory": [
            {"turn": 1, "role": "user", "text": "kick off handoff feature",
             "ts": "2026-05-17T10:00:00Z"},
            {"turn": 2, "role": "user", "text": "ship v0.3.0",
             "ts": "2026-05-17T10:30:00Z"},
        ],
    }


def _artifacts_payload() -> dict:
    return {
        "pcp_version": "0.1",
        "thread_id": "smoke",
        "artifacts": [
            {
                "id": "a1",
                "name": "v0.3.0 release notes",
                "ref": "CHANGELOG.md",
                "summary": "What ships in v0.3.0.",
                "source": "manual",
                "created_at": "2026-05-17T09:00:00Z",
            }
        ],
    }


def test_happy_path_render_returns_non_empty_markdown(isolated_tinm):
    from tinm_handoff import render_handoff

    out = render_handoff(_thread_payload(), _artifacts_payload(), target="generic")
    assert out.strip()
    assert "# Handoff — Smoke handoff" in out
    assert "**Thread:** `smoke`" in out
    assert "## Suggested next prompt" in out


def test_render_includes_recent_artifacts(isolated_tinm):
    """The prompt should surface named artifacts so the next agent can find them."""
    from tinm_handoff import render_handoff

    out = render_handoff(_thread_payload(), _artifacts_payload(), target="generic")
    assert "## Named artifacts you may need" in out
    assert "v0.3.0 release notes" in out
    assert "CHANGELOG.md" in out


def test_unknown_thread_id_exits_nonzero(isolated_tinm, capsys):
    """CLI returns 1 and writes a clear error when the thread is missing."""
    import tinm_handoff

    rc = tinm_handoff.main(["does-not-exist"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_unsupported_target_raises_value_error(isolated_tinm):
    """An unsupported target (e.g. 'codex') is rejected with a clear ValueError."""
    from tinm_handoff import render_handoff

    with pytest.raises(ValueError):
        render_handoff(_thread_payload(), _artifacts_payload(), target="codex")


def test_render_quotes_last_user_turn(isolated_tinm):
    """Suggested next prompt quotes the last thing the user asked."""
    from tinm_handoff import render_handoff

    out = render_handoff(_thread_payload(), _artifacts_payload(), target="generic")
    suggested = out.split("## Suggested next prompt", 1)[1]
    assert "ship v0.3.0" in suggested
    assert "Last thing I asked:" in suggested
