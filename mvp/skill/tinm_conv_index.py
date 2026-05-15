"""TINM conversation index — intra-session rolling index for anaphoric resolution.

Solves L4 limitation: "like we did at turn X" references that live in the current
session's conversation, not in pcp/artifacts/.

Storage: ~/.tinm/buffer/conv_index-<session_id>.jsonl
Each line: {"turn_id": int, "role": str, "text_preview": str (first 200 chars), "embedding": list[float]}

Max entries: 200 per session (cap at 200, discard oldest).
Embedding: sentence-transformers/all-MiniLM-L6-v2 (same as tinm_scoring).

Public API:
    add_turn(session_id, turn_id, role, text) -> None
    find(session_id, query, k=3) -> list[dict]   # returns [{turn_id, role, text_preview, score}]
    clear(session_id) -> None                     # called at session end
"""
from __future__ import annotations

import json
from pathlib import Path

from tinm_paths import BUFFER_DIR

MAX_ENTRIES = 200
INDEX_PREFIX = "conv_index-"

# Re-export scoring helpers at module level so tests (and callers) can
# monkeypatch tinm_conv_index._encode / tinm_conv_index._cosine without having
# to reach into tinm_scoring.  Falls back to stub no-ops if sentence-transformers
# is not installed; add_turn and find degrade gracefully in that case.
try:
    from tinm_scoring import _cosine, _encode
except ImportError:
    def _encode(text: str) -> list[float]:  # type: ignore[misc]
        raise ImportError("sentence-transformers not installed")

    def _cosine(a: list[float], b: list[float]) -> float:  # type: ignore[misc]
        raise ImportError("sentence-transformers not installed")


def _index_path(session_id: str) -> Path:
    return BUFFER_DIR / f"{INDEX_PREFIX}{session_id}.jsonl"


def add_turn(session_id: str, turn_id: int, role: str, text: str) -> None:
    """Add a turn to the conversation index.

    Skips empty/whitespace-only text. Enforces MAX_ENTRIES cap by discarding
    oldest entries. Uses atomic write (write to .tmp then rename) so a crash
    mid-write never leaves a corrupted index file.
    """
    if not text or not text.strip():
        return
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    path = _index_path(session_id)

    # Load existing entries
    entries = _load(path)

    # Compute embedding — gracefully degrade to empty list if
    # sentence-transformers is not installed or encoding fails.
    # Use the module-level _encode so tests can monkeypatch it.
    try:
        emb = _encode(text[:1000])  # cap input for speed
    except Exception:
        emb = []

    entry = {
        "turn_id": turn_id,
        "role": role,
        "text_preview": text[:200],
        "embedding": emb,
    }
    entries.append(entry)

    # Enforce max cap — keep the *most recent* MAX_ENTRIES entries
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]

    # Atomic write: write to .tmp then replace so readers never see a partial file
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def find(session_id: str, query: str, k: int = 3) -> list[dict]:
    """Find the most relevant turns for a query.

    Returns a list of up to k dicts, each with:
        turn_id, role, text_preview, score, source="conv_index"

    Sorted by cosine similarity descending. Returns [] if sentence-transformers
    is not installed, the index file does not exist, or there are no entries
    with embeddings.
    """
    path = _index_path(session_id)
    if not path.is_file():
        return []
    entries = _load(path)
    if not entries:
        return []

    # Use module-level _encode / _cosine so tests can monkeypatch them.
    try:
        query_vec = _encode(query)
    except Exception:
        return []

    scored: list[tuple[float, dict]] = []
    for e in entries:
        emb = e.get("embedding")
        if not emb:
            continue
        try:
            score = _cosine(query_vec, emb)
        except Exception:
            continue
        scored.append((score, e))

    scored.sort(key=lambda x: -x[0])
    results = []
    for score, e in scored[:k]:
        results.append({
            "turn_id": e["turn_id"],
            "role": e["role"],
            "text_preview": e["text_preview"],
            "score": score,
            "source": "conv_index",
        })
    return results


def clear(session_id: str) -> None:
    """Remove the session's conversation index file."""
    path = _index_path(session_id)
    path.unlink(missing_ok=True)


def _load(path: Path) -> list[dict]:
    """Load all entries from a JSONL file. Skips malformed lines silently."""
    if not path.is_file():
        return []
    entries: list[dict] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return entries
