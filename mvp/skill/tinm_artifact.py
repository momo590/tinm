"""Register and look up named artifacts in a PCP v0 thread (L4 mechanism).

Two subcommands:

    tinm_artifact.py <thread_id> add \\
        --id <slug> --name "..." --ref "..." --summary "..." \\
        [--alias "..." --alias "..."]

    tinm_artifact.py <thread_id> find "<query>" [--k 3]

`add` registers a new artifact (and computes its embedding so future
`find` calls can do semantic lookup).
`find` resolves an anaphoric reference: first by substring match on
name + aliases (cheap), then, if no hit, by cosine similarity on the
stored embeddings. Returns the top-K matches as a markdown block ready
to be quoted back to the LLM.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lockfile import pcp_lock
from tinm_paths import ARTIFACTS_DIR, REJECTED_DIR, THREADS_DIR, TINM_PCP_DIR
from tinm_scoring import EXPECTED_DIM, _encode, rank_artifacts
from tinm_telemetry import log_event

# NPF / SR imports — optional (cold installs without sentence-transformers still work)
try:
    from tinm_sr import n_cooccurrences as _sr_n_cooccurrences
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False

# Conv-index import is optional — a missing module must never break the
# existing artifact lookup path.
try:
    from tinm_conv_index import find as _conv_index_find
    _CONV_INDEX_AVAILABLE = True
except ImportError:
    _CONV_INDEX_AVAILABLE = False


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def _load_artifacts(thread_id: str) -> tuple[dict, Path]:
    path = ARTIFACTS_DIR / f"{thread_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Artifacts file for thread {thread_id!r} not found at {path}. "
            f"Run tinm_init.py first."
        )
    return json.loads(path.read_text()), path


def _current_turn(thread_id: str) -> int:
    """Best-effort: read the trajectory length from the thread file."""
    thread_path = THREADS_DIR / f"{thread_id}.json"
    if not thread_path.exists():
        return 0
    thread = json.loads(thread_path.read_text())
    return len(thread.get("trajectory", []))


def cmd_add(
    thread_id: str,
    *,
    artifact_id: str,
    name: str,
    ref: str,
    summary: str,
    aliases: list[str] | None = None,
    skip_embedding: bool = False,
    source: str = "manual",
    approval: dict | None = None,
) -> dict:
    artifacts, path = _load_artifacts(thread_id)

    if any(a["id"] == artifact_id for a in artifacts.get("artifacts", [])):
        raise ValueError(
            f"Artifact with id {artifact_id!r} already exists in thread "
            f"{thread_id!r}. Pick another id."
        )

    embedding = None if skip_embedding else _encode(f"{name}. {summary}")
    if embedding is not None and len(embedding) != EXPECTED_DIM:
        raise RuntimeError(
            f"Embedding dim mismatch: got {len(embedding)}, expected {EXPECTED_DIM}"
        )

    now = _utcnow()
    entry = {
        "id": artifact_id,
        "name": name,
        "aliases": list(aliases or []),
        "turn_first_mentioned": _current_turn(thread_id),
        "ref": ref,
        "summary": summary,
        "embedding": embedding,
        "created_at": now,
        "source": source,
        "last_retrieved_at": now,
    }
    if approval is not None:
        entry["approval"] = approval
    artifacts.setdefault("artifacts", []).append(entry)
    with pcp_lock(TINM_PCP_DIR):
        _atomic_write_json(path, artifacts)
    return entry


def _lazy_migrate_entry(entry: dict, now: str) -> bool:
    """Add v0.2.1 fields to a pre-v0.2.1 artifact entry. Returns True if mutated."""
    changed = False
    if "source" not in entry:
        entry["source"] = "legacy_manual"
        changed = True
    if "last_retrieved_at" not in entry:
        entry["last_retrieved_at"] = entry.get("created_at", now)
        changed = True
    return changed


def cmd_find(
    thread_id: str,
    query: str,
    k: int = 3,
    session_id: str | None = None,
) -> list[dict]:
    """Look up the top-K artifacts for *query*.

    When *session_id* is provided and the conv_index module is available,
    intra-session turns are retrieved and merged with the pcp/artifacts hits:

    1. Retrieve up to *k* artifact hits (pcp store).
    2. Retrieve up to *k* conv_index hits (current session turns).
    3. De-duplicate: if a conv_index hit has score > 0.95 AND its
       ``text_preview`` closely overlaps an artifact's ``summary``, the
       artifact version wins (it carries more metadata).
    4. Merge and re-rank by score descending.

    If *session_id* is None or conv_index is unavailable, behaviour is
    identical to the pre-F4 implementation.
    """
    artifacts, path = _load_artifacts(thread_id)
    now = _utcnow()
    entries = artifacts.get("artifacts", [])
    mutated = False
    for entry in entries:
        if _lazy_migrate_entry(entry, now):
            mutated = True

    # ── NPF context: anchor_vec, rejected_log, SR co-occurrence count ──────
    anchor_vec = None
    rejected_log: list[dict] = []
    n_sr = 0

    thread_path = THREADS_DIR / f"{thread_id}.json"
    if thread_path.exists():
        try:
            thread_data = json.loads(thread_path.read_text())
            anchor_vec = thread_data.get("anchor")
        except Exception:
            pass

    rejected_path = REJECTED_DIR / f"{thread_id}.jsonl"
    if rejected_path.exists():
        try:
            for line in rejected_path.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        rejected_log.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass

    if _SR_AVAILABLE:
        try:
            n_sr = _sr_n_cooccurrences(thread_id)
        except Exception:
            n_sr = 0

    hits = rank_artifacts(
        entries,
        query,
        k=k,
        anchor_vec=anchor_vec,
        rejected_log=rejected_log,
        thread_id=thread_id,
        n_sr_cooccurrences=n_sr,
    )
    hit_ids: set[str] = set()
    if hits:
        hit_ids = {h["id"] for h in hits if "id" in h}
        for entry in entries:
            if entry["id"] in hit_ids:
                entry["last_retrieved_at"] = now
                mutated = True
    if mutated:
        with pcp_lock(TINM_PCP_DIR):
            _atomic_write_json(path, artifacts)

    # ── SR update: propagate prev→curr retrieved artifact IDs ────────────────
    # The SR buffer tracks the artifact IDs retrieved at the PREVIOUS find()
    # call in this session so we can compute the TD update.
    # Storage: ~/.tinm/buffer/sr_prev-<session_id>.json (machine-local, ephemeral)
    if _SR_AVAILABLE and session_id and hit_ids:
        try:
            from tinm_sr import update as _sr_update
            sr_buf_path = Path(__file__).resolve().parent.parent.parent / ".tinm" / "buffer" / f"sr_prev-{session_id}.json"
            # Use TINM_HOME-relative path instead
            import os as _os
            _tinm_home = Path(_os.environ.get("TINM_HOME", Path.home() / ".tinm"))
            sr_buf_path = _tinm_home / "buffer" / f"sr_prev-{session_id}.json"
            sr_buf_path.parent.mkdir(parents=True, exist_ok=True)

            prev_ids: list[str] = []
            if sr_buf_path.is_file():
                try:
                    prev_ids = json.loads(sr_buf_path.read_text())
                except Exception:
                    prev_ids = []

            curr_ids = list(hit_ids)
            if prev_ids:
                _sr_update(thread_id, prev_ids, curr_ids)

            # Write current as next "prev"
            sr_buf_path.write_text(json.dumps(curr_ids))
        except Exception:
            pass  # SR failure never blocks artifact_find
    if hits:
        # Lane H telemetry — cross_session_hit + tokens_saved_estimated.
        # thread_id truncated to 64 chars by _validate_payload, but we also
        # trim to 32 here as a defense-in-depth nod (slugs are <=64 chars
        # by SLUG_RE, but artifact_find can be called from any consumer).
        log_event("cross_session_hit", {
            "thread_id": thread_id[:32],
            "k_results": len(hits),
            "top_score": float(hits[0].get("score", 0.0)),
        })
        # Heuristic: chars ÷ 4 ≈ tokens. Multiplier 5 from the +0.114-F1
        # demo (~6-9k tokens saved on a ~1.5k injected lookup). Calibrate
        # later from real telemetry once N≥3 users.
        injected_chars = sum(
            len(h.get("summary", "")) + len(h.get("name", ""))
            for h in hits
        )
        tokens_injected = max(1, injected_chars // 4)
        log_event("tokens_saved_estimated", {
            "tokens_injected": tokens_injected,
            "tokens_avoided_estimated": tokens_injected * 5,
            "source": "artifact_find",
        })

    # ── F4: merge intra-session conv_index results ───────────────────────────
    if session_id and _CONV_INDEX_AVAILABLE:
        try:
            conv_hits = _conv_index_find(session_id, query, k=k)
        except Exception:
            conv_hits = []

        if conv_hits:
            # Collect text previews from artifact hits for de-duplication.
            artifact_texts = {
                h.get("summary", "")[:200].lower()
                for h in hits
                if h.get("summary")
            }
            # Filter: drop conv_index result if it near-duplicates an artifact
            # (score > 0.95 AND its preview is a prefix of an artifact summary).
            filtered_conv: list[dict] = []
            for ch in conv_hits:
                preview_lower = ch["text_preview"][:100].lower()
                is_dup = (
                    ch["score"] > 0.95
                    and any(
                        preview_lower in art_text or art_text.startswith(preview_lower)
                        for art_text in artifact_texts
                    )
                )
                if not is_dup:
                    filtered_conv.append(ch)

            # Merge and re-rank by score descending
            merged = hits + filtered_conv
            merged.sort(key=lambda x: -float(x.get("score", 0.0)))
            hits = merged
    # ─────────────────────────────────────────────────────────────────────────

    return hits


def _format_hits_md(hits: list[dict]) -> str:
    if not hits:
        return "_(no matching artifacts)_"
    lines = []
    for h in hits:
        # Conv-index hits carry source="conv_index" and lack artifact fields.
        if h.get("source") == "conv_index":
            turn_id = h.get("turn_id", "?")
            role = h.get("role", "?")
            preview = h.get("text_preview", "")[:100]
            score = h.get("score", 0.0)
            lines.append(
                f"- [conv] Turn {turn_id} ({role}): {preview}...  "
                f"_[conv_index match, score={score:.3f}]_"
            )
        else:
            aliases = h.get("aliases") or []
            alias_part = f" _(aka: {', '.join(aliases)})_" if aliases else ""
            ref_part = f" → `{h['ref']}`" if h.get("ref") else ""
            match_kind = h.get("match_kind", "embedding")
            lines.append(
                f"- **{h['name']}**{alias_part}{ref_part}  "
                f"_[{match_kind} match, score={h['score']:.3f}]_"
            )
            if h.get("summary"):
                lines.append(f"  - {h['summary']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Register or look up artifacts in a PCP v0 thread.")
    parser.add_argument("thread_id")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="Register a new artifact.")
    add.add_argument("--id", required=True, help="Artifact slug (stable id).")
    add.add_argument("--name", required=True)
    add.add_argument("--ref", required=True, help="Path / URL / locator.")
    add.add_argument("--summary", required=True)
    add.add_argument("--alias", action="append", default=[], help="May be repeated.")
    add.add_argument("--skip-embedding", action="store_true",
                     help="Do not compute the embedding (substring lookup only).")

    find = sub.add_parser("find", help="Look up an artifact by query.")
    find.add_argument("query")
    find.add_argument("--k", type=int, default=3)

    args = parser.parse_args()

    log_event("user_explicit_action", {"action": f"artifact_{args.cmd}"})

    try:
        if args.cmd == "add":
            entry = cmd_add(
                args.thread_id,
                artifact_id=args.id,
                name=args.name,
                ref=args.ref,
                summary=args.summary,
                aliases=args.alias,
                skip_embedding=args.skip_embedding,
            )
            print(f"Added artifact {entry['id']!r}: {entry['name']}")
        elif args.cmd == "find":
            hits = cmd_find(args.thread_id, args.query, k=args.k)
            print(_format_hits_md(hits))
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
