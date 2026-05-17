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

from tinm_paths import ARTIFACTS_DIR, SEEDS_DIR, THREADS_DIR

# v0.2.3 L3: read-time legacy artifact renderer (gated by TINM_RENDER_LEGACY).
# Import is best-effort — if the module is missing we degrade to passthrough.
try:
    from tinm_renderer import humanize_if_enabled as _humanize
except Exception:  # pragma: no cover — defensive
    def _humanize(body: str) -> str:  # type: ignore[no-redef]
        return body

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
            lines.append(f"  - {_humanize(a['summary'])}")
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
            lines.append(f"  - {_humanize(a['summary'])[:300]}")
        lines.append("")

    return "\n".join(lines)


def _resolve_paths(thread_id: str) -> tuple[Path, Path] | None:
    """Resolve (thread_json, artifacts_json) for an id across namespaces.

    User threads in `pcp/threads/` win over seeds with the same id — the
    open-question recommendation in the v0.2.3 design doc. Returns None
    if the id is in neither namespace.
    """
    user_thread = THREADS_DIR / f"{thread_id}.json"
    if user_thread.exists():
        return user_thread, ARTIFACTS_DIR / f"{thread_id}.json"
    seed_thread = SEEDS_DIR / f"{thread_id}.json"
    if seed_thread.exists():
        return seed_thread, SEEDS_DIR / f"{thread_id}.artifacts.json"
    return None


def load_thread(thread_id: str, n_trajectory: int = 5) -> str:
    resolved = _resolve_paths(thread_id)
    if resolved is None:
        raise FileNotFoundError(
            f"Thread {thread_id!r} not found in {THREADS_DIR} or {SEEDS_DIR}. "
            f"Run tinm_init.py first."
        )
    thread_path, arts_path = resolved

    thread = json.loads(thread_path.read_text())
    _check_version(thread["pcp_version"])

    artifacts = (
        json.loads(arts_path.read_text())
        if arts_path.exists()
        else {"artifacts": []}
    )

    # NOTE: the previous version wrote `thread_id` to `~/.tinm/current_thread`
    # here as a side-effect of loading context. That was the smoking gun for
    # the v0.2.3 thread-pollution bug — a read-style operation mutated the
    # ambient global pointer, so any later prompt appended to whatever was
    # loaded last. The global pointer is being phased out; sessions get
    # their thread from the hook-frozen env (Lane C). Explicit user override
    # via `tinm load <id>` will write a per-cwd override marker once Lane C
    # defines the env contract.
    # TODO(lane-c): write a per-cwd override marker (not the global file)
    # so an explicit `tinm load <id>` survives across prompts within the
    # same cwd without polluting other workspaces.

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
