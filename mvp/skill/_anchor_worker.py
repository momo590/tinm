#!/usr/bin/env python3
"""TINM async anchor worker — heavy embedding work moved off the hot path.

Invoked as a detached fire-and-forget subprocess by `tinm_update.py` after
the user prompt's turn has already been appended to the thread's trajectory.
The worker:

  1. Lazy-imports sentence-transformers (the 6-8s cold-import that was
     blocking UserPromptSubmit on every turn before v0.3.0).
  2. Encodes the user query with all-MiniLM-L6-v2 (must match the
     thread's metadata.embedding_model).
  3. Updates the EMA anchor vector + alpha_used + update_count, then
     writes the thread JSON back under the PCP lock.
  4. If the query is anaphoric (pronouns/demonstratives — same regex as
     the hot path uses for L1 detection), composes a trajectory hint
     and drops it as a "pending hint" file at:
         <pcp_dir>/threads/.pending_hint-<thread_id>.json
     Hot path reads this on the NEXT turn (one-turn lag, deletes after
     consumption). Per task design — see /root/TNIM/mvp/skill/SKILL.md.

The worker is fire-and-forget: any error exits 0 silently (logged to
$TINM_HOME/anchor-worker-<host>.log), so a model-load failure can never
block the user's prompt.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EXPECTED_DIM = 384


# ---------------------------------------------------------------------------
# Time helper — duplicated here (and in tinm_update.py) so the worker is a
# zero-dependency standalone script. Both formats stay in lockstep:
#   2026-05-17T20:30:45Z
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _log_path() -> Path:
    home = Path(os.environ.get("TINM_HOME") or (Path.home() / ".tinm"))
    home.mkdir(parents=True, exist_ok=True)
    host = socket.gethostname().replace(".", "-")
    return home / f"anchor-worker-{host}.log"


def _log_error(prefix: str, exc: BaseException | None = None) -> None:
    """Best-effort logging; never raises."""
    try:
        msg = f"[{_utcnow()}] {prefix}"
        if exc is not None:
            msg += f": {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        with open(_log_path(), "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Atomic JSON write (mirrors tinm_update._atomic_write_json).
# ---------------------------------------------------------------------------
def _atomic_write_json(path: Path, payload: dict) -> None:
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


# ---------------------------------------------------------------------------
# EMA / encode helpers — kept self-contained so worker is independent.
# ---------------------------------------------------------------------------
_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(EXPECTED_MODEL)
    return _MODEL


def _encode(text: str) -> list[float]:
    import numpy as np
    vec = _get_model().encode(text, convert_to_numpy=True, show_progress_bar=False)
    return [float(x) for x in np.asarray(vec).ravel()]


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


# ---------------------------------------------------------------------------
# Hint composition — duplicates the format from tinm_update._format_trajectory_hint
# but runs at worker time (one turn LATER than the hot-path L1 path).
# The hot path reads this hint on the next turn.
# ---------------------------------------------------------------------------
def _format_trajectory_hint(thread: dict, current_turn: int) -> str:
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


def pending_hint_path(pcp_dir: Path, thread_id: str) -> Path:
    """Where the worker drops its hint for the next turn to consume.

    Lives under `<pcp_dir>/threads/.pending_hint-<thread_id>.json`. The
    dot-prefix keeps it out of normal thread enumeration. One file per
    thread is plenty — only the latest hint matters; an older one is
    overwritten if the worker fires twice before the hot path consumes.
    """
    return pcp_dir / "threads" / f".pending_hint-{thread_id}.json"


# ---------------------------------------------------------------------------
# Main worker entry point.
# ---------------------------------------------------------------------------
def run_worker(
    thread_id: str,
    pcp_dir: Path,
    session_id: str,
    target_turn: int,
    *,
    adaptive: bool = True,
    alpha_min: float = 0.20,
    alpha_max: float = 0.95,
    fixed_alpha: float = 0.85,
) -> int:
    """Embed the query, update the anchor, optionally write a hint.

    `target_turn` is the turn number that was just appended by the hot
    path. The worker reads that turn's text from the thread, embeds it,
    runs EMA against the prior anchor vector, then writes the updated
    anchor back. If the appended turn was anaphoric, the worker also
    composes a hint and writes it to the pending-hint file.
    """
    thread_path = pcp_dir / "threads" / f"{thread_id}.json"
    if not thread_path.exists():
        _log_error(f"worker: thread file missing thread={thread_id}")
        return 0

    try:
        thread = json.loads(thread_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        _log_error("worker: thread file unreadable", e)
        return 0

    if thread.get("pcp_version", "").split(".", 1)[0] != "0":
        _log_error(f"worker: unsupported pcp_version {thread.get('pcp_version')!r}")
        return 0
    if thread["metadata"].get("embedding_model") != EXPECTED_MODEL:
        _log_error(
            f"worker: embedding model mismatch "
            f"(thread={thread['metadata'].get('embedding_model')!r}, "
            f"worker={EXPECTED_MODEL!r})"
        )
        return 0

    # Locate the target turn. Hot path appended turn N — find it.
    target_entry = None
    for entry in thread.get("trajectory", []):
        if entry.get("turn") == target_turn:
            target_entry = entry
            break
    if target_entry is None:
        _log_error(f"worker: target turn {target_turn} not found in thread")
        return 0

    query_text = target_entry.get("text") or ""
    if not query_text:
        _log_error(f"worker: empty query text at turn {target_turn}")
        return 0

    # Heavy lift: load model, encode.
    try:
        query_vec = _encode(query_text)
    except Exception as e:
        _log_error("worker: encode failed", e)
        return 0
    if len(query_vec) != EXPECTED_DIM:
        _log_error(
            f"worker: dim mismatch (got {len(query_vec)}, expected {EXPECTED_DIM})"
        )
        return 0

    # Re-read the thread under the lock — another concurrent process may
    # have appended turns between the hot path's append and our worker
    # starting. We update only the anchor, never the trajectory.
    try:
        # Lock import is deferred to avoid touching tinm_paths at module
        # import time (worker is meant to be standalone).
        sys.path.insert(0, str(Path(__file__).parent))
        from lockfile import pcp_lock
    except ImportError as e:
        _log_error("worker: cannot import lockfile", e)
        return 0

    try:
        with pcp_lock(pcp_dir):
            thread = json.loads(thread_path.read_text())
            anchor = thread["anchor"]
            anchor_vec = anchor.get("vector")
            alpha_t = _compute_alpha(
                query_vec, anchor_vec,
                adaptive=adaptive, alpha_min=alpha_min,
                alpha_max=alpha_max, fixed_alpha=fixed_alpha,
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
            anchor["last_updated_turn"] = max(
                int(anchor.get("last_updated_turn") or 0), int(target_turn)
            )
            anchor["last_async_update_ts"] = _utcnow()
            _atomic_write_json(thread_path, thread)
    except Exception as e:
        _log_error("worker: anchor update failed", e)
        return 0

    # Compose hint if anaphora present on this query. The hint is for
    # CONSUMPTION on the NEXT user turn — the hot path reads it then.
    try:
        # Re-use the regex from tinm_update so semantics stay in lock-
        # step. Import here so a sys.path mishap doesn't break the
        # heavy lifting above.
        from tinm_update import _contains_anaphora
        if _contains_anaphora(query_text):
            hint_md = _format_trajectory_hint(thread, target_turn)
            if hint_md:
                hint_path = pending_hint_path(pcp_dir, thread_id)
                payload = {
                    "thread_id": thread_id,
                    "session_id": session_id,
                    "turn_marker": int(target_turn),
                    "hint_md": hint_md,
                    "ts": _utcnow(),
                }
                _atomic_write_json(hint_path, payload)
    except Exception as e:
        # Hint failure is harmless — never blocks the worker exit.
        _log_error("worker: hint compose failed", e)

    # Backfill any deferred conv_index entries for this session — the
    # hot path's tinm_conv_add.py writes entries with empty embeddings
    # so it can return in < 100ms; we encode them here while the model
    # is already loaded.
    if session_id:
        try:
            from tinm_conv_index import backfill_embeddings
            backfill_embeddings(session_id)
        except Exception as e:
            _log_error("worker: conv_index backfill failed", e)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Async embedding worker for TINM anchor updates."
    )
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--pcp-dir", required=True,
                        help="Path to the PCP store root (contains threads/).")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--target-turn", type=int, required=True,
                        help="Turn number the hot path just appended.")
    parser.add_argument("--no-adaptive", action="store_true")
    args = parser.parse_args(argv)

    try:
        return run_worker(
            args.thread_id,
            Path(args.pcp_dir).expanduser(),
            args.session_id,
            args.target_turn,
            adaptive=not args.no_adaptive,
        )
    except Exception as e:
        _log_error("worker: fatal", e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
