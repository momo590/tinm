"""TINM SessionStart hot path — single-process runner.

Replaces the 5+ sequential `python -c …` heredocs the bash hook used to
fire (session_id extract, upgrade notif, resolve_thread_for_cwd,
writable gate, tinm_load, journal append). Each one paid ~50ms of
cold-import; consolidating brings the gate from 290ms p50 back under
the 200ms target.

The git-pull sync stays in bash — it is best-effort, network-bound, and
out of scope for the python hot path. clipboard daemon launch likewise
stays in bash (it is fire-and-forget already).

Contract:
  • Reads the Claude Code SessionStart payload on stdin.
  • Writes the thread context (tinm_load output), upgrade notification,
    and bridge / gate messages to stdout — Claude Code injects stdout
    into the session as additional context.
  • Returns 0 on success and on every failure path (the session must
    never break because TINM stumbled).
  • As a side-effect, writes `$TINM_HOME/session-<sid>.thread` so
    user_prompt.sh / stop.sh read the frozen thread for this session.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Keep the unresolved __file__.parent — see _hot_path_user_prompt.py.
SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

TINM_HOME = Path(os.environ.get("TINM_HOME", str(Path.home() / ".tinm")))
TINM_PCP_DIR = Path(os.environ.get("TINM_PCP_DIR", str(TINM_HOME / "pcp")))
VENV_PY = TINM_HOME / ".venv" / "bin" / "python"
HOOK_LOG = Path("/tmp/tinm_hook.log")
HOOK_WARN_LOG = TINM_HOME / f"hook-warn-{socket.gethostname().replace('.', '-')}.log"


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


def _maybe_emit_upgrade_notif(session_id: str) -> str | None:
    """Cached 24h check. On hit, also drop the per-session marker so
    user_prompt.sh's mid-session L1 check does not duplicate the notif."""
    try:
        from tinm_config import get_config
        from tinm_update_check import check_for_update
    except Exception as exc:
        _log(f"upgrade_notif import failed: {exc!r}")
        return None
    try:
        cfg = get_config()
    except Exception as exc:
        _log(f"upgrade_notif config failed: {exc!r}")
        return None
    if not cfg.get("update_notify", True):
        return None
    try:
        r = check_for_update()
    except Exception as exc:
        _log(f"upgrade_notif check_for_update failed: {exc!r}")
        return None
    if not r.get("has_update"):
        return None
    latest = r.get("latest") or "?"

    msg: str
    if cfg.get("auto_upgrade", False):
        upgrade = SKILL_DIR / "tinm_upgrade.py"
        try:
            subprocess.Popen(
                [str(VENV_PY), str(upgrade)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            _log(f"auto_upgrade spawn failed: {exc!r}")
        msg = f"⚫ TINM v{latest} available — auto-upgrading in background."
    else:
        msg = (
            f"\U0001f4a1 TINM v{latest} available — respond 'upgrade' at the start "
            f"of your next message to install automatically."
        )

    # Drop the suppression marker so the mid-session notifier does not echo
    # this same line in user_prompt.sh hot path.
    marker = TINM_HOME / f"session-{session_id or os.getpid()}.upgrade-shown"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass
    return msg


def _resolve_thread() -> str:
    try:
        from tinm_provenance import resolve_thread_for_cwd
        return resolve_thread_for_cwd(os.getcwd()) or ""
    except Exception as exc:
        _log(f"resolve_thread_for_cwd failed: {exc!r}")
        return ""


def _persist_thread_handoff(session_id: str, thread_id: str) -> None:
    key = session_id or str(os.getpid())
    try:
        TINM_HOME.mkdir(parents=True, exist_ok=True)
        (TINM_HOME / f"session-{key}.thread").write_text(thread_id + "\n")
    except OSError as exc:
        _log(f"persist session-thread file failed: {exc!r}")


def _run_writable_gate(thread_id: str) -> None:
    """Best-effort: refusal goes to the warn log; never block the session."""
    try:
        from tinm_provenance import compute_fingerprint, check_thread_writable
        from tinm_paths import THREADS_DIR
    except Exception as exc:
        _log(f"writable gate import failed: {exc!r}")
        return
    tp = THREADS_DIR / f"{thread_id}.json"
    if not tp.exists():
        return
    try:
        thread = json.loads(tp.read_text())
    except (json.JSONDecodeError, OSError):
        return
    try:
        fp = compute_fingerprint(os.getcwd())
        ok, reason = check_thread_writable(thread, fp)
    except Exception as exc:
        _log(f"check_thread_writable failed: {exc!r}")
        return
    if not ok:
        _warn(
            f"{_utcnow()} session_start gate-refused thread={thread_id} "
            f"cwd={os.getcwd()} reason={reason}"
        )


def _load_thread_context(thread_id: str) -> str | None:
    try:
        from tinm_load import load_thread
        return load_thread(thread_id)
    except Exception as exc:
        _log(f"load_thread failed: {exc!r}")
        return None


def _emit_context_injected_telemetry(thread_id: str, ctx: str) -> None:
    """Best-effort telemetry for the cross-session memory value path.

    SessionStart context injection is *the* mechanism that gives Claude
    cross-session continuity (see _hot_path_session_start.main → ctx ↦
    out_chunks). Before this hook, that path was invisible to telemetry,
    so we had no way to demonstrate value or detect regressions in the
    anchor/render pipeline.

    Payload is metadata-only — never raw ctx content. Schema enforced by
    tinm_telemetry._validate_payload (truncates strings to 64 chars,
    drops unknown fields).
    """
    try:
        from tinm_telemetry import log_event
    except Exception as exc:
        _log(f"telemetry import failed: {exc!r}")
        return

    threads_dir = TINM_PCP_DIR / "threads"
    artifacts_dir = TINM_PCP_DIR / "artifacts"

    trajectory_turns = 0
    anchor_terms_count = 0
    try:
        thread = json.loads((threads_dir / f"{thread_id}.json").read_text())
        trajectory_turns = len(thread.get("trajectory", []))
        anchor_terms_count = len(thread.get("anchor", {}).get("top_terms", []))
    except Exception as exc:
        _log(f"telemetry: thread read failed: {exc!r}")

    has_artifacts = False
    try:
        arts = json.loads((artifacts_dir / f"{thread_id}.json").read_text())
        has_artifacts = len(arts.get("artifacts", [])) > 0
    except Exception:
        pass

    try:
        log_event("session_context_injected", {
            "thread_id": thread_id[:32],
            "ctx_chars": len(ctx),
            "trajectory_turns": trajectory_turns,
            "anchor_terms_count": anchor_terms_count,
            "has_artifacts": has_artifacts,
        })
    except Exception as exc:
        _log(f"telemetry log_event failed: {exc!r}")


def _append_journal_entry(thread_id: str) -> None:
    """Per-host journal-<host>.jsonl entry. Mirrors what the previous
    bash heredoc did, inline so we save a python invocation."""
    host = socket.gethostname().replace(".", "-")
    journal_path = TINM_PCP_DIR / f"journal-{host}.jsonl"
    threads_dir = TINM_PCP_DIR / "threads"
    artifacts_dir = TINM_PCP_DIR / "artifacts"

    turns = 0
    anchor: list = []
    try:
        thread = json.loads((threads_dir / f"{thread_id}.json").read_text())
        turns = sum(1 for x in thread.get("trajectory", []) if x.get("role") == "user")
        anchor = thread.get("anchor", {}).get("top_terms", [])[:4]
    except Exception as exc:
        _log(f"journal: thread read failed: {exc!r}")

    n_art = 0
    try:
        arts = json.loads((artifacts_dir / f"{thread_id}.json").read_text())
        # `arts` is the full PCP v0 envelope:
        #     {"pcp_version": ..., "thread_id": ..., "artifacts": [...]}
        # Pre-2026-05-21 we wrote `len(arts)` which returned 3 (the top-level
        # key count), masking Bug #2. Always count the inner array.
        n_art = len(arts.get("artifacts", []))
    except Exception:
        pass

    entry = {
        "ts": _utcnow(),
        "thread": thread_id,
        "turns": turns,
        "anchor": anchor,
        "artifacts": n_art,
        "event": "session_start",
    }
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        _log(f"journal append failed: {exc!r}")


def main() -> int:
    t_start = time.perf_counter()
    raw = ""
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}
    session_id = payload.get("session_id", "") or ""

    out_chunks: list[str] = []

    notif = _maybe_emit_upgrade_notif(session_id)
    if notif:
        out_chunks.append(notif)

    thread_id = _resolve_thread()
    if not thread_id:
        if out_chunks:
            sys.stdout.write("\n".join(out_chunks) + "\n")
        return 0

    _persist_thread_handoff(session_id, thread_id)
    _run_writable_gate(thread_id)

    ctx = _load_thread_context(thread_id)
    if ctx:
        out_chunks.append(ctx)
        _emit_context_injected_telemetry(thread_id, ctx)

    _append_journal_entry(thread_id)

    if out_chunks:
        sys.stdout.write("\n".join(out_chunks) + "\n")

    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    _log(f"session_start hot_path done in {elapsed_ms:.1f}ms thread={thread_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
