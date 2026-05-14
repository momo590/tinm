"""Tests for tinm_load._format_context recap with v0.2.1 changes.

Covers T29 (recap budget enforcement) and T30 (lazy migration in recap).
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def tinm_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("TINM_HOME", str(tmp_path))
    monkeypatch.setenv("TINM_PCP_DIR", str(tmp_path / "pcp"))
    for mod in ["tinm_paths", "tinm_load"]:
        sys.modules.pop(mod, None)
    return tmp_path


def _thread_stub(thread_id: str) -> dict:
    return {
        "thread_id": thread_id,
        "pcp_version": "0.1",
        "metadata": {
            "title": "Test",
            "created_at": "2026-05-14T00:00:00Z",
            "last_updated": "2026-05-14T00:00:00Z",
        },
        "anchor": {"update_count": 0, "engaged_so_far": False},
        "trajectory": [],
    }


def _artifacts_stub(entries: list[dict]) -> dict:
    return {"thread_id": "t1", "artifacts": entries}


def test_recap_caps_named_artifacts_to_5(tinm_tmp):
    from tinm_load import _format_context

    arts = _artifacts_stub([
        {
            "id": f"m{i}",
            "name": f"Manual {i}",
            "summary": "...",
            "ref": "f.md",
            "source": "manual",
            "created_at": f"2026-05-{i:02d}T00:00:00Z",
        }
        for i in range(1, 9)  # 8 manual artifacts
    ])
    out = _format_context(_thread_stub("t1"), arts, n_trajectory=5)
    assert "## Named artifacts (5 of 8, capped)" in out
    # Most recent (highest dates) should be present, oldest absent
    assert "Manual 8" in out
    assert "Manual 4" in out
    assert "Manual 1" not in out


def test_recap_caps_approved_to_2(tinm_tmp):
    from tinm_load import _format_context

    arts = _artifacts_stub([
        {
            "id": f"a{i}",
            "name": f"Approved {i}",
            "summary": "Some approved content...",
            "source": "approved_exchange",
            "created_at": f"2026-05-{i:02d}T00:00:00Z",
            "approval": {"trigger_phrase": f"OK {i}", "next_user_turn": i},
        }
        for i in range(1, 6)
    ])
    out = _format_context(_thread_stub("t1"), arts, n_trajectory=5)
    assert "## Recent approved exchanges (2 of 5, capped)" in out
    assert "Approved 5" in out
    assert "Approved 4" in out
    assert "Approved 1" not in out


def test_recap_splits_sources_correctly(tinm_tmp):
    from tinm_load import _format_context

    arts = _artifacts_stub([
        {
            "id": "m1",
            "name": "ManualOne",
            "summary": "...",
            "source": "manual",
            "created_at": "2026-05-10T00:00:00Z",
        },
        {
            "id": "a1",
            "name": "ApprovedOne",
            "summary": "...",
            "source": "approved_exchange",
            "created_at": "2026-05-11T00:00:00Z",
        },
        {
            "id": "l1",
            "name": "LegacyOne",
            "summary": "...",
            "source": "legacy_manual",
            "created_at": "2026-05-09T00:00:00Z",
        },
    ])
    out = _format_context(_thread_stub("t1"), arts, n_trajectory=5)
    # Manual + legacy_manual both appear under named
    assert "ManualOne" in out
    assert "LegacyOne" in out
    # Approved appears under separate section
    assert "ApprovedOne" in out
    assert "## Recent approved exchanges" in out


def test_recap_no_approved_section_when_empty(tinm_tmp):
    from tinm_load import _format_context

    arts = _artifacts_stub([
        {
            "id": "m1",
            "name": "ManualOne",
            "summary": "...",
            "source": "manual",
            "created_at": "2026-05-10T00:00:00Z",
        }
    ])
    out = _format_context(_thread_stub("t1"), arts, n_trajectory=5)
    assert "## Recent approved exchanges" not in out


def test_lazy_legacy_source(tinm_tmp):
    """T30 — entries without `source` are treated as legacy_manual."""
    from tinm_load import _format_context

    arts = _artifacts_stub([
        {
            "id": "old",
            "name": "OldNoSource",
            "summary": "...",
            "created_at": "2026-04-01T00:00:00Z",
        }
    ])
    out = _format_context(_thread_stub("t1"), arts, n_trajectory=5)
    assert "OldNoSource" in out
    # Shows up as named (since legacy_manual), not approved
    assert "## Named artifacts" in out
    assert "## Recent approved exchanges" not in out
