"""Show the current TINM thread status: anchor focus, trajectory, session stats."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lockfile import pcp_lock
from tinm_paths import CURRENT_FILE, THREADS_DIR, TINM_PCP_DIR
from tinm_telemetry import log_event


def show_status(thread_id: str) -> None:
    thread_path = THREADS_DIR / f"{thread_id}.json"
    if not thread_path.exists():
        print(f"Thread {thread_id!r} not found at {thread_path}", file=sys.stderr)
        sys.exit(1)

    thread = json.loads(thread_path.read_text())
    meta = thread["metadata"]
    anchor = thread["anchor"]
    traj = thread.get("trajectory", [])
    user_turns = [(t["turn"], t["text"]) for t in traj if t.get("role") == "user"]

    top_terms = anchor.get("top_terms", [])
    anchor_str = " ".join(top_terms) if top_terms else "—"
    alpha = anchor.get("alpha_used")

    print(f"TINM Thread: {meta['title']}")
    print(f"  ID:     {thread['thread_id']}")
    print(f"  Turns:  {len(traj)} total, {len(user_turns)} user queries")
    print(f"  Anchor: \"{anchor_str}\"")
    print(f"  α:      {alpha:.3f}" if alpha is not None else "  α:      —")
    print(f"  L1:     {'engaged' if anchor.get('engaged_so_far') else 'not yet (< turn 3)'}")
    if user_turns:
        print()
        print("Trajectory (user queries only):")
        for turn_num, text in user_turns:
            print(f"  ({turn_num}) {text}")


def reset_anchor(thread_id: str) -> None:
    thread_path = THREADS_DIR / f"{thread_id}.json"
    if not thread_path.exists():
        print(f"Thread {thread_id!r} not found.", file=sys.stderr)
        sys.exit(1)
    thread = json.loads(thread_path.read_text())
    thread["anchor"]["vector"] = None
    thread["anchor"]["update_count"] = 0
    thread["anchor"]["engaged_so_far"] = False
    thread["anchor"]["top_terms"] = []
    with pcp_lock(TINM_PCP_DIR):
        thread_path.write_text(json.dumps(thread, indent=2) + "\n")
    print(f"Anchor reset for thread {thread_id}. Trajectory preserved.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show or reset TINM session status.")
    parser.add_argument("thread_id", nargs="?",
                        help="Thread ID (default: current thread from ~/.tinm/current_thread)")
    parser.add_argument("--reset-anchor", action="store_true",
                        help="Reset the EMA anchor to zero while keeping the trajectory.")
    args = parser.parse_args()

    thread_id = args.thread_id
    if not thread_id:
        if not CURRENT_FILE.exists():
            print("No current thread. Run /tinm init first.", file=sys.stderr)
            sys.exit(1)
        thread_id = CURRENT_FILE.read_text().strip()
    if not thread_id:
        print("No thread ID found.", file=sys.stderr)
        sys.exit(1)

    action = "status_reset_anchor" if args.reset_anchor else "status"
    log_event("user_explicit_action", {"action": action})

    if args.reset_anchor:
        reset_anchor(thread_id)
    else:
        show_status(thread_id)


if __name__ == "__main__":
    main()
