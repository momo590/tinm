"""TINM UserPromptSubmit hot path — single-process runner.

Replaces the 7+ sequential `python -c …` heredocs that the previous bash
hook fired in series. Each heredoc paid ~30-40ms of cold-start + import
cost; summed over 7 calls that was a 280-420ms floor on the gate. By
consolidating into one process we pay the import cost ONCE.

Reads the Claude Code hook JSON payload on stdin. Writes any context that
should be injected into Claude (pending digest, prior-turn hint, upgrade
notification, capture-pipeline ack) to stdout. All heavy work
(embedding, anchor update, score_and_flush, digest generation, git push)
is spawned via `subprocess.Popen(start_new_session=True)` so this script
returns in < 100ms even on the worst case.

Contract (must hold for the v0.3.0 gate):
  • No top-level import of sentence_transformers / torch / numpy.
  • No synchronous network I/O.
  • Every failure path is wrapped — a single broken module must NOT
    block the user's prompt.
  • stdout is only used for content Claude should see; diagnostic noise
    goes to /tmp/tinm_hook.log.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Keep the unresolved __file__.parent so tests that symlink-mount this
# script into a stubbed skill dir still pick up the per-test overrides
# of tinm_update_check / tinm_config. Production has /root/TNIM/mvp/skill
# symlinked from ~/.claude/skills/tinm and both paths point at the same
# real files, so this distinction is invisible at runtime.
SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

TINM_HOME = Path(os.environ.get("TINM_HOME", str(Path.home() / ".tinm")))
TINM_PCP_DIR = Path(os.environ.get("TINM_PCP_DIR", str(TINM_HOME / "pcp")))
VENV_PY = TINM_HOME / ".venv" / "bin" / "python"
HOOK_LOG = Path("/tmp/tinm_hook.log")
HOOK_WARN_LOG = TINM_HOME / f"hook-warn-{socket.gethostname().replace('.', '-')}.log"

_ANAPHORA_RE = re.compile(
    r"(avant|earlier|before|comme|précédemment|previously|turn|tour|step|étape|like we|what we|ce qu|qu'on)",
    re.IGNORECASE,
)


def _log(line: str) -> None:
    try:
        with HOOK_LOG.open("a") as f:
            f.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


def _warn(line: str) -> None:
    try:
        HOOK_WARN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with HOOK_WARN_LOG.open("a") as f:
            f.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _detach_spawn(args: list[str]) -> None:
    """Fire-and-forget subprocess (no wait, no pipe back)."""
    try:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        _log(f"detach_spawn failed {args[0]!r}: {exc!r}")


def _read_session_thread(session_id: str) -> str:
    """Read the per-session frozen thread name (DEC-2)."""
    if session_id:
        f = TINM_HOME / f"session-{session_id}.thread"
        try:
            return f.read_text().strip()
        except OSError:
            pass
    # Legacy fallback: global current_thread (v0.2.2 backwards compat).
    legacy = TINM_HOME / "current_thread"
    try:
        tid = legacy.read_text().strip()
        if tid:
            _warn(
                f"{_utcnow()} user_prompt fell back to legacy current_thread "
                f"(session_id={session_id or '<missing>'} thread={tid})"
            )
        return tid
    except OSError:
        return ""


def _transcript_line_count(transcript_path: str) -> int:
    if not transcript_path:
        return 0
    try:
        with open(transcript_path) as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _record_transcript_size(session_id: str, n_lines: int) -> None:
    if not session_id or n_lines <= 0:
        return
    try:
        TINM_PCP_DIR.mkdir(parents=True, exist_ok=True)
        with (TINM_PCP_DIR / "transcript_size.jsonl").open("a") as f:
            f.write(json.dumps({"ts": _utcnow(), "session_id": session_id, "n_messages": n_lines}) + "\n")
    except OSError as exc:
        _log(f"transcript_size write failed: {exc!r}")


def _conv_add(session_id: str, turn: int, text: str) -> None:
    """Inline equivalent of `python tinm_conv_add.py ... user`."""
    if not session_id or not text:
        return
    try:
        from tinm_conv_index import add_turn
        add_turn(session_id, turn, "user", text, defer_embedding=True)
    except Exception as exc:
        _log(f"conv_add failed: {exc!r}")


def _record_next_action_signals(thread_id: str, turn: int, text: str) -> None:
    if not thread_id or not text:
        return
    try:
        from tinm_next_action import record_signals
        record_signals(thread_id, turn, "user", text)
    except Exception as exc:
        _log(f"next_action record_signals failed: {exc!r}")


def _maybe_trigger_upgrade(prompt_text: str, transcript_lines: int) -> str | None:
    """If the prompt opens with `upgrade` on turn 1, spawn upgrader, return ack."""
    head = re.sub(r"\s+", "", (prompt_text or "")[:40]).lower()
    if not head.startswith("upgrade"):
        return None
    if transcript_lines >= 4:
        return None
    upgrade = SKILL_DIR / "tinm_upgrade.py"
    if not upgrade.is_file() or not VENV_PY.is_file():
        return None
    try:
        with open("/tmp/tinm_upgrade_out.log", "ab") as out:
            subprocess.Popen(
                [str(VENV_PY), str(upgrade)],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                start_new_session=True,
            )
    except OSError as exc:
        _log(f"upgrade spawn failed: {exc!r}")
        return None
    return "[TINM] Upgrade triggered — running in background. You will see the result shortly."


def _maybe_emit_midsession_upgrade_notif(session_id: str) -> str | None:
    """Every Nth prompt (default 20), check for an upgrade and notify once."""
    if not session_id:
        return None
    count_file = TINM_HOME / f"session-{session_id}.prompt-count"
    marker_file = TINM_HOME / f"session-{session_id}.upgrade-shown"

    n = 0
    try:
        n = int(count_file.read_text().strip())
    except (OSError, ValueError):
        n = 0
    n += 1
    try:
        count_file.write_text(f"{n}\n")
    except OSError:
        pass

    if marker_file.exists():
        return None

    try:
        from tinm_config import get_config
        cfg = get_config()
    except Exception:
        return None

    if not cfg.get("update_notify", True):
        return None
    interval = int(cfg.get("update_notify_interval", 20) or 0)
    if interval <= 0:
        return None
    if n < interval or (n % interval) != 0:
        return None

    try:
        from tinm_update_check import check_for_update
        r = check_for_update()
    except Exception as exc:
        _log(f"midsession update_check failed: {exc!r}")
        return None
    if not r.get("has_update"):
        return None
    latest = r.get("latest") or "?"
    try:
        marker_file.touch()
    except OSError:
        pass
    return (
        f"\U0001f4a1 TINM v{latest} available — respond 'upgrade' at the start "
        f"of your next message to install automatically."
    )


def _maybe_emit_pending_digest_and_relaunch(
    session_id: str, thread_id: str, transcript_path: str, turn_count: int
) -> str | None:
    """Return digest text from a prior worker run; conditionally launch a new one."""
    if not session_id:
        return None
    try:
        from tinm_digest import get_pending_digest, should_trigger_digest, launch_digest_async
        from tinm_compaction_detect import compaction_active
    except Exception as exc:
        _log(f"digest module import failed: {exc!r}")
        return None

    n_lines = _transcript_line_count(transcript_path)

    pending = None
    try:
        pending = get_pending_digest(session_id, turn_count)
    except Exception as exc:
        _log(f"get_pending_digest failed: {exc!r}")

    try:
        if not compaction_active(session_id, n_lines) and should_trigger_digest(
            transcript_path, session_id
        ):
            launch_digest_async(session_id, thread_id, transcript_path, turn_count)
    except Exception as exc:
        _log(f"launch_digest_async failed: {exc!r}")

    return f"[TINM digest]\n{pending}" if pending else None


def _update_turn_and_get_hint(
    thread_id: str, prompt_text: str, emit_hint: bool, session_id: str
) -> tuple[str, int]:
    """Append turn (hot path) + return (hint_text, turn_count).

    We pass `dispatch_worker=False` so update_thread does NOT Popen the
    anchor worker. The hot path spawns ONE consolidated dispatcher
    (_hot_path_dispatch.py) at the end that runs anchor + score_and_flush
    + push_throttle in one child process — saves two fork+execs vs the
    pre-v0.3.0 layout where each was its own Popen.
    """
    try:
        from tinm_update import update_thread
        result = update_thread(
            thread_id,
            query=prompt_text,
            role="user",
            client="claude-code",
            emit_hint=emit_hint,
            session_id=session_id,
            dispatch_worker=False,
        )
        return result.get("hint_text") or "", int(result.get("turn") or 0)
    except Exception as exc:
        _log(f"append_turn failed: {exc!r}")
        return "", 0


def _spawn_background_dispatch(
    thread_id: str, session_id: str, prompt_text: str, turn: int
) -> None:
    """One Popen for the three async tasks: anchor worker, score_and_flush,
    push_throttle. All three run in the same detached child process so
    the parent hot path pays exactly ONE fork+exec, not three.
    """
    script = SKILL_DIR / "_hot_path_dispatch.py"
    if not script.is_file() or not VENV_PY.is_file():
        return

    args: list[str] = [str(VENV_PY), str(script)]
    if turn > 0:
        args += ["--anchor", thread_id, str(turn)]
        if session_id:
            args += ["--session-id", session_id]
    if session_id:
        args += [
            "--score-and-flush", thread_id, session_id, prompt_text,
        ]
        if turn > 0:
            args += ["--next-turn", str(turn)]
    if (TINM_PCP_DIR / ".git").is_dir():
        args += ["--push", str(TINM_PCP_DIR)]

    # Nothing to do? Skip the spawn entirely.
    if len(args) <= 2:
        return
    _detach_spawn(args)


def main() -> int:
    t_start = time.perf_counter()
    raw = sys.stdin.read() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    prompt_text = payload.get("prompt", "") or ""
    transcript_path = payload.get("transcript_path", "") or ""
    session_id = payload.get("session_id", "") or ""

    _log(f"[{_utcnow()}] hot_path fired sid={session_id[:8]} prompt_len={len(prompt_text)}")

    if not prompt_text:
        return 0

    thread_id = _read_session_thread(session_id)
    if not thread_id:
        return 0

    transcript_lines = _transcript_line_count(transcript_path)
    _record_transcript_size(session_id, transcript_lines)
    _conv_add(session_id, transcript_lines, prompt_text)
    _record_next_action_signals(thread_id, transcript_lines, prompt_text)

    out_chunks: list[str] = []

    upgrade_ack = _maybe_trigger_upgrade(prompt_text, transcript_lines)
    if upgrade_ack:
        out_chunks.append(upgrade_ack)

    notif = _maybe_emit_midsession_upgrade_notif(session_id)
    if notif:
        out_chunks.append(notif)

    digest_block = _maybe_emit_pending_digest_and_relaunch(
        session_id, thread_id, transcript_path, transcript_lines
    )
    if digest_block:
        out_chunks.append(digest_block)

    # L1 activation gate — emit hint only when the session is long enough OR
    # the prompt contains anaphora referring to prior work.
    emit_hint = transcript_lines >= 6 or bool(_ANAPHORA_RE.search(prompt_text))
    hint, turn_count = _update_turn_and_get_hint(
        thread_id, prompt_text, emit_hint, session_id
    )
    if hint:
        out_chunks.append(hint)

    _spawn_background_dispatch(thread_id, session_id, prompt_text, turn_count)

    if out_chunks:
        sys.stdout.write("\n".join(out_chunks) + "\n")

    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    _log(f"hot_path done in {elapsed_ms:.1f}ms (out_chunks={len(out_chunks)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
