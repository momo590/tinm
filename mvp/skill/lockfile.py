"""
PCP lockfile module — coordinates concurrent writes to TINM_PCP_DIR.

Decision: plan-eng-review 1D2 (2026-05-14).
Targets persona: power-user with 3+ parallel Claude Code sessions.

Approach: file-based lock with exponential backoff retry. Atexit + signal
handler guarantees release even on crash. Stale-lock recovery via PID check.
"""

from __future__ import annotations

import atexit
import errno
import os
import random
import signal
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_FILENAME = ".tinm-pcp.lock"
DEFAULT_TIMEOUT_S = 10.0
INITIAL_BACKOFF_S = 0.05
MAX_BACKOFF_S = 1.0


class LockTimeoutError(RuntimeError):
    pass


class LockOrphanError(RuntimeError):
    pass


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        return int(lock_path.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _try_acquire(lock_path: Path, pid: int) -> bool:
    """Atomic create-if-not-exists with PID payload.

    Returns True iff this process now owns the lock.
    """
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileNotFoundError:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        return _try_acquire(lock_path, pid)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return False
        raise
    try:
        os.write(fd, str(pid).encode())
    finally:
        os.close(fd)
    return True


def _recover_orphan(lock_path: Path) -> bool:
    """If lock exists but the PID inside is dead, remove it.

    Returns True iff the orphan was reclaimed.
    """
    pid = _read_lock_pid(lock_path)
    if pid is None or _pid_alive(pid):
        return False
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass
    return True


@contextmanager
def pcp_lock(pcp_dir: str | Path, timeout_s: float = DEFAULT_TIMEOUT_S):
    """Context manager that holds the PCP write lock.

    Raises LockTimeoutError if the lock can't be acquired in timeout_s seconds.
    Always releases on exit (normal, exception, atexit, signal).
    """
    pcp_dir = Path(pcp_dir)
    lock_path = pcp_dir / LOCK_FILENAME
    pid = os.getpid()

    backoff = INITIAL_BACKOFF_S
    deadline = time.monotonic() + timeout_s
    acquired = False

    while time.monotonic() < deadline:
        if _try_acquire(lock_path, pid):
            acquired = True
            break
        if _recover_orphan(lock_path):
            continue
        sleep_for = min(backoff, MAX_BACKOFF_S) * (0.5 + random.random())
        time.sleep(sleep_for)
        backoff = min(backoff * 2, MAX_BACKOFF_S)

    if not acquired:
        raise LockTimeoutError(
            f"could not acquire {lock_path} within {timeout_s}s "
            f"(holder pid={_read_lock_pid(lock_path)})"
        )

    def _release():
        if _read_lock_pid(lock_path) == pid:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    atexit.register(_release)
    prev_handler = signal.signal(signal.SIGTERM, lambda *_: (_release(), os._exit(1)))

    try:
        yield lock_path
    finally:
        _release()
        try:
            signal.signal(signal.SIGTERM, prev_handler)
        except (TypeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Tests stub — to be filled in tinm/tests/test_lock_concurrent.py
# ---------------------------------------------------------------------------
# def test_acquires_when_free(tmp_path): ...
# def test_blocks_when_held(tmp_path): ...
# def test_recovers_orphan(tmp_path): ...
# def test_release_on_exception(tmp_path): ...
# def test_timeout_raises(tmp_path): ...
# def test_concurrent_two_processes(tmp_path): use multiprocessing.Process
