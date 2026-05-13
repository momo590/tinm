"""Best-effort auto-creation of a PCP v0 thread at session start.

Goal: zero friction for the user — they should never have to type
`/tinm init` for a new project. The SessionStart hook calls this script,
which:

  1. If `current_thread` is already set on this host, do nothing.
  2. Else, if the current working directory is inside a git repo, scan
     `$TINM_PCP_DIR/threads/*.json` for a thread whose
     `metadata.project_root` matches this repo's toplevel; if found,
     mark it current.
  3. Else, slug-ify `basename($PWD-or-git-toplevel)`, init a new thread,
     and mark it current.
  4. If we are not in a git repo, exit silently — auto-init only kicks
     in for "real" projects.

The script is silent on success and on every benign no-op. Errors go to
stderr but exit code is always 0 — the SessionStart hook must keep the
session usable even when auto-init fails.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tinm_paths import CURRENT_FILE, THREADS_DIR


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _git_toplevel() -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return r.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _slugify(name: str) -> str:
    """Normalise a directory basename into a valid PCP slug.

    PCP slugs are lowercase [a-z0-9-], 1-64 chars, starting on a
    letter/digit. Diacritics, spaces, punctuation collapse to `-`.
    """
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:64]


def _find_thread_for_project(project_root: str) -> str | None:
    """Return the thread_id whose metadata.project_root == project_root,
    or None. First match wins (deterministic via sorted glob).
    """
    if not THREADS_DIR.exists():
        return None
    for path in sorted(THREADS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("metadata", {}).get("project_root") == project_root:
            return path.stem
    return None


def _current_thread_set() -> bool:
    if not CURRENT_FILE.exists():
        return False
    return bool(CURRENT_FILE.read_text().strip())


def auto_init() -> str | None:
    """Run the auto-init policy. Returns the thread_id that ended up
    current, or None if nothing was done.
    """
    if _current_thread_set():
        return None

    project_root = _git_toplevel()
    if not project_root:
        return None

    # (a) Match by project_root → reuse existing thread
    existing = _find_thread_for_project(project_root)
    if existing:
        CURRENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CURRENT_FILE.write_text(existing + "\n")
        return existing

    # (b) Otherwise, derive a slug from basename
    basename = os.path.basename(project_root)
    slug = _slugify(basename)
    if not slug or not SLUG_RE.match(slug):
        return None

    # Refuse if the slug already exists but maps to a different
    # project_root — never silently hijack an unrelated thread.
    candidate_path = THREADS_DIR / f"{slug}.json"
    if candidate_path.exists():
        return None

    # Lazy-import to avoid loading sentence-transformers when not needed.
    from tinm_init import init_thread

    try:
        init_thread(slug, title=basename, project_root=project_root)
    except (ValueError, FileExistsError):
        return None
    return slug


def main() -> int:
    try:
        result = auto_init()
        if result:
            # The SessionStart hook collects stderr separately; we keep
            # the success path silent so it doesn't clutter the user's
            # terminal. A line on stderr lets `bash -x` debugging see it.
            print(f"tinm_auto_init: set current thread to {result!r}", file=sys.stderr)
    except Exception as e:
        # Never fail the session over auto-init issues.
        print(f"tinm_auto_init: ignored error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
