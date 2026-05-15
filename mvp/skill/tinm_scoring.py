"""Artifact scoring — extracted from ``tinm_artifact.cmd_find`` for DRY reuse.

Decision: plan-eng-review 2A2 (2026-05-14). The same scoring helpers must be
callable from both ``tinm_artifact.cmd_find`` (existing) and
``digest_generator`` (Wave 2). Keeping a single source of truth avoids
behavioral drift between the artifact lookup path and the digest assembly
path.

Status: **v0.2 ships extraction-only**. This module preserves the existing
two-stage scoring (substring match on ``name + aliases``, then cosine
similarity on stored embeddings) bit-for-bit so the 14 existing pytest
cases pass unchanged. The multi-signal model sketched in the scaffold
(``W_TERM_MATCH`` / ``W_RECENCY`` / ``W_RECALL`` / ``W_PIN`` over
``top_terms`` + ``created_at`` + ``recall_count`` + ``pinned``) is
**future work**; TINM does not yet populate those fields.

Divergence from the scaffold spec (lives at
``<your-gstack-projects>/tinm/scaffolds/tinm_scoring.py`` on the dev
machine — vendor-neutral target shape that consumers reference):

* The scaffold's ``Artifact`` Protocol expects ``top_terms``, ``created_at``,
  ``recall_count``, ``pinned``. The real PCP v0 artifact dict (written by
  ``tinm_artifact.cmd_add``) carries ``name``, ``aliases``, ``embedding``,
  ``ref``, ``summary``, ``id``, ``turn_first_mentioned``, ``created_at``.
  Only ``name`` / ``aliases`` / ``embedding`` are read by today's scorer.
* The scaffold's ``score_artifact`` returns ``float``. Here we return
  ``(score, match_kind)`` so consumers can tell substring hits from
  embedding fallbacks without re-running the comparison.
* The weighted multi-signal blend is intentionally not implemented yet.

Consumers:

* ``tinm_artifact.cmd_find`` — delegates entirely to ``rank_artifacts``.
* (Wave 2) ``digest_generator`` — will call ``score_artifact`` per
  artifact when assembling a digest.
"""
from __future__ import annotations

import re
import sys


EXPECTED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EXPECTED_DIM = 384

_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            print(
                "error: sentence-transformers not installed. "
                "See mvp/README.md for install instructions.",
                file=sys.stderr,
            )
            raise SystemExit(1) from e
        _MODEL = SentenceTransformer(EXPECTED_MODEL)
    return _MODEL


def _encode(text: str) -> list[float]:
    import numpy as np
    vec = _get_model().encode(text, convert_to_numpy=True, show_progress_bar=False)
    return [float(x) for x in np.asarray(vec).ravel()]


def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _substring_score(query: str, candidates: list[str]) -> float:
    """1.0 if any candidate is a substring of query (or vice versa), else 0.0."""
    q = _normalise(query)
    if not q:
        return 0.0
    for c in candidates:
        cn = _normalise(c)
        if not cn:
            continue
        if cn in q or q in cn:
            return 1.0
    return 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    import numpy as np
    av = np.asarray(a, dtype=float)
    bv = np.asarray(b, dtype=float)
    denom = (float(np.linalg.norm(av)) * float(np.linalg.norm(bv))) + 1e-9
    return float(np.dot(av, bv) / denom)


def score_artifact(artifact: dict, query: str) -> tuple[float, str]:
    """Score a single artifact against a query.

    Returns ``(score, match_kind)`` where ``match_kind`` is one of:

    * ``"substring"`` — query matched ``name`` or any alias (score 1.0 or 0.0).
    * ``"embedding"`` — substring missed; score is cosine similarity against
      the stored embedding. Returns ``(0.0, "embedding")`` if the artifact
      has no embedding.

    Mirrors the two-stage logic in ``rank_artifacts`` but for a single
    artifact, so callers (e.g. ``digest_generator``) can rank using their
    own collection-level policy.
    """
    candidates = [artifact["name"]] + list(artifact.get("aliases") or [])
    sub = _substring_score(query, candidates)
    if sub > 0:
        return (sub, "substring")
    emb = artifact.get("embedding")
    if not emb:
        return (0.0, "embedding")
    return (_cosine(_encode(query), emb), "embedding")


def rank_artifacts(
    artifacts: list[dict],
    query: str,
    k: int = 3,
    # NPF kwargs — passed through when caller has the context to supply them
    anchor_vec: "list[float] | None" = None,
    rejected_log: "list[dict] | None" = None,
    thread_id: "str | None" = None,
    n_sr_cooccurrences: int = 0,
) -> list[dict]:
    """Return the top-K artifacts for ``query``, using two-stage scoring.

    Stage 1 (substring, cheap): score each artifact by substring match on
    ``name`` and any aliases. If any artifact scores > 0, return only
    substring hits, sorted by score descending, truncated to ``k``.

    Stage 2a (NPF): if ``anchor_vec``, ``rejected_log``, and ``thread_id`` are
    provided, use the 4-head Neural Potential Field scorer (tinm_npf.py).
    NPF is a superset of cosine: it adds recency, SR-derived current, and
    rejection-demotion on top of the magnetic (anchor-cosine) head.

    Stage 2b (embedding fallback): if NPF is unavailable or any required arg
    is missing, fall back to pure cosine similarity — identical to pre-v0.2.2
    behaviour.

    Each returned dict has the artifact's fields plus ``score: float`` and
    ``match_kind: "substring" | "embedding" | "npf"``.
    """
    if not artifacts:
        return []

    # Stage 1: substring
    substring_hits: list[tuple[float, dict]] = []
    for a in artifacts:
        candidates = [a["name"]] + list(a.get("aliases") or [])
        sub = _substring_score(query, candidates)
        if sub > 0:
            substring_hits.append((sub, a))
    if substring_hits:
        substring_hits.sort(key=lambda x: -x[0])
        return [{"score": s, "match_kind": "substring", **a} for s, a in substring_hits[:k]]

    # Stage 2a: NPF (requires anchor_vec + rejected_log + thread_id)
    if anchor_vec is not None and rejected_log is not None and thread_id is not None:
        try:
            from tinm_npf import rank_with_npf
            return rank_with_npf(
                artifacts=artifacts,
                query=query,
                anchor_vec=anchor_vec,
                rejected_log=rejected_log,
                thread_id=thread_id,
                n_sr_cooccurrences=n_sr_cooccurrences,
                k=k,
            )
        except Exception:
            pass  # fall through to cosine fallback

    # Stage 2b: cosine fallback (pre-v0.2.2 behaviour, always safe)
    embedded = [a for a in artifacts if a.get("embedding")]
    if not embedded:
        return []

    query_vec = _encode(query)
    ranked = sorted(
        ((_cosine(query_vec, a["embedding"]), a) for a in embedded),
        key=lambda x: -x[0],
    )
    return [{"score": s, "match_kind": "embedding", **a} for s, a in ranked[:k]]
