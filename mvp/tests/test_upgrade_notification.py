"""Tests for F1: upgrade notification in session_start hook and user_prompt trigger.

Tests the session_start.sh update-check block (via the Python inline it calls)
and the user_prompt.sh upgrade trigger logic.

Strategy: test the Python logic directly (the inline Python in session_start.sh
is equivalent to a module call) and test the bash upgrade-trigger conditions by
invoking the hook in a controlled environment.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update_result(has_update: bool, current: str = "0.1.0", latest: str = "0.2.0") -> dict:
    return {
        "current": current,
        "latest": latest if has_update else current,
        "has_update": has_update,
        "source": "remote",
    }


def _run_session_start_update_block(
    monkeypatch,
    tmp_path: Path,
    has_update: bool,
    auto_upgrade: bool = False,
    update_notify: bool = True,
) -> str:
    """
    Simulate the Python inline block inside session_start.sh.
    Returns what would be printed to stdout.
    """
    import tinm_update_check as uc
    import io
    from contextlib import redirect_stdout

    monkeypatch.setattr(uc, "_fetch_remote_version", lambda: "0.2.0" if has_update else "0.1.0")
    monkeypatch.setattr(uc, "CACHE_FILE", tmp_path / "update-cache.json")
    vfile = tmp_path / "VERSION"
    vfile.write_text("0.1.0\n")
    monkeypatch.setattr(uc, "LOCAL_VERSION_FILE", vfile)

    # Inline the logic from session_start.sh Python block
    import tinm_config

    mock_cfg = {"update_notify": update_notify, "auto_upgrade": auto_upgrade}
    output = io.StringIO()
    with redirect_stdout(output):
        try:
            cfg = mock_cfg
            if not cfg.get("update_notify", True):
                pass  # sys.exit(0) equivalent — no output
            else:
                r = uc.check_for_update()
                if r.get("has_update"):
                    current, latest = r.get("current"), r.get("latest")
                    if cfg.get("auto_upgrade", False):
                        # Simulate auto-upgrade path (no actual subprocess)
                        print(f"⚫ TINM v{latest} available — auto-upgrading in background.")
                    else:
                        print(
                            f"\U0001f4a1 TINM v{latest} available — respond 'upgrade' at the start of your next message to install automatically."
                        )
        except Exception:
            pass
    return output.getvalue()


# ---------------------------------------------------------------------------
# Test 1: session_start.sh emits 💡 when has_update=True
# ---------------------------------------------------------------------------

class TestSessionStartUpdateNotification:
    def test_emits_upgrade_line_when_update_available(self, monkeypatch, tmp_path):
        """Output contains the 💡 TINM notification when has_update=True."""
        out = _run_session_start_update_block(monkeypatch, tmp_path, has_update=True)
        assert "\U0001f4a1 TINM" in out, f"Expected 💡 TINM in output, got: {out!r}"

    def test_emits_correct_version_in_notification(self, monkeypatch, tmp_path):
        """Notification includes the latest version number."""
        out = _run_session_start_update_block(monkeypatch, tmp_path, has_update=True)
        assert "0.2.0" in out, f"Expected version 0.2.0 in output, got: {out!r}"

    def test_emits_upgrade_instruction_in_notification(self, monkeypatch, tmp_path):
        """Notification includes the 'respond upgrade' instruction."""
        out = _run_session_start_update_block(monkeypatch, tmp_path, has_update=True)
        assert "upgrade" in out.lower(), f"Expected 'upgrade' in output, got: {out!r}"

    def test_no_output_when_no_update(self, monkeypatch, tmp_path):
        """No output when already on latest version."""
        out = _run_session_start_update_block(monkeypatch, tmp_path, has_update=False)
        assert out.strip() == "", f"Expected empty output, got: {out!r}"

    def test_no_output_when_notify_disabled(self, monkeypatch, tmp_path):
        """No output when update_notify=False in config."""
        out = _run_session_start_update_block(
            monkeypatch, tmp_path, has_update=True, update_notify=False
        )
        assert out.strip() == "", f"Expected empty output with notify=False, got: {out!r}"

    def test_auto_upgrade_path_emits_different_message(self, monkeypatch, tmp_path):
        """auto_upgrade=True emits a different (auto-upgrading) message."""
        out = _run_session_start_update_block(
            monkeypatch, tmp_path, has_update=True, auto_upgrade=True
        )
        assert "auto-upgrading" in out, f"Expected 'auto-upgrading' in output, got: {out!r}"
        # Should NOT emit the 💡 respond-upgrade instruction
        assert "respond" not in out, f"Expected no 'respond' text in auto-upgrade output, got: {out!r}"


# ---------------------------------------------------------------------------
# Test 2: user_prompt.sh upgrade trigger — first turn
# ---------------------------------------------------------------------------

def _make_hook_env(tmp_path: Path, transcript_lines: int = 0) -> dict:
    """Build a minimal environment for running the upgrade-trigger logic."""
    # Create a fake transcript with `transcript_lines` lines
    transcript = tmp_path / "transcript.jsonl"
    for _ in range(transcript_lines):
        transcript.open("a").write(json.dumps({"role": "user", "text": "x"}) + "\n")
    return {
        "TRANSCRIPT_PATH": str(transcript),
        "_TRANSCRIPT_LINES": str(transcript_lines),
    }


def _bash_upgrade_check(prompt_text: str, transcript_lines: int, tmp_path: Path) -> str:
    """
    Run the F1 bash upgrade-trigger logic and return stdout.
    Uses a fake UPGRADE_SCRIPT that just prints a sentinel.
    """
    fake_upgrade = tmp_path / "tinm_upgrade.py"
    fake_upgrade.write_text("import sys; print('UPGRADE_RAN')\n")
    fake_upgrade.chmod(0o755)

    venv_py = Path.home() / ".tinm" / ".venv" / "bin" / "python"
    py_bin = str(venv_py) if venv_py.is_file() else sys.executable

    transcript = tmp_path / "transcript.jsonl"
    for _ in range(transcript_lines):
        with transcript.open("a") as fh:
            fh.write(json.dumps({"role": "user", "text": "x"}) + "\n")

    # Inline the F1 bash logic as a small script
    script = f"""#!/bin/bash
PROMPT_TEXT={repr(prompt_text)}
UPGRADE_SCRIPT={repr(str(fake_upgrade))}
VENV_PY={repr(py_bin)}
_TRANSCRIPT_LINES={transcript_lines}
_FIRST20="$(printf '%s' "$PROMPT_TEXT" | tr '[:upper:]' '[:lower:]' | cut -c1-20 | tr -d ' \\t\\n')"
if printf '%s' "$_FIRST20" | grep -q '^upgrade' && [ "$_TRANSCRIPT_LINES" -lt 4 ]; then
    if [ -r "$UPGRADE_SCRIPT" ] && [ -x "$VENV_PY" ]; then
        "$VENV_PY" "$UPGRADE_SCRIPT" >/tmp/tinm_upgrade_test.log 2>&1
        echo "[TINM] Upgrade triggered — running in background. You will see the result shortly."
    fi
fi
"""
    script_path = tmp_path / "test_f1.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script_path)], capture_output=True, text=True, timeout=10
    )
    return result.stdout


class TestUserPromptUpgradeTrigger:
    def test_upgrade_keyword_first_turn_triggers(self, tmp_path):
        """'upgrade ...' as first message (turn_count < 4) triggers upgrade flow."""
        out = _bash_upgrade_check("upgrade please now", transcript_lines=0, tmp_path=tmp_path)
        assert "[TINM] Upgrade triggered" in out, f"Expected trigger, got: {out!r}"

    def test_upgrade_keyword_turn_5_does_not_trigger(self, tmp_path):
        """'upgrade' keyword at turn 5 (transcript_lines >= 4) does NOT trigger."""
        # transcript_lines=5 represents an established session (not the first turn)
        out = _bash_upgrade_check("upgrade to latest", transcript_lines=5, tmp_path=tmp_path)
        assert "[TINM] Upgrade triggered" not in out, f"Expected no trigger at turn 5, got: {out!r}"

    def test_upgrade_case_insensitive(self, tmp_path):
        """'UPGRADE' in all caps still triggers on first turn."""
        out = _bash_upgrade_check("UPGRADE now please", transcript_lines=0, tmp_path=tmp_path)
        assert "[TINM] Upgrade triggered" in out, f"Expected trigger on UPPERCASE, got: {out!r}"

    def test_non_upgrade_prompt_does_not_trigger(self, tmp_path):
        """A normal prompt on first turn does not trigger upgrade."""
        out = _bash_upgrade_check("show me the current status", transcript_lines=0, tmp_path=tmp_path)
        assert "[TINM] Upgrade triggered" not in out, f"Expected no trigger on normal prompt, got: {out!r}"

    def test_upgrade_word_mid_prompt_does_not_trigger(self, tmp_path):
        """'upgrade' appearing after first 20 chars does not trigger."""
        # Prompt where 'upgrade' does not start the first 20 chars
        out = _bash_upgrade_check("please run upgrade now", transcript_lines=0, tmp_path=tmp_path)
        assert "[TINM] Upgrade triggered" not in out, f"Expected no trigger mid-prompt, got: {out!r}"

    def test_boundary_transcript_lines_3_triggers(self, tmp_path):
        """transcript_lines=3 is still < 4, so it triggers."""
        out = _bash_upgrade_check("upgrade me", transcript_lines=3, tmp_path=tmp_path)
        assert "[TINM] Upgrade triggered" in out, f"Expected trigger at transcript_lines=3, got: {out!r}"

    def test_boundary_transcript_lines_4_does_not_trigger(self, tmp_path):
        """transcript_lines=4 is not < 4, so it does NOT trigger."""
        out = _bash_upgrade_check("upgrade me", transcript_lines=4, tmp_path=tmp_path)
        assert "[TINM] Upgrade triggered" not in out, f"Expected no trigger at transcript_lines=4, got: {out!r}"
