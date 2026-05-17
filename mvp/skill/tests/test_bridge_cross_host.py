"""Cross-host bridge integration tests (T10).

Simulates the cross-host scenario: a thread is created with workspace
fingerprint A (e.g., on a laptop), then opened from fingerprint B
(e.g., a server with the same project at a different path). Writes
from B are refused until the user explicitly bridges. After bridging
B, writes succeed and re-bridging is a no-op.

These tests exercise the real `tinm_thread.py bridge` CLI (T7) end to
end, not just the Python API — that's the surface the SessionStart
hook will eventually invoke.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest


VENV_PY = "/root/.tinm/.venv/bin/python"
SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
BRIDGE_SCRIPT = SKILL_DIR / "tinm_thread.py"


@pytest.fixture
def tinm_tmp(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="tinm-xhost-test-")
    monkeypatch.setenv("TINM_HOME", tmp)
    monkeypatch.setenv("TINM_PCP_DIR", str(pathlib.Path(tmp) / "pcp"))
    for mod in ("tinm_paths", "tinm_init", "tinm_provenance"):
        sys.modules.pop(mod, None)
    yield pathlib.Path(tmp)


def _run_bridge(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    """Invoke tinm_thread.py with the test's TINM_HOME inherited."""
    env = os.environ.copy()
    return subprocess.run(
        [VENV_PY, str(BRIDGE_SCRIPT), *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )


def _read_thread(tinm_home: pathlib.Path, thread_id: str) -> dict:
    return json.loads(
        (tinm_home / "pcp" / "threads" / f"{thread_id}.json").read_text()
    )


def test_cross_host_refuse_then_bridge_then_allow(tinm_tmp):
    """End-to-end cross-host flow against the real bridge CLI."""
    from tinm_provenance import (
        check_thread_writable,
        compute_fingerprint,
        resolve_thread_for_cwd,
    )

    host_a = pathlib.Path(tempfile.mkdtemp(prefix="hostA-"))
    host_b = pathlib.Path(tempfile.mkdtemp(prefix="hostB-"))

    # 1. Thread created from host A (sets canonical fingerprint to A).
    thread_id = resolve_thread_for_cwd(str(host_a))
    fp_a = compute_fingerprint(str(host_a))
    fp_b = compute_fingerprint(str(host_b))
    assert fp_a != fp_b

    thread = _read_thread(tinm_tmp, thread_id)
    assert thread["metadata"]["workspace_fingerprint"] == fp_a

    # 2. Write attempt from host B is refused by the gate.
    ok, reason = check_thread_writable(thread, fp_b)
    assert ok is False
    assert "mismatch" in reason.lower()
    assert "bridge" in reason.lower()

    # 3. User runs `tinm thread bridge <slug>` from host B's cwd.
    r = _run_bridge(["bridge", thread_id], cwd=host_b)
    assert r.returncode == 0, r.stderr
    assert thread_id in r.stdout
    assert fp_b in r.stdout

    # 4. Writes from host B are now allowed.
    thread_after = _read_thread(tinm_tmp, thread_id)
    ok, _ = check_thread_writable(thread_after, fp_b)
    assert ok is True
    assert fp_b in thread_after["metadata"]["workspace_bridges"]

    # 5. Re-bridge from host B is a clean no-op (idempotent).
    r2 = _run_bridge(["bridge", thread_id], cwd=host_b)
    assert r2.returncode == 0
    assert "already" in r2.stdout.lower()

    thread_final = _read_thread(tinm_tmp, thread_id)
    # Still exactly one B-bridge — no duplication
    assert thread_final["metadata"]["workspace_bridges"].count(fp_b) == 1


def test_cross_host_list_shows_canonical_and_bridges(tinm_tmp):
    from tinm_provenance import resolve_thread_for_cwd

    host_a = pathlib.Path(tempfile.mkdtemp(prefix="hostA-"))
    host_b = pathlib.Path(tempfile.mkdtemp(prefix="hostB-"))
    host_c = pathlib.Path(tempfile.mkdtemp(prefix="hostC-"))

    thread_id = resolve_thread_for_cwd(str(host_a))
    _run_bridge(["bridge", thread_id], cwd=host_b)
    _run_bridge(["bridge", thread_id], cwd=host_c)

    r = _run_bridge(["bridge", thread_id, "--list"], cwd=host_a)
    assert r.returncode == 0
    out = r.stdout
    assert "canonical" in out.lower()
    # Both bridges appear in the listing
    from tinm_provenance import compute_fingerprint
    assert compute_fingerprint(str(host_b)) in out
    assert compute_fingerprint(str(host_c)) in out


def test_cross_host_bridge_unknown_slug_fails_clean(tinm_tmp):
    host_a = pathlib.Path(tempfile.mkdtemp(prefix="hostA-"))
    r = _run_bridge(["bridge", "does-not-exist"], cwd=host_a)
    assert r.returncode == 1
    assert "not found" in r.stderr.lower() or "not found" in r.stdout.lower()


def test_cross_host_add_workspace_explicit_path(tinm_tmp):
    """--add-workspace bridges a specific path's fingerprint, not $PWD's."""
    from tinm_provenance import compute_fingerprint, resolve_thread_for_cwd

    host_a = pathlib.Path(tempfile.mkdtemp(prefix="hostA-"))
    target = pathlib.Path(tempfile.mkdtemp(prefix="explicit-"))
    thread_id = resolve_thread_for_cwd(str(host_a))

    # Invoke from host_a but pass --add-workspace pointing at target —
    # the target's fp (NOT host_a's) should end up in bridges.
    r = _run_bridge(
        ["bridge", thread_id, "--add-workspace", str(target)],
        cwd=host_a,
    )
    assert r.returncode == 0

    thread = _read_thread(tinm_tmp, thread_id)
    bridges = thread["metadata"]["workspace_bridges"]
    assert compute_fingerprint(str(target)) in bridges
    assert compute_fingerprint(str(host_a)) not in bridges  # canonical stays canonical


def test_cross_host_bridge_re_bridge_after_concurrent_write(tinm_tmp):
    """If two CC instances try to bridge the same fp simultaneously,
    the lock + idempotence guards ensure no duplicate entries."""
    from tinm_provenance import resolve_thread_for_cwd

    host_a = pathlib.Path(tempfile.mkdtemp(prefix="hostA-"))
    host_b = pathlib.Path(tempfile.mkdtemp(prefix="hostB-"))
    thread_id = resolve_thread_for_cwd(str(host_a))

    # Serial-but-rapid bridge calls — exercises the idempotence guard
    # under realistic re-entry conditions (parallel CC instances boot
    # SessionStart roughly together).
    r1 = _run_bridge(["bridge", thread_id], cwd=host_b)
    r2 = _run_bridge(["bridge", thread_id], cwd=host_b)
    r3 = _run_bridge(["bridge", thread_id], cwd=host_b)
    assert r1.returncode == r2.returncode == r3.returncode == 0

    from tinm_provenance import compute_fingerprint
    fp_b = compute_fingerprint(str(host_b))
    thread = _read_thread(tinm_tmp, thread_id)
    assert thread["metadata"]["workspace_bridges"].count(fp_b) == 1
