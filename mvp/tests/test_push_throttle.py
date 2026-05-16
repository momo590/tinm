"""Tests for the push throttle module (mvp/skill/push_throttle.py).

Validates the 30s coalescing window that batches PCP git pushes (decision 4A2).

Test cases:
  1. First schedule_push returns True; marker file is created.
  2. Second schedule_push within the window returns False (coalesce).
  3. _bump_count math — marker contains str(N) after N calls.
  4. Worker coalesces — 3 schedule_push calls in a 2s window produce 1 commit.
  5. Marker cleanup — after worker runs, marker file is removed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Add the skill directory to sys.path so push_throttle imports correctly.
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from push_throttle import (  # noqa: E402
    MARKER_FILENAME,
    _bump_count,
    schedule_push,
)


# ---------------------------------------------------------------------------
# Test 1: first call schedules
# ---------------------------------------------------------------------------

def test_first_call_returns_true_and_creates_marker(tmp_path):
    """schedule_push returns True on first call and writes the marker."""
    # Use a long window to avoid the worker firing before we can assert.
    scheduled = schedule_push(tmp_path, window_s=60.0)
    try:
        assert scheduled is True
        marker = tmp_path / MARKER_FILENAME
        assert marker.exists()
        assert marker.read_text().strip() == "1"
    finally:
        # Clean up the marker so the forked worker exits early on next pass.
        marker = tmp_path / MARKER_FILENAME
        if marker.exists():
            marker.unlink()


# ---------------------------------------------------------------------------
# Test 2: second call coalesces
# ---------------------------------------------------------------------------

def test_second_call_coalesces(tmp_path):
    """A second schedule_push within the window returns False."""
    r1 = schedule_push(tmp_path, window_s=60.0)
    try:
        r2 = schedule_push(tmp_path, window_s=60.0)
        assert r1 is True
        assert r2 is False
    finally:
        marker = tmp_path / MARKER_FILENAME
        if marker.exists():
            marker.unlink()


# ---------------------------------------------------------------------------
# Test 3: _bump_count math
# ---------------------------------------------------------------------------

def test_bump_count_increments(tmp_path):
    """After N _bump_count calls (post-initial), marker contains str(N+1)."""
    marker = tmp_path / MARKER_FILENAME
    marker.write_text("1")
    _bump_count(marker)
    assert marker.read_text().strip() == "2"
    _bump_count(marker)
    _bump_count(marker)
    assert marker.read_text().strip() == "4"


def test_bump_count_recovers_from_corrupt_marker(tmp_path):
    """A corrupt marker file is treated as count=1 then bumped to 2."""
    marker = tmp_path / MARKER_FILENAME
    marker.write_text("not-a-number")
    _bump_count(marker)
    assert marker.read_text().strip() == "2"


# ---------------------------------------------------------------------------
# Test 4: worker coalesces multiple schedules into one commit
# ---------------------------------------------------------------------------

def _init_fake_repo(tmp_path: Path) -> None:
    """Set up a tmp_path as a git repo with an initial commit."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "--quiet"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "test"], check=True,
    )
    # Initial commit so HEAD exists.
    (tmp_path / "README").write_text("init\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init", "--quiet"], check=True,
    )


def _commit_count(repo: Path) -> int:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"],
        check=True, capture_output=True, text=True,
    )
    return len([ln for ln in out.stdout.splitlines() if ln.strip()])


def test_worker_coalesces_three_calls_into_one_commit(tmp_path):
    """3 schedule_push within a 2s window produce exactly 1 new commit."""
    _init_fake_repo(tmp_path)
    initial_commits = _commit_count(tmp_path)

    # Stage a real change so the worker has something to commit.
    threads = tmp_path / "threads"
    threads.mkdir()
    (threads / "fake.json").write_text('{"x":1}\n')

    # Schedule 3 times within a 2s window.
    r1 = schedule_push(tmp_path, window_s=2.0)
    r2 = schedule_push(tmp_path, window_s=2.0)
    r3 = schedule_push(tmp_path, window_s=2.0)
    assert (r1, r2, r3) == (True, False, False)

    # Wait for the worker to finish: window (2s) + git ops slack (2s).
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if not (tmp_path / MARKER_FILENAME).exists():
            break
        time.sleep(0.1)

    # Marker must be gone (test 5 piggybacks here).
    assert not (tmp_path / MARKER_FILENAME).exists(), \
        "marker still present — worker did not clean up"
    # Exactly ONE new commit (the worker won't push because no remote, but it
    # will commit locally).
    new_commits = _commit_count(tmp_path) - initial_commits
    assert new_commits == 1, f"expected 1 coalesced commit, got {new_commits}"


# ---------------------------------------------------------------------------
# Test 5: marker cleanup after worker runs (covered above, plus standalone)
# ---------------------------------------------------------------------------

def test_marker_cleaned_when_nothing_staged(tmp_path):
    """Worker still removes marker even when there is nothing to commit."""
    _init_fake_repo(tmp_path)
    initial_commits = _commit_count(tmp_path)

    # No staged change — worker should bail at the diff --cached --quiet check.
    schedule_push(tmp_path, window_s=1.5)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not (tmp_path / MARKER_FILENAME).exists():
            break
        time.sleep(0.1)

    assert not (tmp_path / MARKER_FILENAME).exists(), \
        "marker still present — worker did not clean up on no-op path"
    # No new commit because nothing was staged.
    assert _commit_count(tmp_path) == initial_commits


# ---------------------------------------------------------------------------
# CLI entry point smoke
# ---------------------------------------------------------------------------

def test_cli_schedule_subcommand(tmp_path):
    """The `schedule` CLI subcommand prints True/False and writes the marker."""
    _init_fake_repo(tmp_path)
    module_path = str(Path(__file__).parent.parent / "skill" / "push_throttle.py")

    # Patch in a fake pcp_dir argument; window defaults to THROTTLE_WINDOW_S
    # which is 30s — long enough that the worker won't fire before assertion.
    result = subprocess.run(
        [sys.executable, module_path, "schedule", str(tmp_path)],
        capture_output=True, text=True, timeout=10,
    )
    try:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"
        assert (tmp_path / MARKER_FILENAME).exists()
    finally:
        # Don't leave a worker waiting 30s — remove the marker so a tracked
        # cleanup won't complain. The fork-child will still be alive but it
        # only sleeps and then checks the marker, which will be gone.
        marker = tmp_path / MARKER_FILENAME
        if marker.exists():
            marker.unlink()


def test_cli_force_no_marker_commits_with_count_1(tmp_path):
    """`force` with no pending marker: commits staged changes, count=1."""
    _init_fake_repo(tmp_path)
    initial = _commit_count(tmp_path)
    (tmp_path / "threads").mkdir()
    (tmp_path / "threads" / "fake.json").write_text('{"x":1}\n')

    module_path = str(Path(__file__).parent.parent / "skill" / "push_throttle.py")
    result = subprocess.run(
        [sys.executable, module_path, "force", str(tmp_path)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / MARKER_FILENAME).exists()
    assert _commit_count(tmp_path) == initial + 1
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--oneline", "-1"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "1 writes coalesced" in log


def test_cli_force_with_marker_cancels_it_and_uses_stored_count(tmp_path):
    """`force` with pending marker (count=3): removes marker, uses count=3 in commit msg."""
    _init_fake_repo(tmp_path)
    initial = _commit_count(tmp_path)
    (tmp_path / "threads").mkdir()
    (tmp_path / "threads" / "fake.json").write_text('{"x":1}\n')
    (tmp_path / MARKER_FILENAME).write_text("3")

    module_path = str(Path(__file__).parent.parent / "skill" / "push_throttle.py")
    result = subprocess.run(
        [sys.executable, module_path, "force", str(tmp_path)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / MARKER_FILENAME).exists()
    assert _commit_count(tmp_path) == initial + 1
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--oneline", "-1"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "3 writes coalesced" in log
