"""TINM Successor Representation (SR) — multi-scale co-occurrence matrix.

Implements the SR from tinm_substrate.md §3:

    M_k(v, v') = E[sum_{t>=0} gamma_k^t * 1{V_t=v'} | V_0=v, policy]

Practical adaptation for TINM:
- "State" = set of artifact IDs retrieved at turn T
- "Transition" = from retrieved_set(T-1) to retrieved_set(T)
- TD update for each pair (v in prev_retrieved, v' in curr_retrieved):
    M_k[v][v'] += alpha_k * (1.0 + gamma_k * SR_value(v', curr) - M_k[v][v'])
  where SR_value(v', curr) = max M_k[v'][v''] for v'' in curr_retrieved (bootstrap)

K=2 scales:
- k=0: gamma=0.5 (local, fast update alpha=0.3) — captures immediate associations
- k=1: gamma=0.9 (global, slow update alpha=0.05) — captures long-range patterns

Storage: ~/.tinm/pcp/sr-<thread_id>-k0.json and sr-<thread_id>-k1.json
Format: {"<v_id>": {"<v_id'>": float, ...}, "__n_cooccurrences__": int}

The __n_cooccurrences__ counter tracks how many update events have been recorded.
Used by NPFScorer for adaptive gating (D3): if < 10, w_courant = 0.

Cold start: empty dict = identity proxy (each artifact has implicit self-score 1.0).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from tinm_paths import TINM_PCP_DIR
from tinm_scoring import _cosine

# SR hyperparameters
SR_SCALES = [
    {"gamma": 0.5, "alpha": 0.3},   # k=0: local
    {"gamma": 0.9, "alpha": 0.05},  # k=1: global
]
SR_WARMUP_THRESHOLD = int(os.environ.get("TINM_SR_WARMUP_THRESHOLD", "10"))
_N_COOCCURRENCES_KEY = "__n_cooccurrences__"


def _sr_path(thread_id: str, scale_idx: int) -> Path:
    return TINM_PCP_DIR / f"sr-{thread_id}-k{scale_idx}.json"


def _load_matrix(thread_id: str, scale_idx: int) -> dict:
    path = _sr_path(thread_id, scale_idx)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_matrix(thread_id: str, scale_idx: int, matrix: dict) -> None:
    path = _sr_path(thread_id, scale_idx)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(matrix) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _get_m(matrix: dict, v: str, v_prime: str) -> float:
    """Return M_k[v][v'] or 0.0 (identity proxy for missing entries)."""
    row = matrix.get(v)
    if row is None:
        # Identity: self-association = 1.0
        return 1.0 if v == v_prime else 0.0
    val = row.get(v_prime, 1.0 if v == v_prime else 0.0)
    return float(val)


def _set_m(matrix: dict, v: str, v_prime: str, value: float) -> None:
    if v not in matrix:
        matrix[v] = {}
    matrix[v][v_prime] = round(value, 6)


def n_cooccurrences(thread_id: str) -> int:
    """Return the total co-occurrence update count across all scales."""
    m0 = _load_matrix(thread_id, 0)
    return int(m0.get(_N_COOCCURRENCES_KEY, 0))


def update(
    thread_id: str,
    prev_retrieved: list[str],
    curr_retrieved: list[str],
) -> None:
    """Perform TD update for all scales when artifacts transition from prev → curr.

    Args:
        thread_id: the active thread
        prev_retrieved: artifact IDs retrieved at turn T-1
        curr_retrieved: artifact IDs retrieved at turn T
    """
    if not prev_retrieved or not curr_retrieved:
        return

    for k_idx, scale in enumerate(SR_SCALES):
        gamma = scale["gamma"]
        alpha = scale["alpha"]
        matrix = _load_matrix(thread_id, k_idx)

        for v in prev_retrieved:
            for v_prime in curr_retrieved:
                # Bootstrap estimate: max SR value from curr's own associations
                bootstrap = max(
                    _get_m(matrix, v_prime, v_pp) for v_pp in curr_retrieved
                ) if curr_retrieved else 0.0

                current = _get_m(matrix, v, v_prime)
                # TD update: M[v][v'] += alpha * (reward + gamma * bootstrap - M[v][v'])
                # reward = 1.0 (v' was directly retrieved after v)
                new_val = current + alpha * (1.0 + gamma * bootstrap - current)
                _set_m(matrix, v, v_prime, new_val)

        # Increment co-occurrence counter on first scale only
        if k_idx == 0:
            prev_count = int(matrix.get(_N_COOCCURRENCES_KEY, 0))
            matrix[_N_COOCCURRENCES_KEY] = prev_count + len(prev_retrieved) * len(curr_retrieved)

        _save_matrix(thread_id, k_idx, matrix)


def score_courant(
    v_id: str,
    goal_vec: list[float],
    artifacts_by_id: dict[str, dict],
    thread_id: str,
) -> float:
    """Compute U_courant(v, g) = normalized SR-weighted goal similarity.

    U_courant(v, g) = sum_v' [ M_k(v, v') * sim(embed(v'), g) ]
    normalized to [0, 1].

    Uses scale k=1 (global, gamma=0.9) for courant — captures long-range paths.

    Returns 0.0 on any error or cold matrix (graceful degradation).
    """
    try:
        matrix = _load_matrix(thread_id, 1)  # global scale
        if not matrix or not artifacts_by_id:
            return 0.0

        total = 0.0
        weight_sum = 0.0
        for v_prime, artifact in artifacts_by_id.items():
            if v_prime == _N_COOCCURRENCES_KEY:
                continue
            m_val = _get_m(matrix, v_id, v_prime)
            if m_val < 0.001:
                continue
            emb = artifact.get("embedding")
            if not emb:
                continue
            sim = _cosine(goal_vec, emb)
            total += m_val * sim
            weight_sum += m_val

        if weight_sum < 0.001:
            return 0.0

        # Normalize by weight_sum to get a [0,1]-ish score
        raw = total / weight_sum
        # Clamp to [0, 1]
        return max(0.0, min(1.0, raw))

    except Exception:
        return 0.0


def prune(thread_id: str, active_ids: set[str]) -> None:
    """Remove entries for artifact IDs that no longer exist (post-decay).

    Called by tinm_decay.py when artifacts are pruned.
    """
    for k_idx in range(len(SR_SCALES)):
        matrix = _load_matrix(thread_id, k_idx)
        if not matrix:
            continue

        changed = False
        # Remove rows for dead artifacts
        dead_rows = [v for v in matrix if v != _N_COOCCURRENCES_KEY and v not in active_ids]
        for v in dead_rows:
            del matrix[v]
            changed = True

        # Remove dead columns from surviving rows
        for v in list(matrix):
            if v == _N_COOCCURRENCES_KEY:
                continue
            row = matrix[v]
            dead_cols = [v_p for v_p in row if v_p not in active_ids]
            for v_p in dead_cols:
                del row[v_p]
                changed = True

        if changed:
            _save_matrix(thread_id, k_idx, matrix)
