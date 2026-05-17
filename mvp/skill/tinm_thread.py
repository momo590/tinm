"""Thread administration subcommands.

Currently exposes a single subcommand:

    tinm_thread.py bridge <slug>                          # bridge $PWD's fingerprint
    tinm_thread.py bridge <slug> --add-workspace <path>   # bridge a specific path
    tinm_thread.py bridge <slug> --list                   # list canonical + bridges

Why this module exists
----------------------

v0.2.3 tags each thread with a `workspace_fingerprint` (sha256 of the
workspace realpath). When the same project is checked out at two different
paths — e.g. ``~/code/foo`` on a Mac and ``/srv/foo`` on a Linux VPS —
the fingerprints differ and hooks refuse writes from the second path
(see `tinm_provenance.check_thread_writable`).

The user-facing escape hatch is an explicit ``bridge`` action: the user
opts in to allowing a second workspace fingerprint to coexist with the
canonical one. This module is the CLI surface for that action. The
underlying mutation lives in `tinm_provenance.bridge_workspace` (Lane A,
already done); this file is the thin user-facing wrapper.

The SessionStart hook (Lane C, separate file) is expected to invoke this
script in non-interactive mode when it detects a fingerprint mismatch.
That wiring is out of scope for this module.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make sibling modules importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tinm_paths import THREADS_DIR  # noqa: E402
from tinm_provenance import (  # noqa: E402
    bridge_workspace,
    compute_fingerprint,
)


def _thread_path(slug: str) -> Path:
    return THREADS_DIR / f"{slug}.json"


def _load_thread(slug: str) -> dict:
    path = _thread_path(slug)
    if not path.exists():
        raise FileNotFoundError(
            f"thread {slug!r} not found at {path}"
        )
    return json.loads(path.read_text())


def _resolve_fingerprint(explicit_path: str | None) -> tuple[str, str]:
    """Return (fingerprint, realpath_str) for the workspace to bridge.

    If `explicit_path` is given, fingerprint that. Otherwise fingerprint
    $PWD. Raises ValueError with a one-line user-facing message if the
    path cannot be resolved (deleted dir, dangling symlink).
    """
    import os

    raw = explicit_path if explicit_path is not None else os.getcwd()
    fp = compute_fingerprint(raw)
    if fp is None:
        # Mirror the path resolution `compute_fingerprint` does so the
        # error string the user sees matches what we actually tried.
        try:
            resolved = os.path.realpath(raw)
        except (OSError, ValueError):
            resolved = raw
        raise ValueError(
            f"could not compute workspace fingerprint for {resolved!r} "
            f"(path missing, dangling symlink, or not accessible)"
        )
    return fp, os.path.realpath(raw)


# ---- bridge subcommand ----------------------------------------------------


def cmd_bridge(args: argparse.Namespace) -> int:
    slug = args.slug

    try:
        thread = _load_thread(slug)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    meta = thread.get("metadata") or {}
    canonical = meta.get("workspace_fingerprint")
    bridges = list(meta.get("workspace_bridges") or [])

    if args.list:
        # Listing is read-only — no fingerprint computation needed.
        print(f"Thread {slug!r} workspace fingerprints:")
        if canonical:
            print(f"  canonical: {canonical}")
        else:
            print("  canonical: (none — legacy thread, pre-v0.2.3)")
        if bridges:
            print(f"  bridges ({len(bridges)}):")
            for fp in bridges:
                print(f"    {fp}")
        else:
            print("  bridges: (none)")
        return 0

    try:
        cwd_fp, resolved = _resolve_fingerprint(args.add_workspace)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Bail loudly if the thread has no canonical fingerprint at all. Bridging
    # a legacy thread is meaningless (its writes are already allowed from any
    # cwd, see check_thread_writable). Telling the user this directly avoids
    # the false sense that they "fixed" something.
    if not canonical:
        print(
            f"bridge declined: thread {slug!r} has no workspace fingerprint "
            f"(legacy, pre-v0.2.3). Writes are already allowed from any cwd; "
            f"no bridge is needed. Run the v0.2.3 migration to backfill.",
            file=sys.stderr,
        )
        return 1

    # Fingerprint identical to the canonical one — the most common
    # confusion mode for users who run `bridge` thinking something is
    # wrong when in fact their cwd already matches.
    if cwd_fp == canonical:
        print(
            f"bridge declined: {slug}'s canonical workspace is {cwd_fp} "
            f"(matches {resolved}); nothing to bridge."
        )
        return 0

    try:
        added = bridge_workspace(_thread_path(slug), cwd_fp)
    except (FileNotFoundError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if added:
        print(f"bridged {slug}: {cwd_fp}")
    else:
        # bridge_workspace already covers (a) cwd_fp == canonical
        # (handled above) and (b) cwd_fp already in bridges. Reaching
        # here means we hit case (b).
        print(f"already bridged {slug}: {cwd_fp}")
    return 0


# ---- argparse wiring ------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tinm thread",
        description=(
            "Thread administration. Currently: 'bridge' to allow writes "
            "from a second workspace (same project cloned to two paths)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    bridge = sub.add_parser(
        "bridge",
        help="Allow writes from a second workspace fingerprint.",
        description=(
            "Bridge an additional workspace fingerprint onto a thread. "
            "Use this when the same project is checked out at two paths "
            "(e.g. ~/code/foo on a laptop and /srv/foo on a server) and "
            "TINM refuses writes from the second path because the "
            "workspace fingerprint does not match the thread's canonical "
            "one. Bridging is explicit, idempotent, and per-thread."
        ),
    )
    bridge.add_argument(
        "slug",
        help="Thread slug to bridge (must exist in $TINM_PCP_DIR/threads/).",
    )
    grp = bridge.add_mutually_exclusive_group()
    grp.add_argument(
        "--add-workspace",
        metavar="PATH",
        default=None,
        help=(
            "Path whose fingerprint to bridge. Defaults to $PWD when "
            "omitted. The path must exist and be readable."
        ),
    )
    grp.add_argument(
        "--list",
        action="store_true",
        help=(
            "List the canonical workspace fingerprint and all bridges "
            "on the thread, then exit. Read-only."
        ),
    )
    bridge.set_defaults(func=cmd_bridge)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
