"""Tests for tinm_sr.py — Successor Representation matrix."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))
import tinm_sr


@pytest.fixture(autouse=True)
def patch_pcp_dir(tmp_path, monkeypatch):
    """Redirect all SR matrix storage to a temp directory."""
    monkeypatch.setattr(tinm_sr, "TINM_PCP_DIR", tmp_path)


# ── _get_m / _set_m ────────────────────────────────────────────────────────

def test_get_m_missing_row_self():
    assert tinm_sr._get_m({}, "A", "A") == 1.0  # identity


def test_get_m_missing_row_other():
    assert tinm_sr._get_m({}, "A", "B") == 0.0


def test_get_m_existing():
    matrix = {"A": {"B": 0.42}}
    assert tinm_sr._get_m(matrix, "A", "B") == pytest.approx(0.42)


def test_set_m_creates_row():
    matrix = {}
    tinm_sr._set_m(matrix, "A", "B", 0.5)
    assert matrix["A"]["B"] == pytest.approx(0.5)


# ── n_cooccurrences ─────────────────────────────────────────────────────────

def test_n_cooccurrences_empty():
    assert tinm_sr.n_cooccurrences("thread-x") == 0


def test_n_cooccurrences_after_update(tmp_path):
    tinm_sr.update("thread-x", ["A"], ["B"])
    assert tinm_sr.n_cooccurrences("thread-x") == 1


# ── update ──────────────────────────────────────────────────────────────────

def test_update_increments_cooccurrences():
    tinm_sr.update("t1", ["A", "B"], ["C"])
    assert tinm_sr.n_cooccurrences("t1") == 2  # 2 pairs (A,C) + (B,C)


def test_update_local_scale_increases_m():
    tinm_sr.update("t2", ["A"], ["B"])
    m0 = tinm_sr._load_matrix("t2", 0)
    val = tinm_sr._get_m(m0, "A", "B")
    # After 1 TD update with alpha=0.3, reward=1.0, bootstrap~1.0
    assert val > 0


def test_update_global_scale_file_created():
    tinm_sr.update("t3", ["X"], ["Y"])
    path_k1 = tinm_sr._sr_path("t3", 1)
    assert path_k1.is_file()


def test_update_empty_prev_noop():
    tinm_sr.update("t4", [], ["A"])
    assert tinm_sr.n_cooccurrences("t4") == 0


def test_update_empty_curr_noop():
    tinm_sr.update("t5", ["A"], [])
    assert tinm_sr.n_cooccurrences("t5") == 0


def test_update_accumulates_across_calls():
    for _ in range(5):
        tinm_sr.update("t6", ["A"], ["B"])
    m0 = tinm_sr._load_matrix("t6", 0)
    val_after_5 = tinm_sr._get_m(m0, "A", "B")
    # After many updates, M[A][B] converges toward 1.0 + gamma*bootstrap
    assert val_after_5 > 0.5


def test_update_global_scale_slower_than_local():
    for _ in range(3):
        tinm_sr.update("t7", ["A"], ["B"])
    m0 = tinm_sr._load_matrix("t7", 0)
    m1 = tinm_sr._load_matrix("t7", 1)
    v0 = tinm_sr._get_m(m0, "A", "B")
    v1 = tinm_sr._get_m(m1, "A", "B")
    # Global scale (alpha=0.05) updates slower than local (alpha=0.3)
    assert v0 > v1


# ── score_courant ────────────────────────────────────────────────────────────

def test_score_courant_empty_matrix():
    fake_emb = [0.1] * 384
    artifacts = {"A": {"embedding": fake_emb}}
    result = tinm_sr.score_courant("A", fake_emb, artifacts, "thread-empty")
    assert result == 0.0  # no matrix entries → 0


def test_score_courant_after_update():
    fake_emb = [0.1] * 384
    artifacts_by_id = {
        "A": {"embedding": fake_emb},
        "B": {"embedding": fake_emb},
    }
    tinm_sr.update("t8", ["A"], ["B"])
    result = tinm_sr.score_courant("A", fake_emb, artifacts_by_id, "t8")
    assert 0.0 <= result <= 1.0


def test_score_courant_returns_zero_on_error():
    result = tinm_sr.score_courant("A", [], {}, "bad-thread")
    assert result == 0.0


# ── prune ────────────────────────────────────────────────────────────────────

def test_prune_removes_dead_rows():
    tinm_sr.update("t9", ["A", "B"], ["C"])
    active = {"A", "C"}
    tinm_sr.prune("t9", active)
    m0 = tinm_sr._load_matrix("t9", 0)
    assert "B" not in m0


def test_prune_removes_dead_columns():
    tinm_sr.update("t10", ["A"], ["B", "C"])
    active = {"A", "B"}  # C is dead
    tinm_sr.prune("t10", active)
    m0 = tinm_sr._load_matrix("t10", 0)
    row_a = m0.get("A", {})
    assert "C" not in row_a


def test_prune_no_op_when_all_active():
    tinm_sr.update("t11", ["A"], ["B"])
    active = {"A", "B"}
    tinm_sr.prune("t11", active)
    m0 = tinm_sr._load_matrix("t11", 0)
    # Should still have the entry
    assert "A" in m0
