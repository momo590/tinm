"""Load a PCP v0 thread and emit a markdown context block.

The output is meant to be piped/quoted back to Claude as session-start
context. It includes:
  - Title and timestamps
  - The last N trajectory turns (default 5)
  - All registered artifacts (name + ref + summary)
  - A reminder of the anchor state (turn count, engaged flag) — but not
    the raw vector, which is opaque.

Usage:
    python tinm_load.py <thread_id> [--n-trajectory 5]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tinm_paths import ARTIFACTS_DIR, CURRENT_FILE, THREADS_DIR

# Recap caps (spec §10 decision 3 + outside-voice §12)
MAX_NAMED_ARTIFACTS = 5
MAX_APPROVED_EXCHANGES = 2
MANUAL_SOURCES = {"manual", "legacy_manual"}
APPROVED_SOURCE = "approved_exchange"


def _check_version(pcp_version: str) -> None:
    major = pcp_version.split(".", 1)[0]
    if major != "0":
        raise RuntimeError(
            f"Unsupported pcp_version {pcp_version!r}; this loader speaks 0.x only."
        )


def _split_artifacts(arts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (named, approved) tuples capped to the recap budgets."""
    named, approved = [], []
    for a in arts:
        source = a.get("source", "legacy_manual")
        if source == APPROVED_SOURCE:
            approved.append(a)
        elif source in MANUAL_SOURCES or source not in {APPROVED_SOURCE}:
            named.append(a)
    # Most recent last → take last N for each. created_at sort if available.
    named.sort(key=lambda a: a.get("created_at", ""))
    approved.sort(key=lambda a: a.get("created_at", ""))
    return named[-MAX_NAMED_ARTIFACTS:], approved[-MAX_APPROVED_EXCHANGES:]


def _format_context(thread: dict, artifacts: dict, n_trajectory: int) -> str:
    meta = thread["metadata"]
    anchor = thread["anchor"]
    lines: list[str] = []

    lines.append(f"# TINM thread: {meta['title']}")
    lines.append(
        f"_id: `{thread['thread_id']}` · "
        f"created {meta['created_at']} · last updated {meta['last_updated']} · "
        f"{anchor['update_count']} anchor updates · "
        f"L1-engaged={anchor['engaged_so_far']}_"
    )
    if meta.get("project_root"):
        lines.append(f"_project root: `{meta['project_root']}`_")
    lines.append("")

    traj = thread.get("trajectory", [])
    if traj:
        recent = traj[-n_trajectory:]
        skipped = len(traj) - len(recent)
        header = f"## Recent trajectory ({len(recent)} of {len(traj)} turns"
        header += f", {skipped} earlier turns omitted)" if skipped else ")"
        lines.append(header)
        for t in recent:
            client = f" _via {t['client']}_" if t.get("client") else ""
            lines.append(f"- **t{t['turn']}** ({t['role']}){client}: {t['text']}")
    else:
        lines.append("## Recent trajectory")
        lines.append("_(empty — this is a fresh thread)_")
    lines.append("")

    arts = artifacts.get("artifacts", [])
    named, approved = _split_artifacts(arts)

    if named:
        total_named = sum(
            1 for a in arts
            if a.get("source", "legacy_manual") != APPROVED_SOURCE
        )
        header = f"## Named artifacts ({len(named)}"
        header += f" of {total_named}, capped)" if total_named > len(named) else ")"
        lines.append(header)
        for a in named:
            ref_part = f" → `{a['ref']}`" if a.get("ref") else ""
            aliases = a.get("aliases") or []
            alias_part = f" _(aka: {', '.join(aliases)})_" if aliases else ""
            lines.append(f"- **{a['name']}**{ref_part}{alias_part}")
            lines.append(f"  - {a['summary']}")
    elif not approved:
        lines.append("## Named artifacts")
        lines.append("_(none registered yet)_")
    lines.append("")

    if approved:
        total_approved = sum(
            1 for a in arts if a.get("source") == APPROVED_SOURCE
        )
        header = f"## Recent approved exchanges ({len(approved)}"
        header += (
            f" of {total_approved}, capped)" if total_approved > len(approved) else ")"
        )
        lines.append(header)
        for a in approved:
            trigger = (a.get("approval") or {}).get("trigger_phrase", "")
            turn = (a.get("approval") or {}).get("next_user_turn", "?")
            tag = f" _via t{turn} «{trigger}»_" if trigger else ""
            lines.append(f"- **{a['name']}**{tag}")
            lines.append(f"  - {a['summary'][:300]}")
        lines.append("")

    return "\n".join(lines)


def load_thread(thread_id: str, n_trajectory: int = 5) -> str:
    thread_path = THREADS_DIR / f"{thread_id}.json"
    if not thread_path.exists():
        raise FileNotFoundError(
            f"Thread {thread_id!r} not found at {thread_path}. "
            f"Run tinm_init.py first."
        )

    thread = json.loads(thread_path.read_text())
    _check_version(thread["pcp_version"])

    arts_path = ARTIFACTS_DIR / f"{thread_id}.json"
    artifacts = (
        json.loads(arts_path.read_text())
        if arts_path.exists()
        else {"artifacts": []}
    )

    # Loading a thread also marks it as current — so the SessionStart hook
    # and the MCP `current_thread` tool consistently agree on which thread
    # the user is currently working in.
    CURRENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_FILE.write_text(thread_id + "\n")

    return _format_context(thread, artifacts, n_trajectory)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load a PCP v0 thread.")
    parser.add_argument("thread_id")
    parser.add_argument("--n-trajectory", type=int, default=5)
    args = parser.parse_args()

    try:
        print(load_thread(args.thread_id, args.n_trajectory))
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
