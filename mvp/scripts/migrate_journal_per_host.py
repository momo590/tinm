"""One-shot migration: rename legacy `journal.jsonl` to per-host suffix.

Per `tinm-journal-race-risk`: the legacy single-file journal was mutable
from both Mac and VPS, producing git merge conflicts that the auto-push
hook cannot resolve. Writes now go to `journal-<hostname>.jsonl` (one
file per host); reads in `tinm_journal.py` glob all `journal-*.jsonl`
to merge them back.

This script renames the existing `journal.jsonl` to
`journal-legacy-pre-2026-05-14.jsonl` so subsequent aggregation reads
still pick up historical data. Idempotent: a no-op if no legacy file
exists or if already migrated.

Usage:
    python mvp/scripts/migrate_journal_per_host.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap so the script can be invoked from anywhere.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "skill"))

from tinm_paths import TINM_PCP_DIR  # noqa: E402

LEGACY_NAME = "journal.jsonl"
TARGET_NAME = "journal-legacy-pre-2026-05-14.jsonl"


def main() -> int:
    legacy = TINM_PCP_DIR / LEGACY_NAME
    target = TINM_PCP_DIR / TARGET_NAME

    if not legacy.exists():
        print(f"no {LEGACY_NAME} in {TINM_PCP_DIR} — nothing to migrate")
        return 0

    if target.exists():
        print(f"already migrated ({TARGET_NAME} exists) — no-op")
        return 0

    n_entries = sum(
        1 for line in legacy.read_text().splitlines() if line.strip()
    )
    legacy.rename(target)
    print(f"migrated legacy journal — {n_entries} entries preserved")
    print(f"  {LEGACY_NAME} → {TARGET_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
