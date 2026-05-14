"""Tests for the PCP lockfile module (mvp/skill/lockfile.py).

Validates the file-based lock that serializes concurrent PCP writes when the
persona has 3+ parallel Claude Code sessions (decision 1D2).

Test cases:
  1. Acquires immediately when nothing holds the lock.
  2. Two concurrent processes serialize (non-overlapping intervals).
  3. Orphan recovery — stale lock with dead PID is reclaimed.
  4. Timeout — raises LockTimeoutError when contended past deadline.
  5. Release on exception — lock file is gone after with-block raises.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

# Add the skill directory to sys.path so lockfile imports correctly.
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from lockfile import (  # noqa: E402
    LOCK_FILENAME,
    LockTimeoutError,
    pcp_lock,
)


# ---------------------------------------------------------------------------
# Test 1: acquires immediately when free
# ---------------------------------------------------------------------------

def test_acquires_when_free(tmp_path):
    """pcp_lock acquires immediately when nothing holds it."""
    start = time.monotonic()
    with pcp_lock(tmp_path, timeout_s=1.0):
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"acquire took {elapsed:.3f}s — should be instant"
        assert (tmp_path / LOCK_FILENAME).exists()
    # After exit, the lock file should be gone.
    assert not (tmp_path / LOCK_FILENAME).exists()


# ---------------------------------------------------------------------------
# Test 2: two concurrent processes serialize
# ---------------------------------------------------------------------------

def _holder_proc(pcp_dir: str, log_path: str, hold_ms: int, label: str) -> None:
    """Worker: acquire the lock, record before/after timestamps, sleep hold_ms."""
    from lockfile import pcp_lock as _lock  # re-import inside child
    with _lock(pcp_dir, timeout_s=5.0):
        t0 = time.monotonic()
        time.sleep(hold_ms / 1000.0)
        t1 = time.monotonic()
        with open(log_path, "a") as f:
            f.write(f"{label} {t0:.6f} {t1:.6f}\n")


def test_two_concurrent_processes_serialize(tmp_path):
    """Two processes contend for the lock; their hold intervals must not overlap."""
    log = tmp_path / "log.txt"
    hold_ms = 50

    p1 = multiprocessing.Process(
        target=_holder_proc, args=(str(tmp_path), str(log), hold_ms, "A"),
    )
    p2 = multiprocessing.Process(
        target=_holder_proc, args=(str(tmp_path), str(log), hold_ms, "B"),
    )
    p1.start()
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=10)
    assert p1.exitcode == 0 and p2.exitcode == 0

    entries = []
    for line in log.read_text().strip().splitlines():
        label, t0, t1 = line.split()
        entries.append((float(t0), float(t1), label))
    assert len(entries) == 2
    entries.sort()  # sort by t0
    (a_t0, a_t1, _), (b_t0, b_t1, _) = entries
    # The second process must start AFTER the first one's hold ended.
    assert b_t0 >= a_t1, (
        f"intervals overlap: A=({a_t0:.4f},{a_t1:.4f}) B=({b_t0:.4f},{b_t1:.4f})"
    )


# ---------------------------------------------------------------------------
# Test 3: orphan recovery
# ---------------------------------------------------------------------------

def _find_dead_pid() -> int:
    """Find a PID that does not correspond to a live process.

    Strategy: spawn a no-op python subprocess, capture its PID, wait until it
    exits. The kernel may recycle the PID later but for the test window it
    is dead.
    """
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = proc.pid
    proc.wait()
    # Give the kernel a moment to fully reap.
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        time.sleep(0.01)
    pytest.skip(f"could not find dead PID (still alive: {pid})")


def test_recovers_orphan(tmp_path):
    """A stale .tinm-pcp.lock with a dead PID is reclaimed."""
    dead_pid = _find_dead_pid()
    lock_path = tmp_path / LOCK_FILENAME
    lock_path.write_text(str(dead_pid))

    # Should acquire within a couple backoff cycles, well under timeout.
    start = time.monotonic()
    with pcp_lock(tmp_path, timeout_s=2.0):
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"orphan recovery took {elapsed:.3f}s"
        # The lock now contains OUR pid, not the dead one.
        assert lock_path.read_text().strip() == str(os.getpid())


# ---------------------------------------------------------------------------
# Test 4: timeout
# ---------------------------------------------------------------------------

def _hold_long(pcp_dir: str, hold_s: float, ready_path: str) -> None:
    """Worker: acquire lock, signal ready, hold for hold_s seconds."""
    from lockfile import pcp_lock as _lock
    with _lock(pcp_dir, timeout_s=2.0):
        Path(ready_path).write_text("ready")
        time.sleep(hold_s)


def test_timeout_raises(tmp_path):
    """A blocked acquire raises LockTimeoutError after timeout_s."""
    ready = tmp_path / "ready"
    holder = multiprocessing.Process(
        target=_hold_long, args=(str(tmp_path), 2.0, str(ready)),
    )
    holder.start()
    try:
        # Wait for holder to acquire.
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.02)
        else:
            pytest.fail("holder process never acquired the lock")

        start = time.monotonic()
        with pytest.raises(LockTimeoutError):
            with pcp_lock(tmp_path, timeout_s=0.3):
                pass
        elapsed = time.monotonic() - start
        # Should raise within ~0.3-0.6s — give some slack for jitter+backoff.
        assert elapsed < 1.0, f"timeout took {elapsed:.3f}s (expected ~0.3s)"
    finally:
        holder.join(timeout=5)


# ---------------------------------------------------------------------------
# Test 5: release on exception
# ---------------------------------------------------------------------------

def test_release_on_exception(tmp_path):
    """Exception inside the with-block still releases the lock."""
    lock_path = tmp_path / LOCK_FILENAME

    class _MarkerError(RuntimeError):
        pass

    with pytest.raises(_MarkerError):
        with pcp_lock(tmp_path, timeout_s=1.0):
            assert lock_path.exists()
            raise _MarkerError("boom")
    assert not lock_path.exists(), "lock was not released after exception"


# ---------------------------------------------------------------------------
# Bonus: reacquire after normal release
# ---------------------------------------------------------------------------

def test_reacquire_after_release(tmp_path):
    """Sequential acquire/release cycles work."""
    for _ in range(3):
        with pcp_lock(tmp_path, timeout_s=1.0):
            assert (tmp_path / LOCK_FILENAME).exists()
        assert not (tmp_path / LOCK_FILENAME).exists()
