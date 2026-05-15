"""TINM Neural Potential Field (NPF) — multi-head artifact scorer.

Implements U_theta from tinm_substrate.md §2:

    U_theta = sum_k w_k(s_t) * U_theta^(k)

Four heads:
  U_magnetic  : global project imprinting — sim(embed(v), anchor)
  U_olfactif  : recency boost — exp(-lambda * delta_t_minutes)
  U_courant   : SR-derived path signal — weighted goal-similarity via SR matrix
  U_repulsif  : rejection penalty — demotes artifacts similar to rejected entries
                [= Feature 2 demotion, unified here per review decision D1]

Score convention: HIGHER = BETTER (flip sign of the energy U_theta).

Default weights:  w = [0.3, 0.2, 0.3, 0.2]
Adaptive gating (D3): if SR matrix < TINM_SR_WARMUP_THRESHOLD co-occurrences,
  w_courant = 0, w_magnetic += w_courant (redistributed to dominant head).

Environment overrides:
  TINM_NPF_WEIGHTS     comma-separated 4 floats (mag,olf,cou,rep), must sum to 1.0
  TINM_SR_WARMUP_THRESHOLD  int, default 10
  TINM_DEMOTION_THRESHOLD  float in [0,1], default 0.7 (cosine sim vs rejected entry)
  TINM_REPULSION_PENALTY   float, default 0.5 (how much to subtract from rep score)
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Optional

from tinm_scoring import _cosine, _encode

# ── Default weights ────────────────────────────────────────────────────────
_DEFAULT_WEIGHTS = (0.3, 0.2, 0.3, 0.2)
_LAMBDA_OLF = 0.01       # recency decay rate in 1/min (half-life ~70 min)
_EPS = 1e-9

DEMOTION_THRESHOLD: float = float(os.environ.get("TINM_DEMOTION_THRESHOLD", "0.7"))
REPULSION_PENALTY: float = float(os.environ.get("TINM_REPULSION_PENALTY", "0.5"))
SR_WARMUP_THRESHOLD: int = int(os.environ.get("TINM_SR_WARMUP_THRESHOLD", "10"))


def _parse_weights() -> tuple[float, float, float, float]:
    raw = os.environ.get("TINM_NPF_WEIGHTS", "")
    if not raw:
        return _DEFAULT_WEIGHTS
    try:
        parts = [float(x) for x in raw.split(",")]
        if len(parts) != 4:
            return _DEFAULT_WEIGHTS
        total = sum(parts)
        if abs(total - 1.0) > 0.01:
            return _DEFAULT_WEIGHTS
        return tuple(parts)  # type: ignore[return-value]
    except ValueError:
        return _DEFAULT_WEIGHTS


def _now_minutes() -> float:
    """Current UTC time as minutes since epoch (for recency calculation)."""
    return datetime.now(timezone.utc).timestamp() / 60.0


def _parse_ts_minutes(ts_str: str) -> float:
    """Parse an ISO 8601 timestamp string → minutes since epoch. 0 on error."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp() / 60.0
    except Exception:
        return 0.0


def _u_magnetic(artifact_emb: list[float], anchor_vec: list[float]) -> float:
    """Global project imprinting. Returns sim in [0, 1]."""
    if not artifact_emb or not anchor_vec:
        return 0.0
    return max(0.0, _cosine(artifact_emb, anchor_vec))


def _u_olfactif(last_retrieved_at: Optional[str]) -> float:
    """Recency boost. Returns exp(-lambda * delta_minutes) in (0, 1].

    Recently seen = close to 1.0. Never seen = 0.0 (no last_retrieved_at).
    """
    if not last_retrieved_at:
        return 0.0
    ts_min = _parse_ts_minutes(last_retrieved_at)
    if ts_min <= 0:
        return 0.0
    now_min = _now_minutes()
    delta = max(0.0, now_min - ts_min)
    return math.exp(-_LAMBDA_OLF * delta)


def _u_repulsif(
    artifact_emb: list[float],
    rejected_log: list[dict],
) -> float:
    """Rejection penalty (= Feature 2 demotion).

    Returns 1.0 (no penalty) if artifact is not similar to any rejected entry.
    Returns (1.0 - REPULSION_PENALTY) if artifact embedding is similar to a
    rejected entry (cosine >= DEMOTION_THRESHOLD).

    The returned value is the "repulsion score contribution". When multiplied
    by w_repulsif and summed, a penalized artifact will score lower.
    """
    if not artifact_emb or not rejected_log:
        return 1.0  # no penalty

    for entry in rejected_log:
        emb = entry.get("embedding_for_match")
        if not emb:
            continue
        sim = _cosine(artifact_emb, emb)
        if sim >= DEMOTION_THRESHOLD:
            return 1.0 - REPULSION_PENALTY  # penalized

    return 1.0


def score(
    artifact: dict,
    query_vec: list[float],
    anchor_vec: list[float],
    rejected_log: list[dict],
    thread_id: str,
    artifacts_by_id: dict[str, dict],
    n_sr_cooccurrences: int = 0,
) -> tuple[float, dict]:
    """Compute the composite NPF score for a single artifact.

    Args:
        artifact: artifact dict (must contain 'embedding', 'last_retrieved_at')
        query_vec: embedding of the current user query
        anchor_vec: EMA anchor embedding (= project_embed, D4 decision)
        rejected_log: list of rejected entry dicts with 'embedding_for_match'
        thread_id: current thread (used to load SR matrix)
        artifacts_by_id: dict[id → artifact] for SR courant computation
        n_sr_cooccurrences: total co-occurrence count for adaptive gating (D3)

    Returns:
        (composite_score: float, head_scores: dict) — head_scores for telemetry/debug
    """
    from tinm_sr import score_courant  # local import to avoid circular deps

    emb = artifact.get("embedding") or []
    w_mag, w_olf, w_cou, w_rep = _parse_weights()

    # D3: adaptive gating — redistribute w_courant if SR is not warm
    if n_sr_cooccurrences < SR_WARMUP_THRESHOLD:
        w_mag += w_cou  # redistribute courant weight to magnetic
        w_cou = 0.0

    # Compute each head
    s_mag = _u_magnetic(emb, anchor_vec)
    s_olf = _u_olfactif(artifact.get("last_retrieved_at"))
    s_cou = score_courant(artifact["id"], query_vec, artifacts_by_id, thread_id) if w_cou > 0 else 0.0
    s_rep = _u_repulsif(emb, rejected_log)

    composite = w_mag * s_mag + w_olf * s_olf + w_cou * s_cou + w_rep * s_rep

    head_scores = {
        "u_magnetic": round(s_mag, 4),
        "u_olfactif": round(s_olf, 4),
        "u_courant": round(s_cou, 4),
        "u_repulsif": round(s_rep, 4),
        "w_mag": round(w_mag, 3),
        "w_olf": round(w_olf, 3),
        "w_cou": round(w_cou, 3),
        "w_rep": round(w_rep, 3),
        "n_sr_cooccurrences": n_sr_cooccurrences,
        "sr_gating_active": n_sr_cooccurrences < SR_WARMUP_THRESHOLD,
    }

    return composite, head_scores


def rank_with_npf(
    artifacts: list[dict],
    query: str,
    anchor_vec: list[float],
    rejected_log: list[dict],
    thread_id: str,
    n_sr_cooccurrences: int = 0,
    k: int = 3,
) -> list[dict]:
    """Rank artifacts using NPF scoring, returning top-K.

    Falls back gracefully: if any head fails, uses 0.0 for that head's score.
    Artifacts with rejection penalty noted in result dict via 'rejected_note'.
    """
    from tinm_sr import score_courant  # ensure module is importable

    if not artifacts:
        return []

    try:
        query_vec = _encode(query)
    except Exception:
        return []  # scorer unavailable

    artifacts_by_id = {a["id"]: a for a in artifacts if "id" in a}

    scored: list[tuple[float, dict]] = []
    for artifact in artifacts:
        if not artifact.get("embedding"):
            continue
        try:
            composite, heads = score(
                artifact=artifact,
                query_vec=query_vec,
                anchor_vec=anchor_vec,
                rejected_log=rejected_log,
                thread_id=thread_id,
                artifacts_by_id=artifacts_by_id,
                n_sr_cooccurrences=n_sr_cooccurrences,
            )
        except Exception:
            composite = 0.0
            heads = {}

        result = {**artifact, "score": composite, "match_kind": "npf", "npf_heads": heads}

        # Attach rejection note if U_repulsif fired (D1 / Feature 2)
        if heads.get("u_repulsif", 1.0) < 1.0:
            for entry in rejected_log:
                emb = entry.get("embedding_for_match")
                if emb and artifact.get("embedding"):
                    sim = _cosine(artifact["embedding"], emb)
                    if sim >= DEMOTION_THRESHOLD:
                        reason = entry.get("rejection_reason", "prior rejection")
                        result["rejected_note"] = f"[⚠️ previously rejected: {reason}]"
                        break

        scored.append((composite, result))

    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:k]]
