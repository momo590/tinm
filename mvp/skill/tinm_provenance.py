"""Provenance: per-cwd thread resolution + write protection.

Single source of truth (DEC-5 of design-thread-isolation-2026-05-17) for:
  - compute_fingerprint(cwd)        hash a workspace path
  - check_thread_writable(t, fp)    origin + fingerprint gate
  - bridge_workspace(path, fp)      add a cross-host bridge
  - resolve_thread_for_cwd(cwd)     pick or create the thread for this cwd

Implements Layer 2 (per-cwd, no global pointer) + Layer 3 (provenance
fingerprint) of the thread-isolation design.

Schema additions to thread JSON `metadata`:
  origin              "seed" | "user" | "imported"
  workspace_fingerprint   "sha256:<32 hex>"   set on creation, immutable
  workspace_bridges       list[str]           cross-host bridges, default []
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from lockfile import pcp_lock
# Import the existing atomic writer from tinm_init rather than duplicate it.
# T5 will pull this helper into a shared module when auto-init is refactored
# through provenance; until then, this is the DRY route.
from tinm_init import _atomic_write_json
from tinm_paths import THREADS_DIR, TINM_PCP_DIR


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FP_PREFIX = "sha256:"
FP_HEX_LEN = 32  # 32 hex chars after the prefix = 128 bits of collision space
SHORT_SHA_LEN = 6  # non-git slug disambiguator length (DEC-3)


# ---- fingerprint ----------------------------------------------------------


def compute_fingerprint(cwd: str | os.PathLike | None = None) -> str | None:
    """Hash a workspace path into a stable fingerprint.

    Resolves symlinks via `realpath` (DEC-4). Returns None if the path
    cannot be resolved (e.g. dangling symlink, deleted target). Callers
    treat None as "no thread for this session" and exit cleanly — never
    fail the user's terminal over fingerprint computation.
    """
    raw = str(cwd) if cwd is not None else os.getcwd()
    try:
        resolved = os.path.realpath(raw)
    except (OSError, ValueError):
        return None
    # `realpath` happily normalises non-existent paths on POSIX; the
    # contract says we return None when the cwd is gone (deleted dir,
    # dangling symlink). Verify existence explicitly.
    if not resolved or not os.path.exists(resolved):
        return None
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:FP_HEX_LEN]
    return FP_PREFIX + digest


# ---- writable gate --------------------------------------------------------


def check_thread_writable(
    thread: dict, cwd_fp: str | None
) -> tuple[bool, str]:
    """Decide whether the current cwd can write to this thread.

    Returns (True, "") if writes are allowed; (False, reason) otherwise.
    The reason is a one-line string suitable for stderr / per-host log
    output by hooks.

    Rules (Layer 1 + Layer 3):
      - origin == "seed"          -> refused (fork required).
      - no workspace_fingerprint  -> allowed (legacy / pre-migration;
                                    the v0.2.3 migration backfills).
      - cwd_fp == stored fp        -> allowed.
      - cwd_fp in workspace_bridges -> allowed.
      - otherwise                  -> refused with mismatch reason.
    """
    meta = thread.get("metadata") or {}
    if meta.get("origin") == "seed":
        return False, "seed thread is read-only — fork to write"

    stored = meta.get("workspace_fingerprint")
    if not stored:
        # Legacy thread (pre-v0.2.3). The migration backfills these;
        # until it runs, do not penalise existing users.
        return True, ""

    if cwd_fp is None:
        return False, "could not compute workspace fingerprint for current cwd"

    if cwd_fp == stored:
        return True, ""

    bridges = meta.get("workspace_bridges") or []
    if cwd_fp in bridges:
        return True, ""

    return False, (
        f"workspace fingerprint mismatch (cwd={cwd_fp}, "
        f"thread={stored}); run `tinm thread bridge` to allow"
    )


# ---- bridge ---------------------------------------------------------------


def bridge_workspace(thread_path: str | os.PathLike, cwd_fp: str) -> bool:
    """Add `cwd_fp` to the thread's workspace_bridges list.

    Idempotent: returns False if `cwd_fp` is already the canonical
    workspace_fingerprint or already present in workspace_bridges.
    Returns True when a bridge is actually appended.

    Locks under pcp_lock so two parallel CC instances bridging at once
    cannot lose a write (T10 covers this).
    """
    if not cwd_fp:
        raise ValueError("cwd_fp required")

    thread_path = Path(thread_path)
    if not thread_path.exists():
        raise FileNotFoundError(thread_path)

    with pcp_lock(TINM_PCP_DIR):
        data = json.loads(thread_path.read_text())
        meta = data.setdefault("metadata", {})
        if meta.get("workspace_fingerprint") == cwd_fp:
            return False
        bridges = meta.setdefault("workspace_bridges", [])
        if cwd_fp in bridges:
            return False
        bridges.append(cwd_fp)
        _atomic_write_json(thread_path, data)
    return True


# ---- resolution -----------------------------------------------------------


def resolve_thread_for_cwd(cwd: str | os.PathLike | None = None) -> str | None:
    """Pick or create the user thread that matches this cwd.

    Strategy:
      1. If cwd is inside a git work tree, derive the slug from the
         toplevel basename. Existing-thread match is by
         `metadata.project_root` first; otherwise create a fresh thread,
         disambiguating slug collisions with the parent dir name.
      2. If cwd is not in a git repo, slug = `{basename}-{short-sha}`
         where short-sha is the first 6 hex chars of the realpath's
         sha256 (DEC-3 collision guard). Re-entry on the same path
         reuses the same slug.
      3. Created threads are tagged `origin: "user"` and the cwd's
         workspace_fingerprint; workspace_bridges starts empty.

    Returns the thread_id or None on any resolution failure (cwd
    missing, slug invalid, etc.). Never raises in the happy path.
    """
    raw = str(cwd) if cwd is not None else os.getcwd()
    try:
        resolved_cwd = os.path.realpath(raw)
    except (OSError, ValueError):
        return None
    if not resolved_cwd or not os.path.isdir(resolved_cwd):
        return None

    cwd_fp = compute_fingerprint(resolved_cwd)
    if cwd_fp is None:
        return None

    project_root = _git_toplevel(resolved_cwd)

    if project_root:
        existing = _find_thread_by_project_root(project_root)
        if existing:
            return existing

        basename = os.path.basename(project_root)
        slug = _slugify(basename)
        if not slug or not SLUG_RE.match(slug):
            return None

        if _thread_path(slug).exists():
            # Same slug, different project_root (caught above) — must be a
            # genuine collision. Disambiguate with parent dir name.
            parent = os.path.basename(os.path.dirname(project_root))
            alt = _slugify(f"{parent}-{basename}") if parent else ""
            if not alt or not SLUG_RE.match(alt) or _thread_path(alt).exists():
                return None
            slug = alt

        return _create_user_thread(
            slug,
            title=basename,
            project_root=project_root,
            workspace_fingerprint=cwd_fp,
        )

    basename = os.path.basename(resolved_cwd) or "root"
    short_sha = hashlib.sha256(resolved_cwd.encode("utf-8")).hexdigest()[
        :SHORT_SHA_LEN
    ]
    slug = _slugify(f"{basename}-{short_sha}")
    if not slug or not SLUG_RE.match(slug):
        return None

    if _thread_path(slug).exists():
        # Deterministic slug for this realpath — reuse on re-entry.
        return slug

    return _create_user_thread(
        slug,
        title=basename,
        project_root=None,
        workspace_fingerprint=cwd_fp,
    )


# ---- internals ------------------------------------------------------------


def _git_toplevel(cwd: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _slugify(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:64]


def _thread_path(thread_id: str) -> Path:
    return THREADS_DIR / f"{thread_id}.json"


def _find_thread_by_project_root(project_root: str) -> str | None:
    if not THREADS_DIR.exists():
        return None
    for path in sorted(THREADS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if (data.get("metadata") or {}).get("project_root") == project_root:
            return path.stem
    return None


def _create_user_thread(
    slug: str,
    *,
    title: str,
    project_root: str | None,
    workspace_fingerprint: str,
) -> str | None:
    """Create a thread via init_thread and tag origin + fingerprint.

    init_thread holds pcp_lock for the create; we re-acquire it to patch
    metadata. Two locked sections is fine — same process can re-enter
    once the first lock is released, and another process arriving
    between the two sections sees a valid (albeit untagged) thread.
    """
    # Lazy import: tinm_init pulls embedding-adjacent modules at top level
    # in some configurations; defer until we actually need to create.
    from tinm_init import init_thread

    try:
        path = init_thread(
            slug,
            title=title,
            project_root=project_root,
            write_current_pointer=False,
        )
    except (ValueError, FileExistsError):
        return None

    with pcp_lock(TINM_PCP_DIR):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return slug
        meta = data.setdefault("metadata", {})
        meta["origin"] = "user"
        meta["workspace_fingerprint"] = workspace_fingerprint
        meta.setdefault("workspace_bridges", [])
        _atomic_write_json(path, data)
    return slug


# ---- CLI entry (debug) ----------------------------------------------------


def main() -> int:
    """Debug helper: print the thread that would resolve for the cwd.

    Useful for verifying the per-cwd derivation from a shell:
        python -m tinm_provenance            # resolves $PWD
        python -m tinm_provenance /some/path # resolves that path
    """
    import argparse

    parser = argparse.ArgumentParser(description="Resolve thread for cwd.")
    parser.add_argument("cwd", nargs="?", default=None)
    args = parser.parse_args()
    result = resolve_thread_for_cwd(args.cwd)
    if result:
        print(result)
        return 0
    print("(no thread)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
