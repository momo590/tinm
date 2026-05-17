"""Manual demo runner for L2 example transcripts (NOT a pytest).

Run this once after L2 lands to capture two example transcripts:

    $ ~/.tinm/.venv/bin/python mvp/skill/tests/_demo_handoff_outputs.py

Outputs go to stdout. The first example loads the bundled `tinm-tour`
seed into an isolated TINM_HOME and renders with target=claude-code.
The second hand-rolls a minimal thread (title + 1 user turn) and
renders with target=generic to verify the empty-section degradation.

This is checked in so future regressions on the handoff format are
easy to eyeball — but it's NOT collected by pytest (filename starts
with underscore).
"""
import json
import os
import shutil
import sys
import tempfile


def _run_rich() -> None:
    tmp = tempfile.mkdtemp(prefix="handoff-rich-")
    os.environ["TINM_HOME"] = tmp
    os.environ["TINM_PCP_DIR"] = tmp + "/pcp"
    sys.path.insert(0, "/root/TNIM/mvp/skill")
    for mod in ("tinm_paths", "tinm_handoff"):
        sys.modules.pop(mod, None)

    os.makedirs(tmp + "/pcp/threads", exist_ok=True)
    os.makedirs(tmp + "/pcp/artifacts", exist_ok=True)
    shutil.copy(
        "/root/TNIM/mvp/seeds/tinm-tour/thread.json",
        tmp + "/pcp/threads/tinm-tour.json",
    )
    shutil.copy(
        "/root/TNIM/mvp/seeds/tinm-tour/artifacts.json",
        tmp + "/pcp/artifacts/tinm-tour.json",
    )

    from tinm_handoff import main

    print("=" * 72)
    print("EXAMPLE 1 — Rich thread (tinm-tour seed, --target claude-code)")
    print("=" * 72)
    main(["tinm-tour", "--target", "claude-code"])


def _run_minimal() -> None:
    tmp = tempfile.mkdtemp(prefix="handoff-min-")
    os.environ["TINM_HOME"] = tmp
    os.environ["TINM_PCP_DIR"] = tmp + "/pcp"
    for mod in ("tinm_paths", "tinm_handoff"):
        sys.modules.pop(mod, None)

    os.makedirs(tmp + "/pcp/threads", exist_ok=True)
    minimal = {
        "pcp_version": "0.1",
        "thread_id": "shopmeaway-libon-widget",
        "metadata": {
            "title": "Libon × SMA widget rollout",
            "created_at": "2026-05-17T08:00:00Z",
            "last_updated": "2026-05-17T08:05:00Z",
        },
        "anchor": {"update_count": 0, "engaged_so_far": False},
        "trajectory": [
            {
                "turn": 1,
                "role": "user",
                "text": "Set up the Libon widget account and send the integration guide.",
                "ts": "2026-05-17T08:05:00Z",
            }
        ],
    }
    with open(tmp + "/pcp/threads/shopmeaway-libon-widget.json", "w") as f:
        json.dump(minimal, f)

    from tinm_handoff import main

    print()
    print("=" * 72)
    print("EXAMPLE 2 — Minimal thread (title + 1 user turn, --target generic)")
    print("=" * 72)
    main(["shopmeaway-libon-widget"])


if __name__ == "__main__":
    _run_rich()
    _run_minimal()
