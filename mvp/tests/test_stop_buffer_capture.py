"""Regression test for stop.sh assistant-buffer write (2026-05-21).

Before this fix, mvp/hooks/stop.sh used the pattern

    printf '%s' "$PAYLOAD_JSON" | "$VENV_PY" - << 'PYEOF'
        import sys; raw = sys.stdin.read()
        ...
    PYEOF

That is broken: `python -` reads the SCRIPT from stdin, and the heredoc
IS the stdin, so the piped JSON payload was silently discarded.
sys.stdin.read() always returned "" and `tinm_assistant_capture.write_buffer`
was never reached. Result: zero buffer files on disk despite the hook
firing on every Stop event. The approval-weighted-capture pipeline was
dead in production.

This test drives the REAL hook end-to-end with a synthetic transcript
and asserts that ~/.tinm/buffer/<host>-<sid>.json exists and contains
the expected assistant text. It FAILS against the pre-fix stop.sh and
PASSES against the fixed one (a heredoc-on-stdin sanity check at the
bottom proves the broken pattern is gone from the hook).

Pattern borrowed from mvp/skill/tests/test_session_isolation.py — same
isolated-env + symlinked-skill approach so the hook executes exactly
as installed.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import socket
import subprocess
import tempfile

import pytest


# Compute repo root from this test file so the test works from any
# worktree (mvp/tests/test_stop_buffer_capture.py → repo root is parents[2]).
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REAL_SKILL_DIR = REPO_ROOT / "mvp" / "skill"
REAL_HOOKS_DIR = REPO_ROOT / "mvp" / "hooks"
REAL_VENV_PY = pathlib.Path("/root/.tinm/.venv/bin/python")

HOST = socket.gethostname().replace(".", "-")


@pytest.fixture
def isolated_env():
    """Build an isolated $HOME + $TINM_HOME with the skill symlinked in.

    The hooks hardcode `$HOME/.claude/skills/tinm` and
    `$TINM_HOME/.venv/bin/python`; we symlink both into the real source
    tree + the real venv so the hook runs exactly as installed.
    """
    if not REAL_VENV_PY.exists():
        pytest.skip(f"TINM venv not installed at {REAL_VENV_PY}")
    if not REAL_HOOKS_DIR.joinpath("stop.sh").exists():
        pytest.skip(f"stop.sh not found in {REAL_HOOKS_DIR}")

    root = pathlib.Path(tempfile.mkdtemp(prefix="tinm-stop-buffer-test-"))
    fake_home = root / "home"
    (fake_home / ".claude" / "skills").mkdir(parents=True)
    (fake_home / ".claude" / "skills" / "tinm").symlink_to(REAL_SKILL_DIR)

    tinm_home = root / "tinm"
    tinm_home.mkdir()
    (tinm_home / ".venv" / "bin").mkdir(parents=True)
    (tinm_home / ".venv" / "bin" / "python").symlink_to(REAL_VENV_PY)
    (tinm_home / "buffer").mkdir()
    (tinm_home / "pcp").mkdir()

    env = {
        "HOME": str(fake_home),
        "TINM_HOME": str(tinm_home),
        "TINM_PCP_DIR": str(tinm_home / "pcp"),
        "PATH": os.environ.get("PATH", ""),
    }
    try:
        yield {"root": root, "tinm_home": tinm_home, "fake_home": fake_home, "env": env}
    finally:
        # Best-effort cleanup; never fails the test.
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def _write_transcript(path: pathlib.Path, assistant_text: str) -> None:
    """Build a minimal Claude Code transcript JSONL with a user + assistant turn.

    Mirrors the real on-disk shape (each line is a JSON object with a
    `message` field carrying `role` and `content`). The content is a
    list of text blocks, matching the more common Claude Code transcript
    format.
    """
    lines = [
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "test user turn"}],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_text}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def _write_session_handoff(tinm_home: pathlib.Path, session_id: str, thread_id: str) -> None:
    (tinm_home / f"session-{session_id}.thread").write_text(thread_id + "\n")


def _run_stop_hook(payload: dict, env: dict, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(REAL_HOOKS_DIR / "stop.sh")],
        cwd=str(cwd),
        env=env,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _buffer_path(tinm_home: pathlib.Path, session_id: str) -> pathlib.Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "default")
    return tinm_home / "buffer" / f"{HOST}-{safe}.json"


# ---------------------------------------------------------------------------
# The main regression test
# ---------------------------------------------------------------------------


def test_stop_hook_writes_assistant_buffer(isolated_env):
    """End-to-end: a real Stop payload + transcript → buffer file on disk.

    This is the canary for the heredoc-on-stdin bug. Before the fix it
    fails (no buffer file is created). After the fix it passes.
    """
    tinm_home = isolated_env["tinm_home"]
    env = isolated_env["env"]
    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-stop-buffer-"))

    session_id = "sess-buffer-regression-001"
    thread_id = "regress-thread-001"
    assistant_text = "Plan: refactor stop.sh — heredoc-on-stdin is broken."

    _write_session_handoff(tinm_home, session_id, thread_id)

    transcript = cwd / "transcript.jsonl"
    _write_transcript(transcript, assistant_text)

    payload = {"session_id": session_id, "transcript_path": str(transcript)}
    result = _run_stop_hook(payload, env, cwd)
    assert result.returncode == 0, (
        f"stop.sh failed: stderr={result.stderr!r} stdout={result.stdout!r}"
    )

    buf = _buffer_path(tinm_home, session_id)
    assert buf.exists(), (
        f"Buffer file {buf} was NOT created — the heredoc-on-stdin bug "
        f"is back. stderr={result.stderr!r}"
    )

    payload_on_disk = json.loads(buf.read_text())
    assert payload_on_disk["thread_id"] == thread_id
    assert payload_on_disk["session_id"] == session_id
    assert payload_on_disk["assistant_text"] == assistant_text
    assert payload_on_disk["host"] == HOST


def test_stop_hook_skips_tool_only_turn(isolated_env):
    """E9: tool-only assistant turns (no text content) must not write a buffer."""
    tinm_home = isolated_env["tinm_home"]
    env = isolated_env["env"]
    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-stop-toolonly-"))

    session_id = "sess-tool-only-002"
    thread_id = "regress-thread-002"
    _write_session_handoff(tinm_home, session_id, thread_id)

    # Transcript whose last assistant turn has only a tool_use block,
    # zero text. extract_assistant_text must return "" and write_buffer
    # must NOT be called.
    transcript = cwd / "transcript.jsonl"
    lines = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash",
                     "input": {"command": "ls"}}
                ],
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    payload = {"session_id": session_id, "transcript_path": str(transcript)}
    result = _run_stop_hook(payload, env, cwd)
    assert result.returncode == 0, result.stderr

    buf = _buffer_path(tinm_home, session_id)
    assert not buf.exists(), (
        f"Buffer was written for a tool-only turn (E9 violation): "
        f"{buf.read_text()}"
    )


def test_stop_hook_handles_missing_transcript(isolated_env):
    """No transcript_path / missing file → hook exits cleanly, no buffer."""
    tinm_home = isolated_env["tinm_home"]
    env = isolated_env["env"]
    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-stop-missing-"))

    session_id = "sess-missing-transcript-003"
    thread_id = "regress-thread-003"
    _write_session_handoff(tinm_home, session_id, thread_id)

    payload = {"session_id": session_id, "transcript_path": "/tmp/does-not-exist.jsonl"}
    result = _run_stop_hook(payload, env, cwd)
    assert result.returncode == 0, result.stderr

    buf = _buffer_path(tinm_home, session_id)
    assert not buf.exists()


def test_stop_hook_walks_backward_skipping_user_turns(isolated_env):
    """The capture must skip user turns and pick the LAST assistant turn."""
    tinm_home = isolated_env["tinm_home"]
    env = isolated_env["env"]
    cwd = pathlib.Path(tempfile.mkdtemp(prefix="cwd-stop-multi-"))

    session_id = "sess-multi-turn-004"
    thread_id = "regress-thread-004"
    _write_session_handoff(tinm_home, session_id, thread_id)

    transcript = cwd / "transcript.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "first prompt"}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": "older response"}},
        {"type": "user", "message": {"role": "user", "content": "second prompt"}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": "newest response"}},
    ]
    transcript.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    payload = {"session_id": session_id, "transcript_path": str(transcript)}
    result = _run_stop_hook(payload, env, cwd)
    assert result.returncode == 0, result.stderr

    buf = _buffer_path(tinm_home, session_id)
    assert buf.exists(), f"Missing buffer; stderr={result.stderr!r}"
    payload_on_disk = json.loads(buf.read_text())
    assert payload_on_disk["assistant_text"] == "newest response"


# ---------------------------------------------------------------------------
# Drift-detection: the broken heredoc-on-stdin pattern must not return.
# ---------------------------------------------------------------------------


def test_stop_hook_does_not_use_heredoc_on_stdin():
    """Anti-regression: ban `python - << 'PYEOF'` from stop.sh executable lines.

    That pattern is the bug — `python -` reads the script from stdin,
    so the heredoc clobbers any piped payload. If anyone reintroduces
    it (e.g. via a "let me inline this for clarity" refactor), this
    test fires immediately.

    We strip shell comments first so the file-level commentary that
    documents the bug doesn't itself trigger the check.
    """
    hook = (REAL_HOOKS_DIR / "stop.sh").read_text()
    # Strip lines that are pure shell comments (whitespace then `#…`).
    non_comment_lines = [
        line for line in hook.splitlines()
        if not re.match(r"^\s*#", line)
    ]
    executable = "\n".join(non_comment_lines)

    # The exact failing pattern: `python` (or $VENV_PY) followed by a
    # `-` argument and then a heredoc opener.
    forbidden_patterns = [
        r'"\$VENV_PY"\s+-\s+<<',
        r"\bpython3?\s+-\s+<<",
    ]
    for pat in forbidden_patterns:
        assert not re.search(pat, executable), (
            f"stop.sh contains the heredoc-on-stdin pattern /{pat}/ in "
            f"executable code — this is the 2026-05-21 production bug. "
            f"Use a standalone .py file instead (see _stop_capture_assistant.py)."
        )
