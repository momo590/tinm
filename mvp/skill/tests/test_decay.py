"""Tests for tinm_decay.decay_thread().

Covers T33 (90-day decay archives stale auto-artifact) and T34
(manual artifacts are never decayed).
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
    """Spin up an isolated TINM_HOME so tests never touch real state."""
    tmp = tempfile.mkdtemp(prefix="tinm-test-")
    monkeypatch.setenv("TINM_HOME", tmp)
    monkeypatch.setenv("TINM_PCP_DIR", str(pathlib.Path(tmp) / "pcp"))
    # Reload tinm_paths so module-level constants pick up the new env.
    for mod in ["tinm_paths", "tinm_decay"]:
        sys.modules.pop(mod, None)
    yield pathlib.Path(tmp)


def _write_artifacts(pcp_dir: pathlib.Path, thread_id: str, entries: list[dict]) -> None:
    art_dir = pcp_dir / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / f"{thread_id}.json").write_text(
        json.dumps({"thread_id": thread_id, "artifacts": entries}, indent=2)
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def test_decays_old_approved_exchange(tinm_tmp):
    from tinm_decay import decay_thread

    now = datetime(2026, 5, 14, tzinfo=timezone.utc)
    old = now - timedelta(days=120)
    fresh = now - timedelta(days=30)
    _write_artifacts(
        tinm_tmp / "pcp",
        "demo",
        [
            {
                "id": "a1",
                "name": "Old approved",
                "source": "approved_exchange",
                "last_retrieved_at": _iso(old),
            },
            {
                "id": "a2",
                "name": "Fresh approved",
                "source": "approved_exchange",
                "last_retrieved_at": _iso(fresh),
            },
        ],
    )

    result = decay_thread("demo", now=now)
    assert result["moved"] == 1
    assert result["kept"] == 1

    hot = json.loads((tinm_tmp / "pcp" / "artifacts" / "demo.json").read_text())
    cold = json.loads((tinm_tmp / "pcp" / "artifacts_cold" / "demo.json").read_text())
    assert [a["id"] for a in hot["artifacts"]] == ["a2"]
    assert [a["id"] for a in cold["artifacts"]] == ["a1"]


def test_never_decays_manual_artifacts(tinm_tmp):
    from tinm_decay import decay_thread

    now = datetime(2026, 5, 14, tzinfo=timezone.utc)
    ancient = now - timedelta(days=400)
    _write_artifacts(
        tinm_tmp / "pcp",
        "demo",
        [
            {
                "id": "m1",
                "name": "Ancient manual",
                "source": "manual",
                "last_retrieved_at": _iso(ancient),
            },
            {
                "id": "m2",
                "name": "Ancient legacy_manual",
                "source": "legacy_manual",
                "last_retrieved_at": _iso(ancient),
            },
        ],
    )

    result = decay_thread("demo", now=now)
    assert result["moved"] == 0
    assert result["skipped_manual"] == 2

    cold_path = tinm_tmp / "pcp" / "artifacts_cold" / "demo.json"
    assert not cold_path.exists()


def test_noop_when_thread_has_no_artifacts(tinm_tmp):
    from tinm_decay import decay_thread

    result = decay_thread("does-not-exist")
    assert result == {"moved": 0, "kept": 0, "skipped_manual": 0}


def test_uses_created_at_when_last_retrieved_at_missing(tinm_tmp):
    from tinm_decay import decay_thread

    now = datetime(2026, 5, 14, tzinfo=timezone.utc)
    old = now - timedelta(days=120)
    _write_artifacts(
        tinm_tmp / "pcp",
        "demo",
        [
            {
                "id": "a1",
                "name": "No last_retrieved_at",
                "source": "approved_exchange",
                "created_at": _iso(old),
            },
        ],
    )

    result = decay_thread("demo", now=now)
    assert result["moved"] == 1
