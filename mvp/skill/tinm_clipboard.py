"""TINM clipboard watcher — opt-in cross-platform AI conversation capture.

Polls the system clipboard. Activates ONLY when the clipboard content starts
with the trigger phrase '# TINM SAVE' (case-insensitive, on its own line).
The trigger line is stripped; the rest of the clipboard is captured into the
current TINM thread.

PRIVACY GUARANTEES:
- Never captures clipboard content without the explicit trigger phrase
- Trigger phrase must be the FIRST non-empty line — partial matches anywhere
  else in the clipboard are ignored
- The trigger line itself is stripped before capture (not stored)
- After successful capture, clipboard is NOT modified (we don't clear it —
  some users rely on the clipboard for other purposes)
- Marker file `~/.tinm/buffer/clipboard_last_capture.txt` stores only a hash
  of the last captured content to avoid duplicate captures of the same paste

DEPENDENCY-FREE: uses native OS commands instead of pyperclip
- macOS: `pbpaste`
- Linux (X11): `xclip -selection clipboard -o`
- Linux (Wayland): `wl-paste`
- Falls back gracefully if no clipboard tool is available

Cross-platform: tested patterns work on macOS + Linux. Windows support deferred.

Usage:
    # One-shot check (call from hook):
    python tinm_clipboard.py --once

    # Daemon mode (background polling, default 3s interval):
    python tinm_clipboard.py --watch [--interval 3]

    # Disable the watcher:
    python tinm_clipboard.py --status   # show whether daemon is running
    python tinm_clipboard.py --stop
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tinm_paths import BUFFER_DIR, CURRENT_FILE, TINM_HOME
from tinm_vendor_adapters import normalize_for

TRIGGER_PHRASE = "# TINM SAVE"
LAST_CAPTURE_HASH_FILE = BUFFER_DIR / "clipboard_last_capture.txt"
DAEMON_PID_FILE = TINM_HOME / "clipboard_daemon.pid"
DEFAULT_INTERVAL = 3  # seconds


def _detect_clipboard_cmd() -> Optional[list[str]]:
    """Detect the available clipboard-read command for this OS."""
    if sys.platform == "darwin":
        if shutil.which("pbpaste"):
            return ["pbpaste"]
    elif sys.platform == "linux":
        # Prefer Wayland over X11 (modern distros)
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
            return ["wl-paste", "--no-newline"]
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard", "-o"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--output"]
    return None


def read_clipboard() -> Optional[str]:
    """Read the current clipboard content. Returns None if unavailable."""
    cmd = _detect_clipboard_cmd()
    if cmd is None:
        return None
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def extract_trigger_content(clipboard: str) -> Optional[str]:
    """Return the content AFTER the trigger phrase, or None if no trigger.

    The trigger phrase '# TINM SAVE' must be on the FIRST non-empty line
    (case-insensitive). Everything after that line is the captured content.
    """
    if not clipboard:
        return None

    lines = clipboard.split("\n")
    first_non_empty_idx = -1
    for i, line in enumerate(lines):
        if line.strip():
            first_non_empty_idx = i
            break

    if first_non_empty_idx < 0:
        return None

    first_line = lines[first_non_empty_idx].strip()
    if first_line.upper() != TRIGGER_PHRASE.upper():
        return None

    # Content = everything after the trigger line
    after = "\n".join(lines[first_non_empty_idx + 1:]).strip()
    if not after:
        return None
    return after


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _already_captured(content: str) -> bool:
    """Check if this exact content was already captured (avoid duplicates)."""
    if not LAST_CAPTURE_HASH_FILE.is_file():
        return False
    try:
        last_hash = LAST_CAPTURE_HASH_FILE.read_text().strip()
        return last_hash == _content_hash(content)
    except Exception:
        return False


def _mark_captured(content: str) -> None:
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        LAST_CAPTURE_HASH_FILE.write_text(_content_hash(content))
    except Exception:
        pass


def _current_thread() -> Optional[str]:
    if not CURRENT_FILE.is_file():
        return None
    try:
        return CURRENT_FILE.read_text().strip() or None
    except Exception:
        return None


def capture_to_tinm(content: str) -> bool:
    """Send captured content to the TINM pipeline as a clipboard event.

    Returns True on success, False if no current thread or any error.
    """
    thread_id = _current_thread()
    if not thread_id:
        print(
            "tinm_clipboard: no current TINM thread. "
            "Run `tinm load <thread>` first.",
            file=sys.stderr,
        )
        return False

    payload = {
        "text": content,
        "session_id": f"clipboard-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
    }
    normalized = normalize_for("clipboard", payload)
    if not normalized:
        return False

    # Pipe normalized JSON to user_prompt.sh — same pipeline as Cursor/etc.
    hook = Path(__file__).resolve().parent.parent / "hooks" / "user_prompt.sh"
    if not hook.is_file():
        return False

    try:
        proc = subprocess.Popen(
            ["bash", str(hook)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(input=json.dumps(normalized).encode(), timeout=5)
    except Exception:
        return False

    _mark_captured(content)
    return True


def check_once() -> bool:
    """One-shot: check clipboard, capture if trigger found, return True if captured."""
    clipboard = read_clipboard()
    if clipboard is None:
        return False
    content = extract_trigger_content(clipboard)
    if content is None:
        return False
    if _already_captured(content):
        return False
    return capture_to_tinm(content)


def watch(interval: int = DEFAULT_INTERVAL) -> None:
    """Daemon mode: poll clipboard every `interval` seconds."""
    TINM_HOME.mkdir(parents=True, exist_ok=True)
    DAEMON_PID_FILE.write_text(str(os.getpid()))
    print(f"tinm_clipboard: watching clipboard every {interval}s. Trigger: '{TRIGGER_PHRASE}'")
    try:
        while True:
            if check_once():
                print(f"[{datetime.now(timezone.utc).isoformat()}] captured to TINM")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\ntinm_clipboard: stopped.")
    finally:
        try:
            DAEMON_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def stop_daemon() -> bool:
    """Stop a running daemon (if any). Returns True if a daemon was killed."""
    if not DAEMON_PID_FILE.is_file():
        return False
    try:
        pid = int(DAEMON_PID_FILE.read_text().strip())
        os.kill(pid, 15)  # SIGTERM
        DAEMON_PID_FILE.unlink(missing_ok=True)
        return True
    except (ValueError, ProcessLookupError, OSError):
        DAEMON_PID_FILE.unlink(missing_ok=True)
        return False


def daemon_status() -> str:
    """Return a human-readable status string."""
    if not DAEMON_PID_FILE.is_file():
        return "not running"
    try:
        pid = int(DAEMON_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = check if process exists
        return f"running (pid {pid})"
    except (ValueError, ProcessLookupError, OSError):
        DAEMON_PID_FILE.unlink(missing_ok=True)
        return "stale pidfile (cleaned)"


def main() -> int:
    parser = argparse.ArgumentParser(description="TINM clipboard watcher")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="One-shot clipboard check")
    g.add_argument("--watch", action="store_true", help="Daemon mode (foreground)")
    g.add_argument("--stop", action="store_true", help="Stop running daemon")
    g.add_argument("--status", action="store_true", help="Show daemon status")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    args = parser.parse_args()

    if args.once:
        captured = check_once()
        if captured:
            print("captured")
        return 0
    if args.watch:
        watch(args.interval)
        return 0
    if args.stop:
        if stop_daemon():
            print("daemon stopped")
        else:
            print("no daemon was running")
        return 0
    if args.status:
        print(daemon_status())
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
