"""TINM CLI wrapper — wraps any subprocess to capture stdin/stdout for TINM.

[BETA-unverified] Shared engine for Aider + Codex wrappers. Spawns the wrapped
command, tees its stdin to TINM's pipeline while letting the user interact
normally.

Detection heuristic (per vendor in tinm_vendor_adapters.VENDORS):
- Aider: detect user prompts via Aider's "> " prompt prefix on stdin lines
- Codex: detect via stdin newline-delimited blocks

Each detected user prompt is normalized via tinm_vendor_adapters and piped to
user_prompt.sh in a non-blocking subprocess.

Usage:
    python -m tinm_cli_wrap --vendor aider -- aider --model claude-sonnet ...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

# Make sibling modules importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tinm_vendor_adapters import normalize_for  # noqa: E402


def _resolve_hook_path() -> Path:
    """Locate user_prompt.sh in the TINM source tree.

    We look first relative to this file (mvp/skill -> mvp/hooks), then fall
    back to $TINM_HOME/source/mvp/hooks for the installed layout where the
    skill is shipped to ~/.claude/skills/tinm/.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "hooks" / "user_prompt.sh",
        Path(os.environ.get("TINM_HOME", str(Path.home() / ".tinm")))
        / "source"
        / "mvp"
        / "hooks"
        / "user_prompt.sh",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]  # return a deterministic path even if missing


def _capture_user_input(vendor: str, session_id: str, line: str) -> None:
    """Non-blocking: send a captured user prompt to TINM."""
    if not line or not line.strip():
        return
    payload = {"input": line.strip(), "session_id": session_id}
    normalized = normalize_for(vendor, payload)
    if not normalized:
        return
    hook = _resolve_hook_path()
    if not hook.is_file():
        return
    # Pipe normalized JSON to user_prompt.sh in a detached subprocess.
    # Never block the user's CLI on hook errors.
    try:
        proc = subprocess.Popen(
            ["bash", str(hook)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            proc.communicate(input=json.dumps(normalized).encode(), timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass  # never block the user's CLI


def wrap_command(vendor: str, command: list, session_id: str = None) -> int:
    """Spawn the wrapped command. Tee user stdin lines to TINM.

    Returns the wrapped process's exit code.
    """
    session_id = session_id or f"{vendor}-{uuid.uuid4().hex[:8]}"

    # Simple pipe-based passthrough. A pty would be nicer for fully
    # interactive feel, but pipes are sufficient for line-oriented CLIs
    # like Aider and Codex and avoid the tty plumbing complexity.
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=None,  # passthrough to terminal
            stderr=None,
        )
    except FileNotFoundError:
        print(
            f"tinm_cli_wrap: command not found: {command[0] if command else ''}",
            file=sys.stderr,
        )
        return 127

    try:
        # Read user's stdin lines, send to BOTH the wrapped process AND TINM
        while True:
            try:
                line = sys.stdin.readline()
            except KeyboardInterrupt:
                proc.terminate()
                break
            if not line:
                break
            try:
                proc.stdin.write(line.encode())
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                break
            # Capture in background thread to avoid blocking the wrapped CLI
            threading.Thread(
                target=_capture_user_input,
                args=(vendor, session_id, line),
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        proc.terminate()
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass

    return proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TINM CLI wrapper for Aider/Codex/etc."
    )
    parser.add_argument(
        "--vendor",
        required=True,
        choices=["aider", "codex"],
        help="Vendor name (controls normalization rules).",
    )
    parser.add_argument("--session-id", default=None)
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to wrap (use -- separator).",
    )
    args = parser.parse_args()

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("tinm_cli_wrap: no command to wrap", file=sys.stderr)
        return 1

    return wrap_command(args.vendor, cmd, args.session_id)


if __name__ == "__main__":
    sys.exit(main())
