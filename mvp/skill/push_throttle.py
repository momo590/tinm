"""
Git push throttle — coalesces TINM_PCP_DIR pushes into ~30s windows.

Decision: plan-eng-review 4A2 (2026-05-14).
Targets: avoid GitHub API rate limits, keep git history readable, preserve
cross-machine latency <30s.

Approach: each UserPromptSubmit hook calls schedule_push(). The first call in
a quiet window writes a marker file and forks a background process that sleeps
30s then does a single `git add . && git commit && git push`. Subsequent calls
within the window are no-ops — the marker file's existence indicates a push
is already scheduled.

Important: this module assumes pcp_lock from lockfile.py is held during the
actual git operations. The throttle layer schedules; the lock layer serializes.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

MARKER_FILENAME = ".tinm-push-pending"
THROTTLE_WINDOW_S = 30.0
# Hostname in the commit message is a small but useful diagnostic — lets a
# reader of `git log` immediately tell whether a sync came from Mac or VPS
# (regression-fix turn 25 after we lost the legacy `from <hostname>` format
# during the inline-git → push_throttle migration).
COMMIT_MSG_TEMPLATE = "tinm: sync {ts} from {hostname} ({count} writes coalesced)"


def schedule_push(pcp_dir: str | Path, *, window_s: float = THROTTLE_WINDOW_S) -> bool:
    """Schedule a push; returns True iff a new background worker was forked.

    Multiple calls within window_s coalesce into a single push. The worker
    reads the marker file's mtime at sleep-end to confirm it should still push
    (it should), and increments the coalesced-count for the commit message.
    """
    pcp_dir = Path(pcp_dir)
    marker = pcp_dir / MARKER_FILENAME

    if marker.exists():
        _bump_count(marker)
        return False

    pcp_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text("1")

    pid = os.fork() if hasattr(os, "fork") else None
    if pid == 0:
        # Child: detach from the parent's stdio so callers that wait for our
        # stdout to close (e.g., subprocess.run from a hook) don't hang while
        # the child sleeps through the throttle window.
        try:
            os.setsid()
        except OSError:
            pass
        try:
            devnull = os.open(os.devnull, os.O_RDWR)
            os.dup2(devnull, 0)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            if devnull > 2:
                os.close(devnull)
        except OSError:
            pass
        try:
            _worker(pcp_dir, marker, window_s)
        finally:
            os._exit(0)
    elif pid is None:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__),
             "_worker", str(pcp_dir), str(window_s)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return True


def _bump_count(marker: Path) -> None:
    try:
        n = int(marker.read_text().strip())
    except (OSError, ValueError):
        n = 1
    marker.write_text(str(n + 1))


def _worker(pcp_dir: Path, marker: Path, window_s: float) -> None:
    """Background worker — sleeps the throttle window then pushes once."""
    time.sleep(window_s)
    try:
        count = int(marker.read_text().strip())
    except (OSError, ValueError):
        count = 1

    from lockfile import pcp_lock, LockTimeoutError

    try:
        with pcp_lock(pcp_dir, timeout_s=10.0):
            _run_git(pcp_dir, count)
    except LockTimeoutError:
        pass  # lock contention — skip this push; marker cleanup in finally
    finally:
        marker.unlink(missing_ok=True)


_TRACKED_PATHS = (
    "threads/",
    "artifacts/",
    "compaction_markers/",
    "gstack-projects/",
)

# Glob patterns expanded at push time. Used for per-host journal files —
# `journal-<hostname>.jsonl` (avoids Mac↔VPS race) and the migrated
# `journal-legacy-pre-*.jsonl`. Cannot use a literal name because git
# aborts on missing pathspec; cannot let the shell expand because we run
# `git add` via subprocess (no shell). So we expand in Python first.
_TRACKED_GLOBS = (
    "journal-*.jsonl",
)


def _run_git(pcp_dir: Path, count: int) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    hostname = socket.gethostname().replace(".", "-")
    msg = COMMIT_MSG_TEMPLATE.format(ts=ts, hostname=hostname, count=count)
    git_args = ["git", "-C", str(pcp_dir)]

    # Only add paths that exist — git aborts the whole add otherwise.
    existing = [p for p in _TRACKED_PATHS if (pcp_dir / p).exists()]
    for pattern in _TRACKED_GLOBS:
        existing.extend(p.name for p in pcp_dir.glob(pattern))
    if existing:
        subprocess.run([*git_args, "add", *existing],
                       check=False, capture_output=True)
    status = subprocess.run([*git_args, "diff", "--cached", "--quiet"], check=False)
    if status.returncode == 0:
        return

    subprocess.run([*git_args, "commit", "-m", msg], check=False, capture_output=True)
    subprocess.run([*git_args, "push", "--quiet"], check=False, capture_output=True)


# ---------------------------------------------------------------------------
# CLI entry point — used by user_prompt.sh hook and by the non-fork fallback
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "usage: push_throttle.py schedule <pcp_dir>\n"
            "       push_throttle.py _worker <pcp_dir> <window_s>\n"
            "       push_throttle.py force <pcp_dir>",
            file=sys.stderr,
        )
        sys.exit(2)

    cmd = sys.argv[1]
    if cmd == "schedule":
        pcp_dir = sys.argv[2]
        scheduled = schedule_push(pcp_dir)
        print("True" if scheduled else "False")
    elif cmd == "_worker":
        pcp_dir = Path(sys.argv[2])
        window_s = float(sys.argv[3])
        marker = pcp_dir / MARKER_FILENAME
        _worker(pcp_dir, marker, window_s)
    elif cmd == "force":
        pcp_dir = Path(sys.argv[2])
        marker = pcp_dir / MARKER_FILENAME
        count = 1
        if marker.exists():
            try:
                count = int(marker.read_text().strip())
            except (ValueError, OSError):
                count = 1
            try:
                marker.unlink()
            except OSError:
                pass
        _run_git(pcp_dir, count)
    else:
        print(f"unknown command: {cmd!r}", file=sys.stderr)
        sys.exit(2)
