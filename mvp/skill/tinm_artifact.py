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
from tinm_paths import ARTIFACTS_DIR, THREADS_DIR, TINM_PCP_DIR
from tinm_scoring import EXPECTED_DIM, _encode, rank_artifacts


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

    entry = {
        "id": artifact_id,
        "name": name,
        "aliases": list(aliases or []),
        "turn_first_mentioned": _current_turn(thread_id),
        "ref": ref,
        "summary": summary,
        "embedding": embedding,
        "created_at": _utcnow(),
    }
    artifacts.setdefault("artifacts", []).append(entry)
    with pcp_lock(TINM_PCP_DIR):
        _atomic_write_json(path, artifacts)
    return entry


def cmd_find(thread_id: str, query: str, k: int = 3) -> list[dict]:
    artifacts, _ = _load_artifacts(thread_id)
    return rank_artifacts(artifacts.get("artifacts", []), query, k=k)


def _format_hits_md(hits: list[dict]) -> str:
    if not hits:
        return "_(no matching artifacts)_"
    lines = []
    for h in hits:
        aliases = h.get("aliases") or []
        alias_part = f" _(aka: {', '.join(aliases)})_" if aliases else ""
        ref_part = f" → `{h['ref']}`" if h.get("ref") else ""
        lines.append(
            f"- **{h['name']}**{alias_part}{ref_part}  "
            f"_[{h['match_kind']} match, score={h['score']:.3f}]_"
        )
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
