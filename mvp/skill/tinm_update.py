"""Append a user turn to a PCP v0 thread (HOT PATH — must stay < 100ms p99).

Mirrors the TINM-lite v2 mechanism from the paper (benchmark/agents/
tinm_lite.py) but operates on the persistent JSON store instead of in-
memory state. L1 activation threshold is ON by default — TINM engages
only when turn ≥ 3 OR the current query contains a pronoun/demonstrative.

── v0.3.0 hot-path refactor ────────────────────────────────────────────────
Pre-v0.3.0 this module loaded sentence-transformers + the all-MiniLM-L6-v2
model SYNCHRONOUSLY on every user prompt — 6-8s per call, blocking the
UserPromptSubmit hook. The bench (mvp/scripts/bench_hooks.sh) measured
p99 = 17.8s, 89× the 200ms gate.

v0.3.0 splits the work:

  Hot path (this module, target < 100ms p99):
    • Append the turn to the thread's trajectory (string ops).
    • Compute top_terms (cheap).
    • Set engaged_so_far based on turn number + anaphora regex.
    • Write the thread JSON back.
    • Spawn `_anchor_worker.py` via subprocess.Popen(start_new_session=True).
    • Read the pending hint from the PREVIOUS turn's worker output if it
      exists and matches the current thread_id; emit to stdout and delete.

  Async worker (_anchor_worker.py, runs 6-8s in the background):
    • Lazy-loads sentence-transformers.
    • Encodes the just-appended query.
    • EMA-updates the anchor vector under the PCP lock.
    • If the query was anaphoric, composes the trajectory hint and writes
      it to `<pcp>/threads/.pending_hint-<thread_id>.json` for the NEXT
      turn to consume.

One-turn lag is the intentional tradeoff: hint on turn N+1 reflects N.
For typical sessions (≥ 6 turns when L1 engages) this is invisible. The
hot path NEVER imports sentence_transformers — `test_tinm_update_async.py`
asserts this via module-import inspection.

Embedding model: sentence-transformers/all-MiniLM-L6-v2 (must match
metadata.embedding_model in the thread file).

Usage:
    python tinm_update.py <thread_id> --query "..." [--role user] [--client claude-code]
    python tinm_update.py <thread_id> --query "..." --emit-hint   # prints prior turn's hint, dispatches new worker
    python tinm_update.py <thread_id> --query "..." --legacy-sync # SYNCHRONOUS path (testing/debug only)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from lockfile import pcp_lock
from tinm_paths import THREADS_DIR, TINM_PCP_DIR
from tinm_telemetry import measure_latency


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

# Meta-turns ask to resume / continue the prior topic. They should not
# contribute their own tokens to top_terms (otherwise "continue" / "reprends"
# pollute the bag); instead they boost the most recent non-meta user turn,
# because that's where the actual current topic lives.
_META_TURN_RE = re.compile(
    r"\b("
    # English
    r"resume|resumes|resuming|"
    r"continue|continues|continuing|"
    r"where\s+were\s+we|"
    r"let'?s\s+continue|let'?s\s+resume|"
    r"pick\s+up\s+where|"
    r"keep\s+going|"
    # French
    r"reprends?|reprenons|reprennent|"
    r"recommenc\w+|"
    r"continuons|continuez|"
    r"on\s+reprend|on\s+continue|"
    r"résume|résumons|resume\s+stp"
    r")\b",
    re.IGNORECASE,
)


def _is_meta_turn(text: str) -> bool:
    """True if the turn is a 'resume/continue prior topic' meta-prompt.

    Meta-turns are typically short ("on reprend", "reprends en autopilot",
    "where were we"). We don't constrain by length because longer messages
    like "ok reprends sur le sujet TINM mais avec X précisions" still
    qualify — the leading verb is the signal.
    """
    return bool(_META_TURN_RE.search(text))


# ---------------------------------------------------------------------------
# Pure helpers (no embedding, cheap).
# ---------------------------------------------------------------------------
_TOP_TERMS_DECAY = 0.85
_META_BOOST = 5.0


def _top_query_terms(
    trajectory: list[dict],
    n: int = 5,
    *,
    decay: float = _TOP_TERMS_DECAY,
) -> list[str]:
    """Top N non-stopword terms across user queries, weighted by recency,
    length-normalized per turn, with meta-turn handling.

    Three corrections over a flat bag-of-words (see project_tinm_anchor_recency_bug):

    * **Recency:** each turn's contribution is multiplied by ``decay**(last-i)``,
      where ``last`` is the index of the most recent non-meta user turn. A
      topic from 4 turns ago therefore weighs ~52% of one from the latest turn.
    * **Length normalization:** every token in a turn contributes ``1/L`` where
      ``L`` is the total non-stopword token count in that turn — so each
      non-meta turn's contributions sum to exactly 1.0 before recency. A
      pasted 600-line brief stops mechanically swamping shorter prompts.
    * **Meta-turn handling:** a query like "reprends", "on continue", "where
      were we" contributes nothing itself, and instead boosts the most recent
      prior non-meta user turn by ``_META_BOOST``× — because that's where the
      user's current topic actually lives. Stacked meta-turns stack the boost.

    Returns the top-N terms by floating-point weight (ties broken by first
    occurrence, courtesy of ``Counter.most_common``).
    """
    from collections import Counter

    user_turns = [t for t in trajectory if t.get("role") == "user"]
    if not user_turns:
        return []

    boosts = [1.0] * len(user_turns)
    is_meta = [False] * len(user_turns)
    for i, t in enumerate(user_turns):
        if _is_meta_turn(t.get("text") or ""):
            is_meta[i] = True
            boosts[i] = 0.0
            for j in range(i - 1, -1, -1):
                if not is_meta[j]:
                    boosts[j] *= _META_BOOST
                    break

    # Anchor recency on the most recent NON-meta user turn so the meta query
    # at the end of the trajectory doesn't itself become the new "now". If
    # everything is meta (degenerate), fall back to the absolute last turn.
    non_meta_idx = [i for i in range(len(user_turns)) if not is_meta[i]]
    last_idx = non_meta_idx[-1] if non_meta_idx else len(user_turns) - 1

    counts: Counter = Counter()
    for i, t in enumerate(user_turns):
        if boosts[i] == 0.0:
            continue
        tokens = [
            w for w in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]*\b", (t.get("text") or "").lower())
            if w not in _STOPWORDS and len(w) > 2
        ]
        if not tokens:
            continue
        per_token = 1.0 / len(tokens)
        recency = decay ** max(0, last_idx - i)
        turn_weight = boosts[i] * recency * per_token
        for w in tokens:
            counts[w] += turn_weight

    return [w for w, _ in counts.most_common(n)]


def _format_trajectory_hint(thread: dict, current_turn: int) -> str:
    """Format the TINM trajectory hint for stdout injection.

    Returns empty string if L1 not engaged or no prior queries exist.
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


# ---------------------------------------------------------------------------
# Async dispatch — mirrors tinm_digest.launch_digest_async (the pattern of
# record that works in production today).
# ---------------------------------------------------------------------------
def _pending_hint_path(thread_id: str, pcp_dir: Path | None = None) -> Path:
    """Resolve `<pcp_dir>/threads/.pending_hint-<thread_id>.json`.

    Default pcp_dir is the configured TINM_PCP_DIR.
    """
    base = pcp_dir if pcp_dir is not None else TINM_PCP_DIR
    return Path(base) / "threads" / f".pending_hint-{thread_id}.json"


def read_pending_hint(
    thread_id: str,
    *,
    pcp_dir: Path | None = None,
    consume: bool = True,
) -> str:
    """Return the hint text from the worker's pending-hint file.

    Returns empty string if the file doesn't exist or its `thread_id`
    field doesn't match the current thread (defensive: the worker only
    ever writes its own thread's name, but we re-check to avoid cross-
    thread contamination if a user manually copies files around).

    When `consume=True` (default), deletes the file after reading so the
    same hint is never injected twice.
    """
    path = _pending_hint_path(thread_id, pcp_dir)
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # Best-effort cleanup of a corrupt file so we don't keep
        # bumping into it.
        if consume:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return ""
    if payload.get("thread_id") != thread_id:
        # Wrong thread — ignore, don't consume (defensive: another
        # process may eventually read it).
        return ""
    hint = str(payload.get("hint_md") or "")
    if consume:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return hint


def launch_anchor_worker_async(
    thread_id: str,
    target_turn: int,
    *,
    session_id: str = "",
    pcp_dir: Path | None = None,
) -> None:
    """Fire-and-forget Popen — same pattern as tinm_digest.launch_digest_async.

    Single-flight: at most one anchor worker per thread runs at a time.
    Each worker loads sentence-transformers (~6s, ~600MB RAM); without
    this guard, rapid successive prompts pile up workers and saturate
    the system, regressing the hot path back into the seconds. A
    skipped launch is functionally fine — the next turn's worker will
    incorporate any intermediate turns (the worker re-reads the thread
    file under pcp_lock and processes whatever trajectory exists at
    that point).

    The pidfile lives at $TINM_HOME/anchor-worker-<thread_id>.pid.
    The worker is responsible for clearing it on exit (atexit-style).

    A missing `_anchor_worker.py` (e.g., partial install) silently
    no-ops via the FileNotFoundError swallow — the user is not blocked,
    the next session_start hook will re-install.
    """
    script = Path(__file__).parent / "_anchor_worker.py"
    if not script.exists():
        return

    # Single-flight pidfile check (shared with the in-process dispatch path
    # via _anchor_worker.anchor_worker_alive — one source of truth).
    tinm_home = Path(os.environ.get("TINM_HOME", str(Path.home() / ".tinm")))
    pidfile = tinm_home / f"anchor-worker-{thread_id}.pid"
    try:
        from _anchor_worker import anchor_worker_alive
        if anchor_worker_alive(thread_id):
            return  # another worker is already processing this thread
    except ImportError:
        pass  # partial install — fall through and launch best-effort

    venv_py = Path.home() / ".tinm" / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable
    pcp = pcp_dir if pcp_dir is not None else TINM_PCP_DIR
    try:
        proc = subprocess.Popen(
            [
                py, str(script),
                "--thread-id", thread_id,
                "--pcp-dir", str(pcp),
                "--session-id", session_id,
                "--target-turn", str(target_turn),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Write the pidfile immediately so the next turn's launch sees it.
        # Worker clears it on exit; if the worker crashes before clearing,
        # the next launch detects the dead pid and overwrites.
        pid = getattr(proc, "pid", None)
        if pid is not None:
            try:
                tinm_home.mkdir(parents=True, exist_ok=True)
                pidfile.write_text(str(pid))
            except OSError:
                pass  # pidfile is best-effort
    except OSError:
        # Spawn failure (e.g., no fork available) is non-fatal: the
        # anchor stays unchanged this turn, the user is unblocked.
        pass


# ---------------------------------------------------------------------------
# Hot path: the function called by user_prompt.sh on EVERY user message.
# Must NOT import sentence_transformers. Tests assert this.
# ---------------------------------------------------------------------------
def update_thread(
    thread_id: str,
    *,
    query: str,
    role: str = "user",
    client: str | None = None,
    activation_min_turn: int = 3,
    emit_hint: bool = False,
    session_id: str = "",
    pcp_dir: Path | None = None,
    dispatch_worker: bool = True,
) -> dict:
    """Append turn, schedule async embedding, optionally return prior hint.

    The current-turn embedding is NOT computed here — it happens in the
    fire-and-forget worker. The hint returned (if `emit_hint=True`) is
    whatever the previous turn's worker dropped; if no hint is pending,
    the empty string is returned.
    """
    pcp = pcp_dir if pcp_dir is not None else TINM_PCP_DIR
    thread_path = Path(pcp) / "threads" / f"{thread_id}.json"
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

    anchor = thread["anchor"]
    # NOTE: anchor.vector / alpha_used / update_count are NOT touched here
    # — those are owned by the async worker (which re-reads under lock).
    # Hot path only touches the cheap fields the hint formatter needs.
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
    anchor["top_terms"] = _top_query_terms(thread["trajectory"])

    # Consume any hint left by the prior turn's worker, BEFORE we write —
    # so the same lock window covers append + hint-consume. The hint is
    # only returned (not emitted) here; main() decides whether to print.
    pending_hint = ""
    if emit_hint:
        pending_hint = read_pending_hint(thread_id, pcp_dir=pcp, consume=True)

    with pcp_lock(pcp):
        # Defensive: re-read under lock so we don't clobber an anchor
        # vector update that the worker for turn N-1 may have just
        # committed between our outside-the-lock read above and now.
        try:
            on_disk = json.loads(thread_path.read_text())
            on_disk_anchor = on_disk.get("anchor", {})
            # Preserve worker-owned fields (vector, alpha_used, update_count,
            # last_async_update_ts) from disk; everything else is our
            # in-memory version.
            for k in ("vector", "alpha_used", "update_count", "last_async_update_ts"):
                if k in on_disk_anchor:
                    anchor[k] = on_disk_anchor[k]
        except (json.JSONDecodeError, OSError):
            pass
        _atomic_write_json(thread_path, thread)

    # Dispatch the worker AFTER the lock is released — Popen is cheap
    # but the lock window should be as small as possible.
    if dispatch_worker:
        launch_anchor_worker_async(
            thread_id, next_turn,
            session_id=session_id, pcp_dir=pcp,
        )

    return {
        "turn": next_turn,
        "l1_engaged": is_l1_engaged,
        "engaged_so_far": anchor["engaged_so_far"],
        "anchor_update_count": int(anchor.get("update_count") or 0),
        "hint_text": pending_hint,
    }


# ---------------------------------------------------------------------------
# Legacy SYNCHRONOUS path — only invoked when --legacy-sync is passed
# (testing / debugging / CI parity checks). NEVER reachable on the hot
# path. sentence_transformers is imported lazily here, and the runtime
# test in test_tinm_update_async.py asserts that no top-level import of
# sentence_transformers exists in this module.
# ---------------------------------------------------------------------------
def _sync_encode_fallback(text: str) -> list[float]:
    """Synchronous embed — testing / debug only. NOT for hot path."""
    # Lazy imports — never at module load.
    from sentence_transformers import SentenceTransformer  # noqa: E402
    import numpy as np  # noqa: E402
    model = SentenceTransformer(EXPECTED_MODEL)
    vec = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
    return [float(x) for x in np.asarray(vec).ravel()]


def _update_thread_sync_legacy(
    thread_id: str,
    *,
    query: str,
    role: str = "user",
    client: str | None = None,
    activation_min_turn: int = 3,
    emit_hint: bool = False,
    adaptive: bool = True,
    alpha_min: float = 0.20,
    alpha_max: float = 0.95,
    fixed_alpha: float = 0.85,
) -> dict:
    """Pre-v0.3.0 behavior — embed in-process, write everything atomically.

    Kept for debugging parity tests + a `--legacy-sync` CLI escape hatch.
    Slow (6-8s). Do not call from a hook.
    """
    import numpy as np  # noqa: E402

    thread_path = THREADS_DIR / f"{thread_id}.json"
    if not thread_path.exists():
        raise FileNotFoundError(
            f"Thread {thread_id!r} not found at {thread_path}. "
            f"Run tinm_init.py first."
        )

    thread = json.loads(thread_path.read_text())
    if thread["pcp_version"].split(".", 1)[0] != "0":
        raise RuntimeError(f"Unsupported pcp_version {thread['pcp_version']!r}")
    if thread["metadata"]["embedding_model"] != EXPECTED_MODEL:
        raise RuntimeError("embedding model mismatch")

    next_turn = len(thread["trajectory"]) + 1
    is_l1_engaged = next_turn >= activation_min_turn or _contains_anaphora(query)

    query_vec = _sync_encode_fallback(query)
    if len(query_vec) != EXPECTED_DIM:
        raise RuntimeError(f"dim mismatch got {len(query_vec)}")

    anchor = thread["anchor"]
    anchor_vec = anchor.get("vector")
    if not adaptive or anchor_vec is None:
        alpha_t = fixed_alpha
    else:
        q = np.asarray(query_vec)
        a = np.asarray(anchor_vec)
        q_n = q / (np.linalg.norm(q) + 1e-9)
        a_n = a / (np.linalg.norm(a) + 1e-9)
        consistency = float(max(0.0, min(1.0, float(np.dot(q_n, a_n)))))
        alpha_t = alpha_min + (alpha_max - alpha_min) * consistency

    new_vec = (
        query_vec if anchor_vec is None
        else (alpha_t * np.asarray(anchor_vec) + (1.0 - alpha_t) * np.asarray(query_vec)).tolist()
    )
    anchor["vector"] = [float(x) for x in new_vec]
    anchor["alpha_used"] = float(alpha_t)
    anchor["update_count"] = int(anchor.get("update_count", 0)) + 1
    anchor["last_updated_turn"] = next_turn
    anchor["engaged_so_far"] = bool(anchor.get("engaged_so_far") or is_l1_engaged)

    turn_entry = {"turn": next_turn, "role": role, "text": query, "ts": _utcnow()}
    if client:
        turn_entry["client"] = client
    thread["trajectory"].append(turn_entry)
    thread["metadata"]["last_updated"] = _utcnow()
    anchor["top_terms"] = _top_query_terms(thread["trajectory"])

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
    parser.add_argument("--session-id", default="",
                        help="Optional session id passed to the async worker (for logging).")
    parser.add_argument("--no-adaptive", action="store_true",
                        help="(legacy-sync only) Use fixed α.")
    parser.add_argument("--emit-hint", action="store_true",
                        help="Print prior-turn pending hint (if any) to stdout.")
    parser.add_argument("--telemetry", default=None, metavar="HOOK_NAME",
                        help="If set, record latency_added_ms for the given hook (Lane H).")
    parser.add_argument("--legacy-sync", action="store_true",
                        help="Run the pre-v0.3.0 synchronous embed in-process. "
                             "Slow (6-8s). Testing/debugging only.")
    args = parser.parse_args()

    ctx = measure_latency(args.telemetry) if args.telemetry else nullcontext()
    with ctx:
        try:
            if args.legacy_sync:
                result = _update_thread_sync_legacy(
                    args.thread_id,
                    query=args.query,
                    role=args.role,
                    client=args.client,
                    emit_hint=args.emit_hint,
                    adaptive=not args.no_adaptive,
                )
            else:
                result = update_thread(
                    args.thread_id,
                    query=args.query,
                    role=args.role,
                    client=args.client,
                    emit_hint=args.emit_hint,
                    session_id=args.session_id,
                )
        except (FileNotFoundError, RuntimeError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)

        if args.emit_hint:
            # In hint mode: print the hint (or nothing if none pending).
            # Never print stats — they would be injected into Claude's context.
            if result.get("hint_text"):
                print(result["hint_text"])
        else:
            alpha = result.get("alpha_used")
            alpha_str = f"α={alpha:.3f}, " if isinstance(alpha, (int, float)) else ""
            print(
                f"turn {result['turn']}: {alpha_str}"
                f"L1_engaged={result['l1_engaged']}, "
                f"engaged_so_far={result['engaged_so_far']}, "
                f"anchor_updates={result['anchor_update_count']}"
            )


if __name__ == "__main__":
    main()
