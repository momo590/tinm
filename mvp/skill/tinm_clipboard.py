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
ENABLED_FLAG_FILE = TINM_HOME / "clipboard_enabled"
DEFAULT_INTERVAL = 3  # seconds


def _notify(title: str, message: str) -> None:
    """Best-effort native system notification. Never raises.

    macOS: AppleScript via osascript. Linux: notify-send if installed.
    Silent if neither is available (no-op).
    """
    try:
        if sys.platform == "darwin":
            # Escape double-quotes for AppleScript string literal
            safe_title = title.replace('"', '\\"')[:80]
            safe_msg = message.replace('"', '\\"')[:200]
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{safe_msg}" with title "{safe_title}"',
                ],
                timeout=2,
                capture_output=True,
            )
        elif sys.platform == "linux":
            if shutil.which("notify-send"):
                subprocess.run(
                    ["notify-send", "-a", "TINM", title[:80], message[:200]],
                    timeout=2,
                    capture_output=True,
                )
    except Exception:
        pass  # notifications must never break the capture flow


def is_enabled() -> bool:
    """True if the user has opted-in to clipboard watching."""
    return ENABLED_FLAG_FILE.is_file()


def enable() -> None:
    """Opt-in: future Claude Code sessions will auto-start the daemon."""
    TINM_HOME.mkdir(parents=True, exist_ok=True)
    ENABLED_FLAG_FILE.touch()


def disable() -> None:
    """Opt-out: stop daemon (if running) and prevent future auto-starts."""
    ENABLED_FLAG_FILE.unlink(missing_ok=True)
    stop_daemon()


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


def _resolve_hook_path() -> Path:
    """Locate user_prompt.sh in the TINM source tree.

    Looks first relative to this file (mvp/skill -> mvp/hooks), then falls
    back to $TINM_HOME/source/mvp/hooks for the installed layout where the
    skill is shipped to ~/.claude/skills/tinm/ (no sibling hooks/).
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
    return candidates[0]  # deterministic fallback even if missing


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
    hook = _resolve_hook_path()
    if not hook.is_file():
        print(
            f"tinm_clipboard: user_prompt.sh not found at {hook}. "
            "Check TINM install layout.",
            file=sys.stderr,
        )
        return False

    # Fire-and-forget: user_prompt.sh runs the full TINM pipeline (embeddings,
    # NPF scoring, journal write) which can take 5-30s on first call (model
    # cold-start). The clipboard daemon must not block on this — we write
    # the payload to stdin, close it, and let the subprocess complete in the
    # background. The user gets the notification immediately; the thread
    # update lands a few seconds later.
    try:
        proc = subprocess.Popen(
            ["bash", str(hook)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach so it survives the daemon
        )
        proc.stdin.write(json.dumps(normalized).encode())
        proc.stdin.close()
    except Exception as e:
        print(f"tinm_clipboard: subprocess failed: {e}", file=sys.stderr)
        return False

    _mark_captured(content)

    # Visual feedback — native system notification (best-effort, never raises)
    preview = content.replace("\n", " ").strip()
    if len(preview) > 80:
        preview = preview[:77] + "..."
    _notify(
        title=f"TINM captured to {thread_id}",
        message=preview,
    )
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


def _daemon_alive() -> bool:
    """True if a clipboard daemon is currently running."""
    if not DAEMON_PID_FILE.is_file():
        return False
    try:
        pid = int(DAEMON_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, OSError):
        DAEMON_PID_FILE.unlink(missing_ok=True)
        return False


def ensure_daemon(interval: int = DEFAULT_INTERVAL) -> bool:
    """Launch the daemon as a detached background process if not already running.

    Used by session_start.sh to auto-start without requiring the user to type
    a command. Honors the ENABLED_FLAG_FILE opt-in gate: if the user has not
    explicitly enabled clipboard watching, this is a no-op.

    Returns True if a new daemon was spawned, False otherwise.
    """
    if not is_enabled():
        return False
    if _daemon_alive():
        return False

    script = Path(__file__).resolve()
    venv_py = TINM_HOME / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable

    try:
        subprocess.Popen(
            [py, str(script), "--watch", "--interval", str(interval)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from parent so it survives session exit
            close_fds=True,
        )
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="TINM clipboard watcher")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="One-shot clipboard check")
    g.add_argument("--watch", action="store_true", help="Daemon mode (foreground)")
    g.add_argument("--ensure-daemon", action="store_true",
                   help="Launch daemon if enabled and not already running (used by SessionStart)")
    g.add_argument("--stop", action="store_true", help="Stop running daemon")
    g.add_argument("--status", action="store_true", help="Show daemon + opt-in status")
    g.add_argument("--enable", action="store_true",
                   help="Opt-in: clipboard daemon auto-starts with future Claude Code sessions")
    g.add_argument("--disable", action="store_true",
                   help="Opt-out: stop daemon and prevent future auto-starts")
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
    if args.ensure_daemon:
        spawned = ensure_daemon(args.interval)
        if spawned:
            print("clipboard daemon started")
        # silent if already running or opt-in disabled (used by hooks)
        return 0
    if args.stop:
        if stop_daemon():
            print("daemon stopped")
        else:
            print("no daemon was running")
        return 0
    if args.status:
        print(f"opt-in: {'enabled' if is_enabled() else 'disabled'}")
        print(f"daemon: {daemon_status()}")
        return 0
    if args.enable:
        enable()
        spawned = ensure_daemon(args.interval)
        msg = "clipboard watcher enabled"
        if spawned:
            msg += " (daemon started)"
        else:
            msg += " (daemon already running)" if _daemon_alive() else " (will start on next Claude Code session)"
        print(msg)
        print(f"\nTo capture: copy any text starting with '{TRIGGER_PHRASE}' on the first line.")
        return 0
    if args.disable:
        was_running = _daemon_alive()
        disable()
        if was_running:
            print("clipboard watcher disabled and daemon stopped")
        else:
            print("clipboard watcher disabled")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
