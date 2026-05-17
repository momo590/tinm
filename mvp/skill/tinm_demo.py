"""Install the `tinm-tour` demo thread into the PCP seeds namespace.

The TINM "whoa moment" — surfacing a result from a prior session
without reading any file — only fires when there IS a prior session.
A first-time installer has an empty PCP store, so the moment cannot
happen on day one. `/tinm demo` solves that by copying a pre-built
seed thread (`mvp/seeds/tinm-tour/`) into the install's PCP seeds
namespace at `~/.tinm/pcp/seeds/`.

As of v0.2.3 (thread-isolation design), the demo:
  - installs into `pcp/seeds/` (read-only by convention), NEVER
    `pcp/threads/` which is the user namespace,
  - does NOT touch `~/.tinm/current_thread` (eliminates the pollution
    bug where weeks of unrelated work appended to the seed thread),
  - tags `metadata.origin = "seed"` so hooks refuse writes (Layer 3).

After running, the user can paste:

    What was the biggest absolute effect we measured on the 2WikiMultihopQA pilot?

into Claude Code with `/tinm load tinm-tour` first, and watch TINM
surface the `wiki2hop-results` artifact (`+0.114 F1, t=4.03`) without
reading any file.

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

from tinm_paths import SEEDS_DIR

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


def _seed_thread_path() -> Path:
    return SEEDS_DIR / f"{DEMO_THREAD_ID}.json"


def _seed_artifacts_path() -> Path:
    # Co-located in the seeds namespace so install/remove is self-contained
    # and the user namespace at pcp/artifacts/ never sees seed payload.
    return SEEDS_DIR / f"{DEMO_THREAD_ID}.artifacts.json"


def _stamp_seed_origin(thread_dst: Path) -> None:
    """Mark the on-disk thread JSON as origin=seed so hooks refuse writes."""
    data = json.loads(thread_dst.read_text())
    meta = data.setdefault("metadata", {})
    meta["origin"] = "seed"
    # Seeds intentionally have no workspace_fingerprint — they belong to no
    # workspace. The provenance gate treats origin=seed as hard-refuse for
    # any write attempt, so the missing fingerprint is the correct shape.
    thread_dst.write_text(json.dumps(data, indent=2) + "\n")


def install(reset: bool = False) -> int:
    seed = _seed_dir()
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    thread_dst = _seed_thread_path()
    artifacts_dst = _seed_artifacts_path()

    if thread_dst.exists() and not reset:
        print(
            f"tinm-tour already installed at {thread_dst}.\n"
            "Pass --reset to re-import, or --remove to uninstall the demo.",
            file=sys.stderr,
        )
        return 1

    shutil.copyfile(seed / "thread.json", thread_dst)
    shutil.copyfile(seed / "artifacts.json", artifacts_dst)
    _stamp_seed_origin(thread_dst)

    print(
        "\n══════════════════════════════════════════════════════════════════\n"
        f"✓ Installed the `{DEMO_THREAD_ID}` demo seed into pcp/seeds/.\n"
        "══════════════════════════════════════════════════════════════════\n\n"
        "Next: load the seed into a session and paste the demo prompt:\n\n"
        f"    /tinm load {DEMO_THREAD_ID}\n\n"
        '    What was the biggest absolute effect we measured on the\n'
        "    2WikiMultihopQA pilot?\n\n"
        "Watch the [TINM ...] hook line surface BEFORE Claude responds.\n"
        "TINM will pull the wiki2hop-results artifact and quote\n"
        "  +0.114 F1, t=4.03\n"
        "without reading any file. That is the whoa moment.\n\n"
        "The seed is read-only — your own work creates its own thread\n"
        "automatically based on your cwd. Nothing leaks between them.\n\n"
        "To remove the demo seed later:\n"
        f"    {sys.argv[0]} --remove\n"
    )
    return 0


def remove() -> int:
    thread_dst = _seed_thread_path()
    artifacts_dst = _seed_artifacts_path()
    removed = False
    for p in (thread_dst, artifacts_dst):
        if p.exists():
            p.unlink()
            removed = True
    if removed:
        print(f"✓ Removed `{DEMO_THREAD_ID}` from PCP seeds namespace.")
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
        help="delete the tinm-tour seed thread + artifacts",
    )
    args = parser.parse_args(argv)
    if args.remove:
        return remove()
    return install(reset=args.reset)


if __name__ == "__main__":
    sys.exit(main())
