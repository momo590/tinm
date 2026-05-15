"""Tests for tinm_npf.py — Neural Potential Field scorer."""
from __future__ import annotations

import math
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))
import tinm_npf
import tinm_sr


def _unit_vec(seed: int, dim: int = 384) -> list[float]:
    """Deterministic unit vector for testing."""
    import hashlib
    h = int(hashlib.md5(str(seed).encode()).hexdigest(), 16)
    vec = [math.sin(h + i) for i in range(dim)]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


# ── _u_magnetic ──────────────────────────────────────────────────────────────

def test_u_magnetic_identical_vecs():
    v = _unit_vec(1)
    score = tinm_npf._u_magnetic(v, v)
    assert score == pytest.approx(1.0, abs=1e-4)


def test_u_magnetic_orthogonal():
    # Two random vectors are unlikely to be orthogonal but score should be [0,1]
    v1 = _unit_vec(1)
    v2 = _unit_vec(999)
    score = tinm_npf._u_magnetic(v1, v2)
    assert 0.0 <= score <= 1.0


def test_u_magnetic_empty_returns_zero():
    assert tinm_npf._u_magnetic([], [0.1] * 384) == 0.0
    assert tinm_npf._u_magnetic([0.1] * 384, []) == 0.0


# ── _u_olfactif ──────────────────────────────────────────────────────────────

def test_u_olfactif_none():
    assert tinm_npf._u_olfactif(None) == 0.0


def test_u_olfactif_recent():
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    score = tinm_npf._u_olfactif(ts)
    assert score > 0.99  # almost 1 (exp(-0.01*1) ≈ 0.99)


def test_u_olfactif_old():
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    score = tinm_npf._u_olfactif(ts)
    assert score < 0.01  # very decayed


def test_u_olfactif_bad_ts():
    assert tinm_npf._u_olfactif("not-a-timestamp") == 0.0


# ── _u_repulsif ──────────────────────────────────────────────────────────────

def test_u_repulsif_no_rejected():
    v = _unit_vec(1)
    score = tinm_npf._u_repulsif(v, [])
    assert score == 1.0


def test_u_repulsif_similar_to_rejected():
    v = _unit_vec(1)
    rejected = [{"embedding_for_match": v}]  # identical → sim=1.0
    score = tinm_npf._u_repulsif(v, rejected)
    assert score < 1.0  # penalized


def test_u_repulsif_dissimilar_to_rejected():
    v1 = _unit_vec(1)
    v2 = _unit_vec(9999)  # unrelated direction
    rejected = [{"embedding_for_match": v2}]
    score = tinm_npf._u_repulsif(v1, rejected)
    # Unlikely to exceed DEMOTION_THRESHOLD=0.7 for random vectors
    # (can't guarantee but most random pairs have cosine < 0.3)
    assert score >= 0.0


def test_u_repulsif_empty_artifact_emb():
    v = _unit_vec(1)
    score = tinm_npf._u_repulsif([], [{"embedding_for_match": v}])
    assert score == 1.0  # no penalty when artifact has no embedding


def test_u_repulsif_entry_without_embedding():
    v = _unit_vec(1)
    rejected = [{"rejection_reason": "bad idea"}]  # no embedding_for_match
    score = tinm_npf._u_repulsif(v, rejected)
    assert score == 1.0


# ── score (composite) ─────────────────────────────────────────────────────────

def test_score_returns_float_in_range(tmp_path, monkeypatch):
    monkeypatch.setattr(tinm_sr, "TINM_PCP_DIR", tmp_path)
    monkeypatch.setattr(
        __import__("tinm_scoring", fromlist=["_encode"]),
        "_encode",
        lambda text: _unit_vec(hash(text) % 1000),
    )

    from datetime import datetime, timezone, timedelta
    artifact = {
        "id": "art-1",
        "name": "test artifact",
        "embedding": _unit_vec(1),
        "last_retrieved_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    }
    anchor = _unit_vec(2)
    query_vec = _unit_vec(3)
    result, heads = tinm_npf.score(
        artifact=artifact,
        query_vec=query_vec,
        anchor_vec=anchor,
        rejected_log=[],
        thread_id="test-thread",
        artifacts_by_id={"art-1": artifact},
        n_sr_cooccurrences=0,
    )
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0
    assert "u_magnetic" in heads
    assert "u_olfactif" in heads
    assert "u_courant" in heads
    assert "u_repulsif" in heads


def test_score_sr_gating_cold_start(tmp_path, monkeypatch):
    """With n_sr=0, w_courant should be 0 and w_magnetic should increase."""
    monkeypatch.setattr(tinm_sr, "TINM_PCP_DIR", tmp_path)

    artifact = {
        "id": "art-2",
        "name": "test",
        "embedding": _unit_vec(1),
        "last_retrieved_at": None,
    }
    _, heads_cold = tinm_npf.score(
        artifact=artifact,
        query_vec=_unit_vec(2),
        anchor_vec=_unit_vec(3),
        rejected_log=[],
        thread_id="t-cold",
        artifacts_by_id={},
        n_sr_cooccurrences=0,  # cold
    )
    _, heads_warm = tinm_npf.score(
        artifact=artifact,
        query_vec=_unit_vec(2),
        anchor_vec=_unit_vec(3),
        rejected_log=[],
        thread_id="t-cold",
        artifacts_by_id={},
        n_sr_cooccurrences=15,  # warm
    )
    assert heads_cold["w_cou"] == 0.0
    assert heads_cold["sr_gating_active"] is True
    assert heads_warm["w_cou"] > 0.0
    assert heads_warm["sr_gating_active"] is False


def test_score_weights_sum_to_one_cold(tmp_path, monkeypatch):
    monkeypatch.setattr(tinm_sr, "TINM_PCP_DIR", tmp_path)
    artifact = {"id": "a", "name": "x", "embedding": _unit_vec(1), "last_retrieved_at": None}
    _, heads = tinm_npf.score(
        artifact=artifact,
        query_vec=_unit_vec(2),
        anchor_vec=_unit_vec(3),
        rejected_log=[],
        thread_id="t-ws",
        artifacts_by_id={},
        n_sr_cooccurrences=0,
    )
    total_w = heads["w_mag"] + heads["w_olf"] + heads["w_cou"] + heads["w_rep"]
    assert total_w == pytest.approx(1.0, abs=0.01)


# ── rank_with_npf ─────────────────────────────────────────────────────────────

def test_rank_with_npf_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(tinm_sr, "TINM_PCP_DIR", tmp_path)
    with patch.object(
        __import__("tinm_scoring", fromlist=["_encode"]),
        "_encode",
        side_effect=lambda t: _unit_vec(hash(t) % 100),
    ):
        result = tinm_npf.rank_with_npf([], "query", _unit_vec(1), [], "t", k=3)
    assert result == []


def test_rank_with_npf_returns_k_results(tmp_path, monkeypatch):
    monkeypatch.setattr(tinm_sr, "TINM_PCP_DIR", tmp_path)

    artifacts = [
        {"id": f"a{i}", "name": f"artifact {i}", "embedding": _unit_vec(i), "last_retrieved_at": None}
        for i in range(5)
    ]
    with patch.object(
        __import__("tinm_scoring", fromlist=["_encode"]),
        "_encode",
        side_effect=lambda t: _unit_vec(hash(t) % 100),
    ):
        result = tinm_npf.rank_with_npf(
            artifacts, "test query", _unit_vec(99), [], "t", k=3
        )
    assert len(result) == 3
    assert all(r["match_kind"] == "npf" for r in result)


def test_rank_with_npf_rejected_note(tmp_path, monkeypatch):
    """Artifacts similar to rejected entries should get a rejected_note."""
    monkeypatch.setattr(tinm_sr, "TINM_PCP_DIR", tmp_path)

    v = _unit_vec(42)
    artifact = {"id": "bad", "name": "stale rec", "embedding": v, "last_retrieved_at": None}
    rejected_log = [{"embedding_for_match": v, "rejection_reason": "stale recommendation"}]

    with patch.object(
        __import__("tinm_scoring", fromlist=["_encode"]),
        "_encode",
        side_effect=lambda t: _unit_vec(hash(t) % 100),
    ):
        result = tinm_npf.rank_with_npf([artifact], "stale", v, rejected_log, "t", k=1)

    assert len(result) == 1
    assert "rejected_note" in result[0]
    assert "stale recommendation" in result[0]["rejected_note"]
