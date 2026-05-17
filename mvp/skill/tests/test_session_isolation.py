"""Session isolation integration tests (T9).

Invokes the real bash hooks (session_start.sh, user_prompt.sh, stop.sh)
in subprocesses with an isolated $HOME + $TINM_HOME to verify the DEC-2
freeze invariant: the thread is resolved ONCE at session start and
never re-derives from cwd.

Specifically asserts:
  - SessionStart in two different cwds with two different session_ids
    yields two distinct frozen threads.
  - Re-entering SessionStart in the same cwd with the same session_id
    reuses the same thread (idempotent — no thread proliferation).
  - cd'ing mid-session does NOT switch the frozen thread (user_prompt
    invoked from cwd B with session_id X reads cwd-A's frozen file,
    not cwd-B's resolved name).
  - Parallel Claude Code instances in different cwds get independent
    frozen threads.
  - stop.sh removes the per-session handoff file.
  - tinm_demo.py install does NOT touch current_thread or pcp/threads/
    (Lane B's headline guarantee — re-tested here through the hook
    surface to catch regressions).
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


REPO_ROOT = pathlib.Path("/root/TNIM")
REAL_SKILL_DIR = REPO_ROOT / "mvp" / "skill"
REAL_HOOKS_DIR = REPO_ROOT / "mvp" / "hooks"
REAL_VENV_PY = pathlib.Path("/root/.tinm/.venv/bin/python")


@pytest.fixture
def isolated_env(monkeypatch):
    """Build an isolated `$HOME` + `$TINM_HOME` for hook invocation.

    The hooks hardcode `$HOME/.claude/skills/tinm` and
    `$TINM_HOME/.venv/bin/python`. We symlink both into the real source
    tree + the real venv so the hooks execute exactly as installed.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="tinm-hook-test-"))
    fake_home = root / "home"
    (fake_home / ".claude" / "skills").mkdir(parents=True)
    (fake_home / ".claude" / "skills" / "tinm").symlink_to(REAL_SKILL_DIR)

    tinm_home = root / "tinm"
    tinm_home.mkdir()
    (tinm_home / ".venv" / "bin").mkdir(parents=True)
    (tinm_home / ".venv" / "bin" / "python").symlink_to(REAL_VENV_PY)

    env = {
        "HOME": str(fake_home),
        "TINM_HOME": str(tinm_home),
        "TINM_PCP_DIR": str(tinm_home / "pcp"),
        "PATH": os.environ.get("PATH", ""),
    }
    yield {"root": root, "tinm_home": tinm_home, "fake_home": fake_home, "env": env}


def _run_hook(hook_name: str, payload: dict, cwd: pathlib.Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(REAL_HOOKS_DIR / hook_name)],
        cwd=str(cwd),
        env=env,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _read_handoff(tinm_home: pathlib.Path, session_id: str) -> str | None:
    p = tinm_home / f"session-{session_id}.thread"
    if not p.exists():
        return None
    return p.read_text().strip() or None


# ---------------------------------------------------------------------------
# Core DEC-2 invariants
# ---------------------------------------------------------------------------


def test_two_cwds_yield_two_distinct_threads(isolated_env):
    cwd_a = pathlib.Path(tempfile.mkdtemp(prefix="cwd-A-"))
    cwd_b = pathlib.Path(tempfile.mkdtemp(prefix="cwd-B-"))

    r_a = _run_hook(
        "session_start.sh",
        {"session_id": "sess-A", "transcript_path": ""},
        cwd_a, isolated_env["env"],
    )
    r_b = _run_hook(
        "session_start.sh",
        {"session_id": "sess-B", "transcript_path": ""},
        cwd_b, isolated_env["env"],
    )
    assert r_a.returncode == 0, r_a.stderr
    assert r_b.returncode == 0, r_b.stderr

    thread_a = _read_handoff(isolated_env["tinm_home"], "sess-A")
    thread_b = _read_handoff(isolated_env["tinm_home"], "sess-B")
    assert thread_a is not None
    assert thread_b is not None
    assert thread_a != thread_b


def test_same_cwd_reuses_same_thread(isolated_env):
    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-reuse-"))

    r1 = _run_hook(
        "session_start.sh",
        {"session_id": "sess-1", "transcript_path": ""},
        cwd, isolated_env["env"],
    )
    assert r1.returncode == 0, r1.stderr
    thread_1 = _read_handoff(isolated_env["tinm_home"], "sess-1")
    assert thread_1 is not None

    # Second SessionStart in the same cwd, different session_id —
    # should resolve to the SAME thread name (per-cwd derivation), but
    # write to a different handoff file (per-session_id).
    r2 = _run_hook(
        "session_start.sh",
        {"session_id": "sess-2", "transcript_path": ""},
        cwd, isolated_env["env"],
    )
    assert r2.returncode == 0, r2.stderr
    thread_2 = _read_handoff(isolated_env["tinm_home"], "sess-2")
    assert thread_1 == thread_2

    # Exactly one thread file on disk — re-entry must not proliferate.
    threads = list((isolated_env["tinm_home"] / "pcp" / "threads").glob("*.json"))
    assert len(threads) == 1


def test_cd_mid_session_does_not_switch_thread(isolated_env):
    """The DEC-2 freeze guarantee — the headline invariant."""
    cwd_a = pathlib.Path(tempfile.mkdtemp(prefix="cwd-start-"))
    cwd_b = pathlib.Path(tempfile.mkdtemp(prefix="cwd-elsewhere-"))

    # SessionStart in cwd A
    r_start = _run_hook(
        "session_start.sh",
        {"session_id": "sess-cd", "transcript_path": ""},
        cwd_a, isolated_env["env"],
    )
    assert r_start.returncode == 0, r_start.stderr
    frozen_before = _read_handoff(isolated_env["tinm_home"], "sess-cd")
    assert frozen_before is not None

    # Simulate cd to cwd B and a UserPromptSubmit. user_prompt.sh must
    # read the EXISTING handoff file (cwd A's thread), not re-resolve
    # from cwd B. The handoff file must be unchanged after the prompt.
    r_prompt = _run_hook(
        "user_prompt.sh",
        {
            "session_id": "sess-cd",
            "transcript_path": "",
            "prompt": "any user prompt",
        },
        cwd_b, isolated_env["env"],
    )
    # user_prompt.sh may exit 0 even on internal errors (it's best-effort).
    # What matters is the handoff file is untouched.
    assert r_prompt.returncode == 0, r_prompt.stderr

    frozen_after = _read_handoff(isolated_env["tinm_home"], "sess-cd")
    assert frozen_after == frozen_before, (
        "DEC-2 violated: thread switched mid-session "
        f"({frozen_before} -> {frozen_after})"
    )


def test_parallel_sessions_independent_freezes(isolated_env):
    cwd_a = pathlib.Path(tempfile.mkdtemp(prefix="cwd-par-A-"))
    cwd_b = pathlib.Path(tempfile.mkdtemp(prefix="cwd-par-B-"))
    cwd_c = pathlib.Path(tempfile.mkdtemp(prefix="cwd-par-C-"))

    for sid, cwd in [("par-A", cwd_a), ("par-B", cwd_b), ("par-C", cwd_c)]:
        r = _run_hook(
            "session_start.sh",
            {"session_id": sid, "transcript_path": ""},
            cwd, isolated_env["env"],
        )
        assert r.returncode == 0, r.stderr

    threads = {
        sid: _read_handoff(isolated_env["tinm_home"], sid)
        for sid in ("par-A", "par-B", "par-C")
    }
    # All three frozen distinctly
    assert len(set(threads.values())) == 3
    # None is None
    assert all(threads.values())


def test_stop_removes_handoff_file(isolated_env):
    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-stop-"))

    _run_hook(
        "session_start.sh",
        {"session_id": "sess-stop", "transcript_path": ""},
        cwd, isolated_env["env"],
    )
    assert _read_handoff(isolated_env["tinm_home"], "sess-stop") is not None

    r_stop = _run_hook(
        "stop.sh",
        {"session_id": "sess-stop", "transcript_path": ""},
        cwd, isolated_env["env"],
    )
    assert r_stop.returncode == 0, r_stop.stderr
    assert _read_handoff(isolated_env["tinm_home"], "sess-stop") is None


# ---------------------------------------------------------------------------
# Lane B re-check at hook surface
# ---------------------------------------------------------------------------


def test_demo_install_leaves_current_thread_untouched(isolated_env):
    """tinm_demo install must not touch current_thread or pcp/threads/."""
    env = isolated_env["env"]
    tinm_home = isolated_env["tinm_home"]

    r = subprocess.run(
        [
            str(REAL_VENV_PY),
            str(REAL_SKILL_DIR / "tinm_demo.py"),
            "--install",
        ],
        env=env, capture_output=True, text=True, timeout=30,
    )
    # Demo may not have a seed bundled in this test setup; exit code
    # doesn't matter — what matters is the negative space.
    current_thread = tinm_home / "current_thread"
    threads_dir = tinm_home / "pcp" / "threads"
    assert not current_thread.exists(), (
        "tinm_demo wrote current_thread (regression of Lane B contract)"
    )
    if threads_dir.exists():
        # Empty is fine; any *.json in there is a regression
        leaked = list(threads_dir.glob("*.json"))
        assert leaked == [], f"tinm_demo leaked threads: {leaked}"
