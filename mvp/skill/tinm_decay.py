"""TINM 90-day decay for auto-captured artifacts.

Auto-captured artifacts (`source ∈ {approved_exchange}`) that haven't been
retrieved by `cmd_find` in DECAY_DAYS days are moved to cold storage
(`pcp/artifacts_cold/<thread>.json`) so they don't pollute the hot
`artifact_find` index. Reactivatable on explicit reference (TODO v0.2.2).

Manual artifacts (`source ∈ {manual, legacy_manual}`) are NEVER decayed —
they were intentional curation by the user.

Run on SessionStart (lazy, cheap) or via `python tinm_decay.py <thread_id>`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lockfile import pcp_lock
from tinm_paths import ARTIFACTS_COLD_DIR, ARTIFACTS_DIR, TINM_PCP_DIR

DECAY_DAYS = 90
AUTO_SOURCES = {"approved_exchange"}


def _utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def decay_thread(thread_id: str, *, now: datetime | None = None,
                 days: int = DECAY_DAYS) -> dict:
    """Move stale auto-artifacts to cold storage. Returns counts."""
    now = now or _utcnow_dt()
    cutoff = now - timedelta(days=days)

    hot_path = ARTIFACTS_DIR / f"{thread_id}.json"
    if not hot_path.exists():
        return {"moved": 0, "kept": 0, "skipped_manual": 0}

    hot = json.loads(hot_path.read_text())
    cold_path = ARTIFACTS_COLD_DIR / f"{thread_id}.json"
    cold = (
        json.loads(cold_path.read_text())
        if cold_path.exists()
        else {"artifacts": [], "thread_id": thread_id}
    )

    keep, moved = [], []
    skipped_manual = 0
    for entry in hot.get("artifacts", []):
        source = entry.get("source", "legacy_manual")
        if source not in AUTO_SOURCES:
            skipped_manual += 1
            keep.append(entry)
            continue
        last = _parse_ts(entry.get("last_retrieved_at") or entry.get("created_at", ""))
        if last is None or last >= cutoff:
            keep.append(entry)
            continue
        moved.append(entry)

    if not moved:
        return {"moved": 0, "kept": len(keep), "skipped_manual": skipped_manual}

    hot["artifacts"] = keep
    cold.setdefault("artifacts", []).extend(moved)

    with pcp_lock(TINM_PCP_DIR):
        _atomic_write_json(hot_path, hot)
        _atomic_write_json(cold_path, cold)

    return {"moved": len(moved), "kept": len(keep), "skipped_manual": skipped_manual}


def main() -> None:
    p = argparse.ArgumentParser(description="Decay stale auto-captured artifacts.")
    p.add_argument("thread_id")
    p.add_argument("--days", type=int, default=DECAY_DAYS)
    args = p.parse_args()
    result = decay_thread(args.thread_id, days=args.days)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
