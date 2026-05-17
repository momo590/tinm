"""TINM v0.2.3 migration: split polluted seeds + tag origins.

Migrates an existing `~/.tinm/pcp/` store to the v0.2.3 thread-isolation
layout. See design-thread-isolation-2026-05-17.md "Distribution /
migration plan" + DEC-6.

What it does (in order):
  1. Tarballs `~/.tinm/pcp/` to `~/.tinm/backups/pcp-pre-v023-<ts>.tar.gz`.
     Backup-first is non-negotiable: the rest of the script is destructive.
  2. Records pre-migration metadata to `~/.tinm/migration_notes.json`
     (timestamp, hostname, value of old `current_thread`, summary counts).
  3. Walks every thread in `pcp/threads/` and classifies it via
     **artifact_id set intersection** against the canonical seeds known
     to this install (DEC-6 — NOT content hash, since by definition a
     polluted seed has a different content hash than the canonical one).
       - Pure seed (artifact_ids == canonical seed IDs) -> move to
         `pcp/seeds/<seed-id>.json` (+ artifacts).
       - Polluted seed (canonical IDs ⊂ thread IDs AND extras) -> fork:
         write canonical seed to `pcp/seeds/`, write user-added IDs to
         `pcp/threads/<seed-id>-userfork.json` with origin=user, move
         the original to `~/.tinm/backups/polluted-<id>-<ts>.json`.
       - Pure user thread (no canonical seed is a subset of its IDs)
         -> tag in place with `metadata.origin = "user"`.
  4. Backfills `metadata.workspace_fingerprint` best-effort from
     `metadata.project_root` when that path still resolves on this host.
  5. Moves `~/.tinm/current_thread` into the backup directory (Lane C
     hooks no longer read it).
  6. Prints a summary; writes the same summary to
     `~/.tinm/migration_notes.json`.

Idempotence guards:
  - If `migration_notes.json` already records a v0.2.3 completion,
    print summary and exit 2 (no-op).
  - Per-thread: skip any thread whose `metadata.origin` is already set.
  - Per-seed: skip writing `pcp/seeds/<id>.json` if it already exists
    with identical content.

Exit codes: 0 success, 1 partial failure (per-thread errors), 2 no-op.

Usage:
    python mvp/scripts/migrate_v023.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tarfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Bootstrap: make the skill modules importable so we can reuse
# tinm_paths + tinm_provenance without duplicating constants.
_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent / "skill"
sys.path.insert(0, str(_SKILL))

from tinm_paths import (  # noqa: E402
    TINM_HOME,
    TINM_PCP_DIR,
    THREADS_DIR,
    SEEDS_DIR,
    ARTIFACTS_DIR,
    CURRENT_FILE,
)

MIGRATION_VERSION = "0.2.3"
NOTES_FILENAME = "migration_notes.json"
BACKUPS_DIRNAME = "backups"

# ---------------------------------------------------------------------------
# Canonical seed registry
# ---------------------------------------------------------------------------
#
# DEC-6 (artifact_id set intersection) needs an authoritative list of
# canonical seed IDs to compare against. Three sources, in order of
# preference:
#
#   1. The bundled seed directory at `mvp/seeds/<id>/`. This is the
#      source of truth shipped with the repo. We walk
#      `<id>/artifacts.json` and collect every entry's id field.
#   2. If the bundled seed directory is absent (script run from an
#      install where the repo isn't co-located), fall back to a
#      hard-coded registry below. The `tinm-tour` seed is the only one
#      known as of v0.2.3 — keep this list updated when new seeds ship.
#   3. If neither is available, skip the seed-fork step (warn, don't
#      false-positive). Better to leave a thread alone than misclassify.
#
# This script must run on real users' data: prefer skipping over
# guessing.

_HARDCODED_SEED_IDS: dict[str, list[str]] = {
    "tinm-tour": [
        "pareto-plot",
        "wiki2hop-results",
        "tinm-substrate",
        "phase1-tokens-saved",
        "pcp-spec",
    ],
}


def _discover_canonical_seeds() -> dict[str, set[str]]:
    """Return {seed_id: {canonical artifact_id, ...}}.

    Tries the bundled seed directory first, then falls back to the
    hard-coded registry. Order matters because a seed may have evolved
    in the repo and the install's hard-coded list lags behind.
    """
    seeds: dict[str, set[str]] = {}

    # 1) Bundled seeds at mvp/seeds/<id>/artifacts.json
    repo_seeds_dir = _HERE.parent / "seeds"
    if repo_seeds_dir.is_dir():
        for sub in sorted(repo_seeds_dir.iterdir()):
            if not sub.is_dir():
                continue
            artifacts_file = sub / "artifacts.json"
            if not artifacts_file.is_file():
                continue
            try:
                data = json.loads(artifacts_file.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            ids = _collect_artifact_ids(data.get("artifacts") or [])
            if ids:
                seeds[sub.name] = ids

    # 2) Hard-coded fallback for installs without the repo
    for seed_id, ids in _HARDCODED_SEED_IDS.items():
        seeds.setdefault(seed_id, set(ids))

    return seeds


def _collect_artifact_ids(artifacts: Iterable[dict]) -> set[str]:
    """Pull `id` or `artifact_id` (whichever exists) from each entry."""
    out: set[str] = set()
    for a in artifacts:
        if not isinstance(a, dict):
            continue
        aid = a.get("id") or a.get("artifact_id")
        if isinstance(aid, str) and aid:
            out.add(aid)
    return out


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _utc_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _backups_dir() -> Path:
    p = TINM_HOME / BACKUPS_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _notes_path() -> Path:
    return TINM_HOME / NOTES_FILENAME


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _artifacts_path(thread_id: str) -> Path:
    return ARTIFACTS_DIR / f"{thread_id}.json"


def _thread_path(thread_id: str) -> Path:
    return THREADS_DIR / f"{thread_id}.json"


def _seed_thread_path(seed_id: str) -> Path:
    return SEEDS_DIR / f"{seed_id}.json"


def _seed_artifacts_path(seed_id: str) -> Path:
    # Mirrors the convention established by tinm_demo.py: artifacts
    # for a seed live alongside the seed thread in pcp/seeds/.
    return SEEDS_DIR / f"{seed_id}.artifacts.json"


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def make_backup() -> Path:
    """Tarball ~/.tinm/pcp/ into ~/.tinm/backups/pcp-pre-v023-<ts>.tar.gz.

    Always runs before any destructive operation. If pcp/ does not exist
    (fresh install — nothing to migrate), creates an empty backup
    placeholder so the migration_notes path is still informative.
    """
    backups = _backups_dir()
    ts = _utc_iso8601()
    out = backups / f"pcp-pre-v023-{ts}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        if TINM_PCP_DIR.exists():
            tar.add(TINM_PCP_DIR, arcname="pcp")
        if CURRENT_FILE.exists():
            tar.add(CURRENT_FILE, arcname="current_thread")
    return out


# ---------------------------------------------------------------------------
# Migration metadata (idempotence anchor)
# ---------------------------------------------------------------------------


def _already_migrated() -> dict | None:
    notes = _load_json(_notes_path())
    if not notes:
        return None
    if notes.get("migration_version") == MIGRATION_VERSION and notes.get(
        "completed_at"
    ):
        return notes
    return None


# ---------------------------------------------------------------------------
# Classification (DEC-6)
# ---------------------------------------------------------------------------


def classify_thread(
    thread_ids: set[str], canonical_seeds: dict[str, set[str]]
) -> tuple[str, str | None]:
    """Return (classification, matched_seed_id_or_None).

    classification is one of:
      - "pure_seed"      thread_ids == canonical seed IDs
      - "polluted_seed"  thread_ids ⊃ canonical seed IDs (strict)
      - "user"           no canonical seed is a subset of thread_ids
    """
    if not thread_ids:
        # No artifacts at all — treat as a fresh user thread.
        return "user", None

    for seed_id, canon in canonical_seeds.items():
        if not canon:
            continue
        if canon == thread_ids:
            return "pure_seed", seed_id
        if canon.issubset(thread_ids):
            return "polluted_seed", seed_id
    return "user", None


# ---------------------------------------------------------------------------
# Per-thread actions
# ---------------------------------------------------------------------------


def _set_origin_user(thread_data: dict, fingerprint: str | None) -> bool:
    """Patch metadata.origin = 'user' + fingerprint. Returns True if dirty.

    The fingerprint check uses `not meta.get(...)` so a stored value of
    `None` (legacy/partial backfill) is still overwritten when a real
    fingerprint becomes computable — `"workspace_fingerprint" not in meta`
    alone would skip those entries forever.
    """
    meta = thread_data.setdefault("metadata", {})
    dirty = False
    if meta.get("origin") != "user":
        meta["origin"] = "user"
        dirty = True
    if not meta.get("workspace_fingerprint") and fingerprint is not None:
        meta["workspace_fingerprint"] = fingerprint
        dirty = True
    elif "workspace_fingerprint" not in meta:
        meta["workspace_fingerprint"] = None
        dirty = True
    if "workspace_bridges" not in meta:
        meta["workspace_bridges"] = []
        dirty = True
    return dirty


def _backfill_fingerprint(thread_data: dict) -> str | None:
    """Best-effort: derive fingerprint from metadata.project_root.

    Returns the fingerprint string or None if it can't be computed.
    Imports tinm_provenance lazily so the script still runs in
    environments where embedding deps (loaded transitively by
    tinm_init -> ...) are absent.
    """
    meta = thread_data.get("metadata") or {}
    project_root = meta.get("project_root")
    if not project_root or not isinstance(project_root, str):
        return None
    try:
        from tinm_provenance import compute_fingerprint
    except Exception:
        return None
    try:
        return compute_fingerprint(project_root)
    except Exception:
        return None


def _write_seed(
    seed_id: str,
    thread_data: dict,
    artifacts_data: dict,
    canonical_ids: set[str],
) -> bool:
    """Write canonical seed to pcp/seeds/. Idempotent.

    Returns True if a write happened, False if the seed was already
    present with identical canonical content.
    """
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    seed_thread_payload = json.loads(json.dumps(thread_data))  # deep copy
    seed_meta = seed_thread_payload.setdefault("metadata", {})
    seed_meta["origin"] = "seed"
    # Seeds belong to no workspace by design — strip any fingerprint.
    seed_meta.pop("workspace_fingerprint", None)
    seed_meta.pop("workspace_bridges", None)
    seed_thread_payload["thread_id"] = seed_id

    # Filter artifacts down to the canonical set.
    canon_artifacts = [
        a
        for a in (artifacts_data.get("artifacts") or [])
        if (a.get("id") or a.get("artifact_id")) in canonical_ids
    ]
    seed_artifacts_payload = {
        "pcp_version": artifacts_data.get("pcp_version", "0.1"),
        "thread_id": seed_id,
        "artifacts": canon_artifacts,
    }

    seed_thread_path = _seed_thread_path(seed_id)
    seed_artifacts_path = _seed_artifacts_path(seed_id)

    # Per-seed idempotence guard: if the file is already there with the
    # same canonical id set, skip.
    if seed_thread_path.exists() and seed_artifacts_path.exists():
        existing = _load_json(seed_artifacts_path) or {}
        existing_ids = _collect_artifact_ids(existing.get("artifacts") or [])
        if existing_ids == canonical_ids:
            return False

    _atomic_write_json(seed_thread_path, seed_thread_payload)
    _atomic_write_json(seed_artifacts_path, seed_artifacts_payload)
    return True


def _write_userfork(
    seed_id: str,
    thread_data: dict,
    artifacts_data: dict,
    canonical_ids: set[str],
    fingerprint: str | None,
) -> Path:
    """Write user-added artifacts to pcp/threads/<seed-id>-userfork.json.

    The userfork inherits trajectory + anchor + top_terms from the
    polluted thread (best-effort — user's accumulated state belongs
    with the user's artifacts, not the canonical seed).
    """
    userfork_id = f"{seed_id}-userfork"
    userfork_thread_path = _thread_path(userfork_id)
    userfork_artifacts_path = _artifacts_path(userfork_id)

    fork_thread = json.loads(json.dumps(thread_data))  # deep copy
    fork_thread["thread_id"] = userfork_id
    fork_meta = fork_thread.setdefault("metadata", {})
    fork_meta["origin"] = "user"
    # Backfill is best-effort; hooks will set on first write if None.
    fork_meta["workspace_fingerprint"] = fingerprint
    fork_meta.setdefault("workspace_bridges", [])
    note = (
        f"Forked from polluted seed `{seed_id}` during v0.2.3 migration. "
        "Contains only the artifacts you added on top of the canonical seed."
    )
    fork_meta["migration_note"] = note

    user_artifacts = [
        a
        for a in (artifacts_data.get("artifacts") or [])
        if (a.get("id") or a.get("artifact_id")) not in canonical_ids
    ]
    fork_artifacts = {
        "pcp_version": artifacts_data.get("pcp_version", "0.1"),
        "thread_id": userfork_id,
        "artifacts": user_artifacts,
    }

    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(userfork_thread_path, fork_thread)
    _atomic_write_json(userfork_artifacts_path, fork_artifacts)
    return userfork_thread_path


def _quarantine_polluted(thread_id: str, ts: str) -> Path | None:
    """Move the original polluted thread + artifacts files into the backup.

    Returns the polluted backup path or None if nothing was moved.
    """
    backups = _backups_dir()
    out = backups / f"polluted-{thread_id}-{ts}.json"
    bundle: dict[str, dict | None] = {
        "thread": None,
        "artifacts": None,
    }
    tpath = _thread_path(thread_id)
    apath = _artifacts_path(thread_id)
    moved = False
    if tpath.exists():
        bundle["thread"] = _load_json(tpath)
        tpath.unlink()
        moved = True
    if apath.exists():
        bundle["artifacts"] = _load_json(apath)
        apath.unlink()
        moved = True
    if not moved:
        return None
    _atomic_write_json(out, bundle)
    return out


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def migrate(verbose: bool = True) -> dict:
    """Run the full v0.2.3 migration.

    Returns a summary dict (also written to migration_notes.json).
    """
    summary: dict = {
        "migration_version": MIGRATION_VERSION,
        "hostname": socket.gethostname(),
        "started_at": _utc_iso8601(),
        "tinm_home": str(TINM_HOME),
        "tinm_pcp_dir": str(TINM_PCP_DIR),
    }

    # Idempotence: full-script guard.
    already = _already_migrated()
    if already:
        summary.update(already)
        summary["status"] = "noop"
        if verbose:
            print(
                f"[migrate-v023] already completed at "
                f"{already.get('completed_at')} on host {already.get('hostname')} "
                "— no-op."
            )
        return summary

    # Step 1: backup
    backup_path = make_backup()
    summary["backup_path"] = str(backup_path)
    if verbose:
        print(f"[migrate-v023] backup written: {backup_path}")

    # Step 2: capture pre-migration current_thread (for the user's notes)
    current_thread_value: str | None = None
    if CURRENT_FILE.exists():
        try:
            current_thread_value = CURRENT_FILE.read_text().strip() or None
        except OSError:
            current_thread_value = None
    summary["pre_migration_current_thread"] = current_thread_value

    # Discover canonical seeds
    canonical_seeds = _discover_canonical_seeds()
    summary["canonical_seeds_known"] = {
        sid: sorted(list(ids)) for sid, ids in canonical_seeds.items()
    }
    if not canonical_seeds and verbose:
        print(
            "[migrate-v023] WARNING: no canonical seeds discovered — "
            "seed-fork step will be skipped."
        )

    # Step 3 + 4: walk threads
    ts = _utc_iso8601()
    threads_processed = 0
    origins_assigned = 0
    seed_forks_created: list[dict] = []
    pure_seeds_moved: list[str] = []
    fingerprints_backfilled = 0
    errors: list[str] = []

    if not THREADS_DIR.exists():
        if verbose:
            print(
                f"[migrate-v023] {THREADS_DIR} does not exist — nothing to migrate."
            )
    else:
        for tpath in sorted(THREADS_DIR.glob("*.json")):
            thread_id = tpath.stem
            try:
                thread_data = _load_json(tpath)
                if thread_data is None:
                    errors.append(f"{thread_id}: unreadable JSON")
                    continue
                threads_processed += 1

                # Per-thread idempotence: skip if origin already set.
                existing_origin = (thread_data.get("metadata") or {}).get(
                    "origin"
                )

                artifacts_data = _load_json(_artifacts_path(thread_id)) or {
                    "artifacts": []
                }
                thread_ids = _collect_artifact_ids(
                    artifacts_data.get("artifacts") or []
                )

                classification, seed_id = classify_thread(
                    thread_ids, canonical_seeds
                )

                # Fingerprint backfill (works for all classifications).
                fingerprint = _backfill_fingerprint(thread_data)

                # Already classified → skip reclassification. This protects
                # against re-running the script accidentally turning a
                # user-confirmed user thread back into a seed-fork.
                if existing_origin in ("seed", "user", "imported"):
                    if verbose:
                        print(
                            f"[migrate-v023] {thread_id}: already tagged "
                            f"origin={existing_origin} — skipping"
                        )
                    continue

                if (
                    classification == "pure_seed"
                    and seed_id is not None
                    and canonical_seeds.get(seed_id)
                ):
                    canonical_ids = canonical_seeds[seed_id]
                    wrote = _write_seed(
                        seed_id, thread_data, artifacts_data, canonical_ids
                    )
                    # Remove from threads/ + artifacts/ since it now lives in seeds/.
                    if tpath.exists():
                        tpath.unlink()
                    apath = _artifacts_path(thread_id)
                    if apath.exists():
                        apath.unlink()
                    if wrote:
                        pure_seeds_moved.append(seed_id)
                    if verbose:
                        print(
                            f"[migrate-v023] {thread_id}: pure seed -> "
                            f"pcp/seeds/{seed_id}.json"
                        )

                elif (
                    classification == "polluted_seed"
                    and seed_id is not None
                    and canonical_seeds.get(seed_id)
                ):
                    canonical_ids = canonical_seeds[seed_id]
                    _write_seed(
                        seed_id, thread_data, artifacts_data, canonical_ids
                    )
                    userfork_path = _write_userfork(
                        seed_id,
                        thread_data,
                        artifacts_data,
                        canonical_ids,
                        fingerprint,
                    )
                    quarantined = _quarantine_polluted(thread_id, ts)
                    seed_forks_created.append(
                        {
                            "source_thread_id": thread_id,
                            "seed_id": seed_id,
                            "userfork_id": userfork_path.stem,
                            "quarantined_backup": str(quarantined)
                            if quarantined
                            else None,
                        }
                    )
                    if fingerprint is not None:
                        fingerprints_backfilled += 1
                    if verbose:
                        n_user = len(thread_ids - canonical_ids)
                        print(
                            f"[migrate-v023] {thread_id}: polluted seed split "
                            f"-> seeds/{seed_id}.json + "
                            f"threads/{userfork_path.stem}.json "
                            f"({n_user} user artifacts forked)"
                        )

                else:
                    # Plain user thread — tag in place.
                    dirty = _set_origin_user(thread_data, fingerprint)
                    if dirty:
                        _atomic_write_json(tpath, thread_data)
                        origins_assigned += 1
                        if fingerprint is not None:
                            fingerprints_backfilled += 1
                        if verbose:
                            print(
                                f"[migrate-v023] {thread_id}: tagged "
                                f"origin=user"
                                + (
                                    f" + fingerprint backfilled"
                                    if fingerprint is not None
                                    else ""
                                )
                            )

            except Exception as exc:  # noqa: BLE001
                errors.append(f"{thread_id}: {exc}\n{traceback.format_exc()}")
                if verbose:
                    print(
                        f"[migrate-v023] ERROR processing {thread_id}: {exc}",
                        file=sys.stderr,
                    )

    # Step 5: retire current_thread (move into the backup dir, don't delete
    # outright — gives the user a recovery path if they need to look it up
    # manually).
    if CURRENT_FILE.exists():
        retired = _backups_dir() / f"current_thread-pre-v023-{ts}"
        try:
            shutil.move(str(CURRENT_FILE), str(retired))
            summary["retired_current_thread_to"] = str(retired)
            if verbose:
                print(
                    f"[migrate-v023] retired {CURRENT_FILE} -> {retired}"
                )
        except OSError as exc:
            errors.append(f"current_thread retire failed: {exc}")

    # Step 5b: sweep orphan per-session handoff files (Lane C hooks write
    # `~/.tinm/session-<id>.thread` at SessionStart, delete at stop.sh).
    # Any leftover from crashed/killed prior sessions would keep firing
    # the legacy fallback in user_prompt.sh forever — retire them too.
    swept: list[str] = []
    for orphan in TINM_HOME.glob("session-*.thread"):
        try:
            target = _backups_dir() / f"{orphan.name}-pre-v023-{ts}"
            shutil.move(str(orphan), str(target))
            swept.append(orphan.name)
        except OSError as exc:
            errors.append(f"session handoff sweep failed for {orphan.name}: {exc}")
    if swept:
        summary["retired_session_handoffs"] = swept
        if verbose:
            print(f"[migrate-v023] swept {len(swept)} stale session-*.thread file(s)")

    # Step 6: finalize notes
    summary["completed_at"] = _utc_iso8601()
    summary["threads_processed"] = threads_processed
    summary["origins_assigned"] = origins_assigned
    summary["seed_forks_created"] = seed_forks_created
    summary["pure_seeds_moved"] = pure_seeds_moved
    summary["fingerprints_backfilled"] = fingerprints_backfilled
    summary["errors"] = errors
    summary["status"] = "partial_failure" if errors else "success"

    TINM_HOME.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(_notes_path(), summary)

    if verbose:
        print()
        print("[migrate-v023] ─── summary ─────────────────────────")
        print(f"  backup:                  {summary['backup_path']}")
        print(f"  threads processed:       {threads_processed}")
        print(f"  origins assigned (user): {origins_assigned}")
        print(f"  pure seeds moved:        {len(pure_seeds_moved)}")
        print(
            f"  seed-forks created:      "
            f"{len(seed_forks_created)} "
            + (
                "("
                + ", ".join(s["source_thread_id"] for s in seed_forks_created)
                + ")"
                if seed_forks_created
                else ""
            )
        )
        print(f"  fingerprints backfilled: {fingerprints_backfilled}")
        print(f"  errors:                  {len(errors)}")
        print(f"  notes written to:        {_notes_path()}")
        print()

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress per-thread progress lines",
    )
    args = parser.parse_args(argv)

    summary = migrate(verbose=not args.quiet)
    status = summary.get("status")
    if status == "noop":
        return 2
    if status == "partial_failure":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
