"""Install the `tinm-tour` demo thread into the PCP store.

The TINM "whoa moment" — surfacing a result from a prior session
without reading any file — only fires when there IS a prior session.
A first-time installer has an empty PCP store, so the moment cannot
happen on day one. `/tinm demo` solves that by copying a pre-built
seed thread (`mvp/seeds/tinm-tour/`) into the install's PCP store and
making it the current thread.

After running, the user can paste:

    What was the biggest absolute effect we measured on the 2WikiMultihopQA pilot?

into Claude Code and watch TINM surface the `wiki2hop-results`
artifact (`+0.114 F1, t=4.03`) without reading any file.

Usage:
    ~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py        # install
    ~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py --reset  # re-install
    ~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py --remove # uninstall demo
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from tinm_paths import ARTIFACTS_DIR, CURRENT_FILE, THREADS_DIR, TINM_HOME

DEMO_THREAD_ID = "tinm-tour"


def _seed_dir() -> Path:
    """Resolve the bundled seed regardless of whether this script is run
    from the repo (mvp/skill/tinm_demo.py) or via the install symlink
    (~/.claude/skills/tinm/tinm_demo.py → repo file)."""
    here = Path(__file__).resolve()
    # Walk up until we find mvp/seeds/<DEMO_THREAD_ID>/thread.json
    for ancestor in [here.parent, *here.parents]:
        candidate = ancestor / "seeds" / DEMO_THREAD_ID
        if (candidate / "thread.json").exists():
            return candidate
        # repo root case: ancestor/mvp/seeds/...
        candidate2 = ancestor / "mvp" / "seeds" / DEMO_THREAD_ID
        if (candidate2 / "thread.json").exists():
            return candidate2
    raise FileNotFoundError(
        f"Could not locate the bundled seed (tried {here}'s ancestors). "
        "Reinstall TINM with `curl -fsSL .../install.sh | bash` to restore."
    )


def install(reset: bool = False) -> int:
    seed = _seed_dir()
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    thread_dst = THREADS_DIR / f"{DEMO_THREAD_ID}.json"
    artifacts_dst = ARTIFACTS_DIR / f"{DEMO_THREAD_ID}.json"

    if thread_dst.exists() and not reset:
        print(
            f"tinm-tour already installed at {thread_dst}.\n"
            "Pass --reset to re-import, or --remove to uninstall the demo.",
            file=sys.stderr,
        )
        return 1

    shutil.copyfile(seed / "thread.json", thread_dst)
    shutil.copyfile(seed / "artifacts.json", artifacts_dst)

    # Set current_thread so the next Claude Code session picks it up.
    TINM_HOME.mkdir(parents=True, exist_ok=True)
    CURRENT_FILE.write_text(DEMO_THREAD_ID + "\n")

    print(
        "\n══════════════════════════════════════════════════════════════════\n"
        f"✓ Installed the `{DEMO_THREAD_ID}` demo thread.\n"
        "══════════════════════════════════════════════════════════════════\n\n"
        "Next: open a Claude Code session and paste this exact prompt:\n\n"
        '    What was the biggest absolute effect we measured on the\n'
        "    2WikiMultihopQA pilot?\n\n"
        "Watch the [TINM ...] hook line surface BEFORE Claude responds.\n"
        "TINM will pull the wiki2hop-results artifact and quote\n"
        "  +0.114 F1, t=4.03\n"
        "without reading any file. That is the whoa moment.\n\n"
        f"When you're ready to start your own work:\n"
        f"    /tinm init <your-thread-slug>\n\n"
        "To remove the demo thread later:\n"
        f"    {sys.argv[0]} --remove\n"
    )
    return 0


def remove() -> int:
    thread_dst = THREADS_DIR / f"{DEMO_THREAD_ID}.json"
    artifacts_dst = ARTIFACTS_DIR / f"{DEMO_THREAD_ID}.json"
    removed = False
    for p in (thread_dst, artifacts_dst):
        if p.exists():
            p.unlink()
            removed = True
    if CURRENT_FILE.exists() and CURRENT_FILE.read_text().strip() == DEMO_THREAD_ID:
        CURRENT_FILE.unlink()
    if removed:
        print(f"✓ Removed `{DEMO_THREAD_ID}` from PCP store.")
    else:
        print(f"(`{DEMO_THREAD_ID}` was not installed — nothing to remove.)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reset",
        action="store_true",
        help="overwrite the existing tinm-tour install (re-import the seed)",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="delete the tinm-tour thread + artifacts and clear current_thread",
    )
    args = parser.parse_args(argv)
    if args.remove:
        return remove()
    return install(reset=args.reset)


if __name__ == "__main__":
    sys.exit(main())
