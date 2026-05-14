"""Append a user turn to a PCP v0 thread, EMA-update the anchor.

Mirrors the TINM-lite v2 mechanism from the paper (benchmark/agents/
tinm_lite.py) but operates on the persistent JSON store instead of in-
memory state. L1 activation threshold is ON by default — TINM engages
only when turn ≥ 3 OR the current query contains a pronoun/demonstrative.
Below threshold, the trajectory is still appended and the anchor still
updated (so it is ready when it engages), but `anchor.engaged_so_far`
flips only once L1 trips.

Embedding model: sentence-transformers/all-MiniLM-L6-v2 (must match
metadata.embedding_model in the thread file).

Usage:
    python tinm_update.py <thread_id> --query "..." [--role user] [--client claude-code]
    python tinm_update.py <thread_id> --query "..." --emit-hint   # prints trajectory hint to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lockfile import pcp_lock
from tinm_paths import THREADS_DIR, TINM_PCP_DIR


EXPECTED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EXPECTED_DIM = 384

_STOPWORDS = frozenset({
    # English
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "are", "was", "were", "be", "been", "have", "has",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "can", "that", "this", "these", "those", "it", "its", "i", "you", "we",
    "my", "your", "our", "what", "how", "why", "when", "where", "which",
    "not", "no", "up", "out", "if", "as", "by", "from", "so", "then",
    "about", "just", "get", "use", "now", "new", "also", "all", "any",
    # French
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "est",
    "en", "au", "aux", "ce", "que", "qui", "ne", "pas", "sur", "par",
    "pour", "dans", "avec", "il", "elle", "ils", "elles", "je", "tu",
    "nous", "vous", "mon", "ma", "mes", "ton", "ta", "ses", "son",
    "se", "on", "ou", "si", "car", "mais", "donc", "or", "ni", "car",
    "plus", "bien", "tout", "comme", "mais", "ça", "quoi", "ici",
    "sans", "très", "après", "avant", "entre", "sous", "vers", "chez",
    "dont", "lors", "même", "peu", "trop", "déjà", "encore", "toujours",
})

_ANAPHORIC_TOKEN_RE = re.compile(
    r"\b("
    # English
    r"it|its|itself|they|them|their|theirs|themselves|"
    r"this|that|these|those|"
    r"he|him|his|himself|she|her|hers|herself|"
    # French
    r"ça|cela|celui|celle|ceux|celles|lequel|laquelle|"
    r"lesquels|lesquelles|duquel|auquel|"
    r"ce|y|en"
    r")\b",
    re.IGNORECASE,
)

_MODEL = None  # lazy-loaded; loading takes 2-5s and we want a clean error first


def _top_query_terms(trajectory: list[dict], n: int = 5) -> list[str]:
    """Top N non-stopword terms by frequency across all user queries."""
    from collections import Counter
    counts: Counter = Counter()
    for turn in trajectory:
        if turn.get("role") != "user":
            continue
        for w in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]*\b", turn["text"].lower()):
            if w not in _STOPWORDS and len(w) > 2:
                counts[w] += 1
    return [w for w, _ in counts.most_common(n)]


def _format_trajectory_hint(thread: dict, current_turn: int) -> str:
    """Format the TINM trajectory hint for stdout injection.

    Returns empty string if L1 not engaged or no prior queries exist.
    The hint lists only user queries (never assistant responses) and includes
    a disambiguation instruction so Claude uses queries — not intermediate
    responses — to resolve anaphora.
    """
    if not thread["anchor"].get("engaged_so_far"):
        return ""
    prior_queries = [
        (t["turn"], t["text"])
        for t in thread.get("trajectory", [])
        if t.get("role") == "user" and t["turn"] != current_turn
    ]
    if not prior_queries:
        return ""
    top_terms = thread["anchor"].get("top_terms", [])
    anchor_str = " ".join(top_terms) if top_terms else "—"
    lines = [
        f"[TINM — Turn {current_turn} | Anchor: \"{anchor_str}\"]",
        (
            "To resolve pronouns (it, this, that, they) and understand the current "
            "working context, use the query list below. Prior assistant responses may "
            "contain intermediate answers that are not the current target — rely on "
            "queries for disambiguation."
        ),
        "Prior questions this session:",
    ]
    for turn_num, text in prior_queries:
        lines.append(f"  ({turn_num}) {text}")
    return "\n".join(lines)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _contains_anaphora(query: str) -> bool:
    return bool(_ANAPHORIC_TOKEN_RE.search(query))


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


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically: temp file in same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _compute_alpha(
    query_vec: list[float],
    anchor_vec: list[float] | None,
    *,
    adaptive: bool,
    alpha_min: float,
    alpha_max: float,
    fixed_alpha: float,
) -> float:
    if not adaptive or anchor_vec is None:
        return fixed_alpha
    import numpy as np
    q = np.asarray(query_vec)
    a = np.asarray(anchor_vec)
    q_n = q / (np.linalg.norm(q) + 1e-9)
    a_n = a / (np.linalg.norm(a) + 1e-9)
    consistency = float(max(0.0, min(1.0, float(np.dot(q_n, a_n)))))
    return alpha_min + (alpha_max - alpha_min) * consistency


def update_thread(
    thread_id: str,
    *,
    query: str,
    role: str = "user",
    client: str | None = None,
    adaptive: bool = True,
    alpha_min: float = 0.20,
    alpha_max: float = 0.95,
    fixed_alpha: float = 0.85,
    activation_min_turn: int = 3,
    emit_hint: bool = False,
) -> dict:
    thread_path = THREADS_DIR / f"{thread_id}.json"
    if not thread_path.exists():
        raise FileNotFoundError(
            f"Thread {thread_id!r} not found at {thread_path}. "
            f"Run tinm_init.py first."
        )

    thread = json.loads(thread_path.read_text())
    if thread["pcp_version"].split(".", 1)[0] != "0":
        raise RuntimeError(
            f"Unsupported pcp_version {thread['pcp_version']!r}"
        )
    if thread["metadata"]["embedding_model"] != EXPECTED_MODEL:
        raise RuntimeError(
            f"Embedding mismatch: thread uses "
            f"{thread['metadata']['embedding_model']!r}, this tool produces "
            f"{EXPECTED_MODEL!r}. Refusing to update the anchor with a "
            f"different-model embedding."
        )

    next_turn = len(thread["trajectory"]) + 1
    is_l1_engaged = (
        next_turn >= activation_min_turn
        or _contains_anaphora(query)
    )

    query_vec = _encode(query)
    if len(query_vec) != EXPECTED_DIM:
        raise RuntimeError(
            f"Embedding dim mismatch: got {len(query_vec)}, expected {EXPECTED_DIM}"
        )

    anchor = thread["anchor"]
    anchor_vec = anchor.get("vector")
    alpha_t = _compute_alpha(
        query_vec, anchor_vec,
        adaptive=adaptive, alpha_min=alpha_min, alpha_max=alpha_max,
        fixed_alpha=fixed_alpha,
    )
    if anchor_vec is None:
        new_vec = query_vec
    else:
        import numpy as np
        a = np.asarray(anchor_vec)
        q = np.asarray(query_vec)
        new_vec = (alpha_t * a + (1.0 - alpha_t) * q).tolist()

    anchor["vector"] = [float(x) for x in new_vec]
    anchor["alpha_used"] = float(alpha_t)
    anchor["update_count"] = int(anchor.get("update_count", 0)) + 1
    anchor["last_updated_turn"] = next_turn
    anchor["engaged_so_far"] = bool(anchor.get("engaged_so_far") or is_l1_engaged)

    turn_entry = {
        "turn": next_turn,
        "role": role,
        "text": query,
        "ts": _utcnow(),
    }
    if client:
        turn_entry["client"] = client
    thread["trajectory"].append(turn_entry)

    if client:
        ch = thread["metadata"].get("client_history") or []
        if client not in ch:
            ch.append(client)
        thread["metadata"]["client_history"] = ch

    thread["metadata"]["last_updated"] = _utcnow()

    # Compute top query terms from full trajectory (including current turn)
    anchor["top_terms"] = _top_query_terms(thread["trajectory"])

    # Format hint before write so we work from final state
    hint_text = _format_trajectory_hint(thread, next_turn) if emit_hint else ""

    with pcp_lock(TINM_PCP_DIR):
        _atomic_write_json(thread_path, thread)

    return {
        "turn": next_turn,
        "alpha_used": float(alpha_t),
        "l1_engaged": is_l1_engaged,
        "engaged_so_far": anchor["engaged_so_far"],
        "anchor_update_count": anchor["update_count"],
        "hint_text": hint_text,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Append a turn to a PCP v0 thread.")
    parser.add_argument("thread_id")
    parser.add_argument("--query", required=True)
    parser.add_argument("--role", default="user", choices=["user", "assistant"])
    parser.add_argument("--client", default=None)
    parser.add_argument("--no-adaptive", action="store_true",
                        help="Use fixed α instead of adaptive friction-based α.")
    parser.add_argument("--emit-hint", action="store_true",
                        help="Print trajectory hint to stdout for injection into Claude's context.")
    args = parser.parse_args()

    try:
        result = update_thread(
            args.thread_id,
            query=args.query,
            role=args.role,
            client=args.client,
            adaptive=not args.no_adaptive,
            emit_hint=args.emit_hint,
        )
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.emit_hint:
        # In hint mode: print the hint (or nothing if L1 not engaged).
        # Never print stats — they would be injected into Claude's context.
        if result.get("hint_text"):
            print(result["hint_text"])
    else:
        print(
            f"turn {result['turn']}: α={result['alpha_used']:.3f}, "
            f"L1_engaged={result['l1_engaged']}, "
            f"engaged_so_far={result['engaged_so_far']}, "
            f"anchor_updates={result['anchor_update_count']}"
        )


if __name__ == "__main__":
    main()
