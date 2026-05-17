"""L1 mid-session upgrade notification tests.

Scope: the new F1b block in `user_prompt.sh` that re-checks for a TINM
upgrade every N user prompts during a long session. The infrastructure
(`tinm_update_check`, `tinm_upgrade`) is tested elsewhere; here we focus
on the trigger logic:

  - Notif suppressed when no update is available
  - Notif fires exactly once when update IS available, even across many
    UserPromptSubmit invocations within the same session_id
  - Notif fires only on the Nth prompt (interval honored)
  - Notif respects update_notify=false (kill switch)
  - Notif respects update_notify_interval=0 (disable mid-session)
  - SessionStart-side notif marker also suppresses user_prompt.sh's notif
    (no dup when both fire at the start of a session)
  - stop.sh removes the per-session prompt-count + upgrade-shown files
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


# ---------------------------------------------------------------------------
# Test fixture — isolated $HOME + $TINM_HOME with a stubbed update_check
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_env_with_stub():
    """Build an isolated env where tinm_update_check is REPLACED with a stub
    that returns has_update=True deterministically (no network).

    We do this by symlinking the skill dir to a writable copy where we
    overwrite tinm_update_check.py with a stub. The rest of the tree is
    symlinked through to the real source so all the other imports work.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="tinm-upgrade-notify-test-"))
    fake_home = root / "home"
    skills_dir = fake_home / ".claude" / "skills"
    skills_dir.mkdir(parents=True)

    # Create a writable skill dir we control. Symlink every file from the
    # real skill source EXCEPT tinm_update_check.py + tinm_config.py +
    # tests/, which we replace with stubs / control the source of truth.
    skill_dir = skills_dir / "tinm"
    skill_dir.mkdir()
    for item in REAL_SKILL_DIR.iterdir():
        if item.name in (
            "tinm_update_check.py",
            "tinm_config.py",
            "tests",
            "__pycache__",
        ):
            continue
        target = skill_dir / item.name
        target.symlink_to(item)

    # We DO want the real tinm_config.py so the DEFAULTS (incl.
    # update_notify_interval) come through.
    (skill_dir / "tinm_config.py").symlink_to(REAL_SKILL_DIR / "tinm_config.py")

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

    yield {
        "root": root,
        "tinm_home": tinm_home,
        "fake_home": fake_home,
        "skill_dir": skill_dir,
        "env": env,
    }


def _write_update_check_stub(skill_dir: pathlib.Path, has_update: bool):
    """Replace tinm_update_check.py with a deterministic stub."""
    stub = f"""# Test stub for tinm_update_check.
def get_local_version():
    return "0.2.3"

def check_for_update(force=False):
    return {{
        "current": "0.2.3",
        "latest": "9.9.9",
        "has_update": {has_update!r},
        "source": "cache",
    }}
"""
    target = skill_dir / "tinm_update_check.py"
    if target.is_symlink() or target.exists():
        target.unlink()
    target.write_text(stub)


def _run_hook(
    hook_name: str,
    payload: dict,
    cwd: pathlib.Path,
    env: dict,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(REAL_HOOKS_DIR / hook_name)],
        cwd=str(cwd),
        env=env,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_thread_handoff(tinm_home: pathlib.Path, session_id: str, thread: str = "stub-thread"):
    """user_prompt.sh exits early if no thread handoff exists. Create one."""
    p = tinm_home / f"session-{session_id}.thread"
    p.write_text(thread + "\n")


def _user_prompt(
    session_id: str,
    cwd: pathlib.Path,
    env: dict,
    prompt: str = "hello world",
) -> subprocess.CompletedProcess:
    payload = {
        "session_id": session_id,
        "transcript_path": "",
        "prompt": prompt,
    }
    return _run_hook("user_prompt.sh", payload, cwd, env)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_notif_not_emitted_when_no_update_available(isolated_env_with_stub):
    """Stub returns has_update=False → notif never fires."""
    env = isolated_env_with_stub["env"]
    _write_update_check_stub(isolated_env_with_stub["skill_dir"], has_update=False)
    _make_thread_handoff(isolated_env_with_stub["tinm_home"], "sess-noup")

    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-noup-"))
    for _ in range(25):
        r = _user_prompt("sess-noup", cwd, env)
        assert r.returncode == 0, r.stderr
        assert "available" not in r.stdout, r.stdout

    marker = isolated_env_with_stub["tinm_home"] / "session-sess-noup.upgrade-shown"
    assert not marker.exists()


def test_notif_fires_exactly_once_across_30_prompts(isolated_env_with_stub):
    """The headline manual-verification scenario from the L1 ticket:
    30 prompts in the same session_id with has_update=True → notif
    appears exactly once.
    """
    env = isolated_env_with_stub["env"]
    _write_update_check_stub(isolated_env_with_stub["skill_dir"], has_update=True)
    _make_thread_handoff(isolated_env_with_stub["tinm_home"], "sess-once")

    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-once-"))
    hits = 0
    for i in range(30):
        r = _user_prompt("sess-once", cwd, env)
        assert r.returncode == 0, r.stderr
        if "TINM v9.9.9 available" in r.stdout:
            hits += 1
    assert hits == 1, f"expected exactly 1 notif in 30 prompts, got {hits}"

    # Marker file should exist after the notif fired.
    marker = isolated_env_with_stub["tinm_home"] / "session-sess-once.upgrade-shown"
    assert marker.exists(), "marker file should be created after notif fires"


def test_notif_fires_on_interval_boundary_only(isolated_env_with_stub):
    """With default interval=20, notif fires on prompt #20 (the Nth),
    never before. Verifies the interval logic — not just one-shot
    suppression but the actual schedule.
    """
    env = isolated_env_with_stub["env"]
    _write_update_check_stub(isolated_env_with_stub["skill_dir"], has_update=True)
    _make_thread_handoff(isolated_env_with_stub["tinm_home"], "sess-int")

    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-int-"))
    notif_at = None
    for i in range(1, 25):
        r = _user_prompt("sess-int", cwd, env)
        assert r.returncode == 0, r.stderr
        if "TINM v9.9.9 available" in r.stdout:
            notif_at = i
            break
    assert notif_at == 20, f"notif should fire at prompt 20, got {notif_at}"


def test_notify_disabled_suppresses_notif(isolated_env_with_stub):
    """update_notify=false in user config → notif never fires even with
    has_update=true.
    """
    env = isolated_env_with_stub["env"]
    _write_update_check_stub(isolated_env_with_stub["skill_dir"], has_update=True)
    _make_thread_handoff(isolated_env_with_stub["tinm_home"], "sess-off")

    # Write user config opt-out. tinm_config reads from
    # Path.home() / ".tinm" / "config.json" — i.e. $HOME/.tinm not
    # $TINM_HOME, so place it under the fake_home.
    cfg_path = isolated_env_with_stub["fake_home"] / ".tinm" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"update_notify": False}))

    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-off-"))
    for _ in range(25):
        r = _user_prompt("sess-off", cwd, env)
        assert "available" not in r.stdout


def test_interval_zero_disables_mid_session_check(isolated_env_with_stub):
    """update_notify_interval=0 → mid-session check is disabled entirely
    (SessionStart side still works on its own cadence).
    """
    env = isolated_env_with_stub["env"]
    _write_update_check_stub(isolated_env_with_stub["skill_dir"], has_update=True)
    _make_thread_handoff(isolated_env_with_stub["tinm_home"], "sess-zero")

    cfg_path = isolated_env_with_stub["fake_home"] / ".tinm" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"update_notify_interval": 0}))

    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-zero-"))
    for _ in range(25):
        r = _user_prompt("sess-zero", cwd, env)
        assert "available" not in r.stdout


def test_existing_marker_suppresses_mid_session_notif(isolated_env_with_stub):
    """If SessionStart already showed the notif (marker file exists),
    user_prompt.sh's mid-session check must NOT re-emit.
    """
    env = isolated_env_with_stub["env"]
    _write_update_check_stub(isolated_env_with_stub["skill_dir"], has_update=True)
    _make_thread_handoff(isolated_env_with_stub["tinm_home"], "sess-pre")

    # Pre-create the marker — simulating SessionStart having already
    # surfaced the notif at the top of the session.
    marker = isolated_env_with_stub["tinm_home"] / "session-sess-pre.upgrade-shown"
    marker.touch()

    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-pre-"))
    for _ in range(25):
        r = _user_prompt("sess-pre", cwd, env)
        assert "available" not in r.stdout, (
            "marker should have suppressed mid-session notif"
        )


def test_stop_cleans_up_per_session_marker_and_counter(isolated_env_with_stub):
    """stop.sh must delete the per-session upgrade marker + prompt counter
    so the next session starts clean. Otherwise a stale marker would
    silently suppress next session's legitimate notif.
    """
    env = isolated_env_with_stub["env"]
    _write_update_check_stub(isolated_env_with_stub["skill_dir"], has_update=True)
    _make_thread_handoff(isolated_env_with_stub["tinm_home"], "sess-stop")

    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-stop-"))
    # 20 prompts: triggers the notif and creates both state files.
    for _ in range(20):
        _user_prompt("sess-stop", cwd, env)

    tinm_home = isolated_env_with_stub["tinm_home"]
    marker = tinm_home / "session-sess-stop.upgrade-shown"
    counter = tinm_home / "session-sess-stop.prompt-count"
    assert marker.exists(), "marker should exist after notif fires"
    assert counter.exists(), "counter should exist after prompts"

    r_stop = _run_hook(
        "stop.sh",
        {"session_id": "sess-stop", "transcript_path": ""},
        cwd, env,
    )
    assert r_stop.returncode == 0, r_stop.stderr
    assert not marker.exists(), "stop.sh must clean up upgrade-shown marker"
    assert not counter.exists(), "stop.sh must clean up prompt-count file"
