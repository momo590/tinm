"""Background dispatcher for the UserPromptSubmit hot path.

Replaces the three separate `subprocess.Popen(..., start_new_session=True)`
calls the hot path used to make (anchor worker, score_and_flush, push
throttle). Spawning three processes from the hook means three fork+execs
on the hot path and three rounds of page-table setup — each one is
cheap (~5ms) but they stack up and the tail spikes can drag p99 over
the 200ms gate.

The hot path now spawns ONE detached process — this script — which then
fans out the work in series (each step is independent). The work itself
runs in the same child process, not in further forks, so the cumulative
fork cost on the parent's hot path is exactly one Popen.

Order matters for latency-of-effect: anchor worker first (drives the
next-turn pending hint), then score_and_flush (writes artifacts), then
push_throttle (Phase 2 sync). Any step's failure must not stop the
following step — each call is wrapped.

Invocation:
    python _hot_path_dispatch.py \\
        --anchor <thread_id> <target_turn> [--session-id <id>] \\
        --score-and-flush <thread_id> <session_id> <prompt> [--next-turn <n>] \\
        [--push <pcp_dir>]

Flags are optional; only the actions whose flags are present run.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

# Use the unresolved parent so test fixtures that symlink-mount this
# script into a stubbed skill dir still pick up per-test stubs (mirrors
# _hot_path_user_prompt.py / _hot_path_session_start.py).
SKILL_DIR = Path(__file__).parent
sys.path.insert(0, str(SKILL_DIR))

LOG_PATH = Path("/tmp/tinm_hook.log")


def _log(msg: str) -> None:
    try:
        with LOG_PATH.open("a") as f:
            f.write(msg.rstrip("\n") + "\n")
    except OSError:
        pass


def _run_anchor(thread_id: str, target_turn: int, session_id: str) -> None:
    """Run the anchor worker in-process. Embedding ~6s, mem ~600MB.

    Calling `run_worker` directly here means we save one fork+exec
    versus `launch_anchor_worker_async` (which would Popen another
    python). Single-flight pidfile is still respected — `run_worker`
    coordinates via $TINM_HOME/anchor-worker-<thread>.pid.
    """
    try:
        from _anchor_worker import run_worker
        from tinm_paths import TINM_PCP_DIR
        run_worker(thread_id, TINM_PCP_DIR, session_id, target_turn)
    except Exception:
        _log("dispatch.anchor failed:\n" + traceback.format_exc())


def _run_score_and_flush(
    thread_id: str, session_id: str, prompt: str, next_turn: int | None
) -> None:
    """Invoke the assistant capture pipeline in-process. Loads
    sentence-transformers via tinm_artifact, ~5s. Single-shot per turn."""
    try:
        from tinm_assistant_capture import score_and_flush
        score_and_flush(thread_id, session_id, prompt, next_user_turn=next_turn)
    except Exception:
        _log("dispatch.score_and_flush failed:\n" + traceback.format_exc())


def _run_push(pcp_dir: str) -> None:
    """Throttled git push for Phase 2 cross-host sync."""
    try:
        from push_throttle import schedule_push
        schedule_push(Path(pcp_dir))
    except Exception:
        _log("dispatch.push_throttle failed:\n" + traceback.format_exc())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fan-out background dispatcher for user_prompt hot path.")
    parser.add_argument("--anchor", nargs=2, metavar=("THREAD_ID", "TARGET_TURN"))
    parser.add_argument("--session-id", default="")
    parser.add_argument(
        "--score-and-flush",
        nargs=3,
        metavar=("THREAD_ID", "SESSION_ID", "PROMPT"),
    )
    parser.add_argument("--next-turn", type=int, default=None)
    parser.add_argument("--push", metavar="PCP_DIR")
    args = parser.parse_args(argv)

    if args.anchor:
        thread_id, target_turn_s = args.anchor
        try:
            target_turn = int(target_turn_s)
        except ValueError:
            target_turn = 0
        _run_anchor(thread_id, target_turn, args.session_id)

    if args.score_and_flush:
        thread_id, session_id, prompt = args.score_and_flush
        _run_score_and_flush(thread_id, session_id, prompt, args.next_turn)

    if args.push:
        _run_push(args.push)

    return 0


if __name__ == "__main__":
    sys.exit(main())
