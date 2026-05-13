"""Best-effort auto-routing of the current PCP v0 thread at session start.

Goal: zero friction for the user — they should never have to type
`/tinm init`, AND switching projects (changing `cwd` between sessions)
must auto-switch the current thread. Without this, the previous
project's `current_thread` lingers and Claude loads the wrong context
on a new project. The SessionStart hook calls this script, which:

  1. Determine `project_root` = `git rev-parse --show-toplevel` for the
     current working directory. If we are not in a git repo, leave
     `current_thread` untouched (no thread for this session).
  2. Scan `$TINM_PCP_DIR/threads/*.json` for a thread whose
     `metadata.project_root` matches. If found, mark it current.
  3. Else, slug-ify `basename(project_root)`, init a new thread with
     that slug, and mark it current.

Note: this DOES overwrite a previously-set `current_thread` if the
current `cwd` resolves to a different project. The task-per-project
model is per the paper §3.2 ("State is reset on every new task") — a
new project is a new task is a new thread.

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


def _current_thread() -> str | None:
    if not CURRENT_FILE.exists():
        return None
    s = CURRENT_FILE.read_text().strip()
    return s or None


def _set_current(thread_id: str) -> None:
    CURRENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_FILE.write_text(thread_id + "\n")


def auto_init() -> str | None:
    """Run the auto-routing policy. Returns the thread_id that ended up
    current, or None if nothing was done.

    Re-runs on every SessionStart. The `current_thread` marker is
    overwritten whenever the cwd's project_root no longer matches the
    thread referenced by `current_thread`. This is intentional — a new
    project is a new task is a new thread (paper §3.2).
    """
    project_root = _git_toplevel()
    if not project_root:
        # Not in a git repo: nothing to auto-route to. We do NOT clear
        # current_thread here — the user may have set it explicitly via
        # `/tinm load` and we should not erase that just because they
        # cd'd somewhere non-git.
        return None

    # (a) Match by project_root → reuse the existing thread for this project
    existing = _find_thread_for_project(project_root)
    if existing:
        if _current_thread() != existing:
            _set_current(existing)
        return existing

    # (b) No thread for this project yet — derive a slug from basename,
    #     create a fresh thread, mark it current. This is the "new
    #     project, never seen before" path.
    basename = os.path.basename(project_root)
    slug = _slugify(basename)
    if not slug or not SLUG_RE.match(slug):
        return None

    candidate_path = THREADS_DIR / f"{slug}.json"
    if candidate_path.exists():
        # Slug collision with a thread that maps to a different
        # project_root — disambiguate with the parent directory's name.
        # Example: ~/work/clientA/api and ~/work/clientB/api both want
        # `api`; we fall back to `clientA-api` / `clientB-api`.
        parent = os.path.basename(os.path.dirname(project_root))
        slug = _slugify(f"{parent}-{basename}") or slug
        candidate_path = THREADS_DIR / f"{slug}.json"
        if candidate_path.exists():
            # Still colliding — bail. User will have to /tinm init manually.
            return None

    # Lazy-import to avoid loading sentence-transformers when not needed.
    from tinm_init import init_thread

    try:
        init_thread(slug, title=basename, project_root=project_root)
    except (ValueError, FileExistsError):
        return None
    _set_current(slug)
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
