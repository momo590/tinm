"""Tests for tinm_cli_wrap.py — generic CLI wrapper for Aider/Codex."""
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

import tinm_cli_wrap
from tinm_cli_wrap import _capture_user_input, wrap_command


# ── _capture_user_input ────────────────────────────────────────────────────


def test_capture_ignores_empty_string():
    """Empty lines must NOT spawn user_prompt.sh."""
    with patch.object(tinm_cli_wrap.subprocess, "Popen") as p:
        _capture_user_input("aider", "sess-1", "")
        p.assert_not_called()


def test_capture_ignores_whitespace_only():
    with patch.object(tinm_cli_wrap.subprocess, "Popen") as p:
        _capture_user_input("aider", "sess-1", "   \n\t")
        p.assert_not_called()


def test_capture_unknown_vendor_silently_skipped():
    """Unknown vendor → normalize_for returns None → no subprocess spawned."""
    with patch.object(tinm_cli_wrap.subprocess, "Popen") as p:
        _capture_user_input("not-a-real-vendor", "sess-1", "hello")
        p.assert_not_called()


def test_capture_spawns_subprocess_when_hook_exists(tmp_path, monkeypatch):
    """When hook exists and input is non-empty, Popen is called with the
    normalized JSON piped in."""
    fake_hook = tmp_path / "user_prompt.sh"
    fake_hook.write_text("#!/bin/bash\nexit 0\n")
    monkeypatch.setattr(tinm_cli_wrap, "_resolve_hook_path", lambda: fake_hook)

    calls = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})

        def communicate(self, input=None, timeout=None):
            calls[-1]["input"] = input
            return b"", b""

        def kill(self):
            pass

    monkeypatch.setattr(tinm_cli_wrap.subprocess, "Popen", FakePopen)

    _capture_user_input("aider", "sess-99", "real prompt")
    assert len(calls) == 1
    payload = json.loads(calls[0]["input"])
    assert payload["prompt"] == "real prompt"
    assert payload["session_id"] == "sess-99"
    assert payload["hook_type"] == "aider_cli"


def test_capture_missing_hook_silently_skipped(tmp_path, monkeypatch):
    """If hook file doesn't exist, no Popen call happens."""
    missing = tmp_path / "does_not_exist.sh"
    monkeypatch.setattr(tinm_cli_wrap, "_resolve_hook_path", lambda: missing)

    with patch.object(tinm_cli_wrap.subprocess, "Popen") as p:
        _capture_user_input("codex", "sess-1", "prompt")
        p.assert_not_called()


def test_capture_subprocess_exception_does_not_raise(tmp_path, monkeypatch):
    """Popen raising must be swallowed — never block the user's CLI."""
    fake_hook = tmp_path / "user_prompt.sh"
    fake_hook.write_text("#!/bin/bash\nexit 0\n")
    monkeypatch.setattr(tinm_cli_wrap, "_resolve_hook_path", lambda: fake_hook)

    def boom(*a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(tinm_cli_wrap.subprocess, "Popen", boom)

    # Should not raise
    _capture_user_input("aider", "sess-1", "prompt")


# ── wrap_command ───────────────────────────────────────────────────────────


def test_wrap_command_returns_exit_code_zero(monkeypatch):
    """wrap_command should return the wrapped process's exit code.

    Use `true` which immediately exits 0.
    """
    # Empty stdin — wrap_command's readline loop terminates immediately
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = wrap_command("aider", ["true"], session_id="sess-test")
    assert rc == 0


def test_wrap_command_returns_nonzero_exit_code(monkeypatch):
    """`false` exits 1 — wrap_command should propagate that."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = wrap_command("aider", ["false"], session_id="sess-test")
    assert rc == 1


def test_wrap_command_command_not_found(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = wrap_command(
        "aider",
        ["this-command-definitely-does-not-exist-xyz"],
        session_id="sess-test",
    )
    assert rc == 127


def test_wrap_command_autogenerates_session_id(monkeypatch):
    """If session_id is None, one should be generated with the vendor prefix.

    We don't have a direct hook to inspect the generated ID, so we patch
    the capture function and feed one line through stdin.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))

    captured_session_ids = []

    def fake_capture(vendor, session_id, line):
        captured_session_ids.append(session_id)

    monkeypatch.setattr(tinm_cli_wrap, "_capture_user_input", fake_capture)

    # `cat` will read stdin and echo it back, then exit when stdin closes.
    rc = wrap_command("codex", ["cat"], session_id=None)
    assert rc == 0
    # The capture should have been invoked at least once with an auto-id
    # starting with the vendor prefix.
    assert len(captured_session_ids) >= 1
    assert captured_session_ids[0].startswith("codex-")
    assert len(captured_session_ids[0]) == len("codex-") + 8


def test_wrap_command_passes_stdin_through(monkeypatch, tmp_path):
    """Lines typed on our stdin should be sent through to the wrapped proc."""
    out_file = tmp_path / "captured.txt"

    # cat will copy stdin to stdout — redirect stdout into out_file so we
    # can verify the bytes made it through.
    monkeypatch.setattr(sys, "stdin", io.StringIO("alpha\nbeta\n"))
    monkeypatch.setattr(tinm_cli_wrap, "_capture_user_input", lambda *a, **kw: None)

    # We need to redirect the subprocess's stdout. The current
    # implementation uses stdout=None (terminal passthrough); we patch
    # Popen to redirect stdout into our temp file while preserving the
    # rest of the behavior.
    orig_popen = subprocess.Popen

    def custom_popen(*args, **kwargs):
        kwargs["stdout"] = open(out_file, "wb")
        return orig_popen(*args, **kwargs)

    monkeypatch.setattr(tinm_cli_wrap.subprocess, "Popen", custom_popen)

    rc = wrap_command("aider", ["cat"], session_id="sess-x")
    assert rc == 0
    contents = out_file.read_text()
    assert "alpha" in contents
    assert "beta" in contents


# ── _resolve_hook_path ─────────────────────────────────────────────────────


def test_resolve_hook_path_returns_path():
    """Should always return a Path, even if no candidate exists."""
    result = tinm_cli_wrap._resolve_hook_path()
    assert isinstance(result, Path)
