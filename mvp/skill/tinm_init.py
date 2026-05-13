"""Initialise a new PCP v0 thread.

Creates two empty JSON files:
  ~/.tinm/threads/<thread_id>.json
  ~/.tinm/artifacts/<thread_id>.json

Refuses to overwrite an existing thread (the user must `rm` it or
choose a new slug).

Usage:
    python tinm_init.py <thread_id> --title "..." [--project-root PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


PCP_VERSION = "0.1"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
DEFAULT_ALPHA = 0.85

THREADS_DIR = Path.home() / ".tinm" / "threads"
ARTIFACTS_DIR = Path.home() / ".tinm" / "artifacts"
CURRENT_FILE = Path.home() / ".tinm" / "current_thread"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def init_thread(thread_id: str, title: str, project_root: str | None = None) -> Path:
    if not SLUG_RE.match(thread_id):
        raise ValueError(
            f"thread_id {thread_id!r} must be a lowercase slug "
            f"(letters, digits, hyphens), ≤64 chars, starting with a letter or digit"
        )

    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    thread_path = THREADS_DIR / f"{thread_id}.json"
    artifacts_path = ARTIFACTS_DIR / f"{thread_id}.json"

    if thread_path.exists():
        raise FileExistsError(
            f"Thread {thread_id!r} already exists at {thread_path}. "
            f"Delete it manually or pick another slug."
        )

    now = _utcnow()
    thread = {
        "pcp_version": PCP_VERSION,
        "thread_id": thread_id,
        "metadata": {
            "title": title,
            "created_at": now,
            "last_updated": now,
            "embedding_model": EMBED_MODEL,
            "embedding_dim": EMBED_DIM,
            "project_root": project_root,
            "client_history": [],
        },
        "anchor": {
            "vector": None,
            "alpha_used": DEFAULT_ALPHA,
            "update_count": 0,
            "last_updated_turn": 0,
            "engaged_so_far": False,
        },
        "trajectory": [],
    }
    artifacts = {
        "pcp_version": PCP_VERSION,
        "thread_id": thread_id,
        "artifacts": [],
    }

    thread_path.write_text(json.dumps(thread, indent=2) + "\n")
    artifacts_path.write_text(json.dumps(artifacts, indent=2) + "\n")
    # A newly initialised thread becomes the current thread — that is the
    # ergonomically obvious behaviour for `tinm init … && /tinm load`.
    CURRENT_FILE.write_text(thread_id + "\n")
    return thread_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise a new PCP v0 thread.")
    parser.add_argument("thread_id", help="Lowercase slug, hyphens allowed, ≤64 chars.")
    parser.add_argument("--title", required=True, help="Human-readable thread title.")
    parser.add_argument("--project-root", default=None, help="Optional absolute path.")
    args = parser.parse_args()

    try:
        path = init_thread(args.thread_id, args.title, args.project_root)
    except (ValueError, FileExistsError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Created thread {args.thread_id!r}")
    print(f"  trajectory: {path}")
    print(f"  artifacts:  {ARTIFACTS_DIR / (args.thread_id + '.json')}")


if __name__ == "__main__":
    main()
