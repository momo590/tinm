"""Tests for mvp/scripts/migrate_v023.py.

Covers T11 of the v0.2.3 thread-isolation migration:
  - polluted-seed split (5 canonical + 3 user IDs -> seed + userfork)
  - idempotence (second run is a no-op)
  - pure user thread tagged but not forked
  - pure seed thread (no extras) moved to pcp/seeds/
  - backup tarball is created

The fixture pattern mirrors `tinm_tmp` in test_decay.py: monkeypatch
TINM_HOME + TINM_PCP_DIR to a tmp dir, reload tinm_paths so its
module-level constants pick up the new env, then import migrate_v023.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import shutil
import sys
import tarfile
import tempfile

import pytest


HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL.parent / "scripts"
FIXTURES = HERE / "fixtures"

# Make both skill modules and the migration script importable.
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def tinm_tmp(monkeypatch):
    """Isolated TINM_HOME — each test gets a fresh on-disk state.

    Reloads any tinm_paths / migrate_v023 modules pulled in by a prior
    test so they see the new env. Same pattern as test_decay.py.
    """
    tmp = tempfile.mkdtemp(prefix="tinm-migration-test-")
    tmp_path = pathlib.Path(tmp)
    monkeypatch.setenv("TINM_HOME", str(tmp_path))
    monkeypatch.setenv("TINM_PCP_DIR", str(tmp_path / "pcp"))
    # Drop cached modules so module-level constants re-read env.
    for mod in ("tinm_paths", "tinm_provenance", "migrate_v023"):
        sys.modules.pop(mod, None)
    yield tmp_path
    # Best-effort cleanup; not critical (tmp_path is in /tmp).
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _layout(home: pathlib.Path) -> dict[str, pathlib.Path]:
    pcp = home / "pcp"
    return {
        "home": home,
        "pcp": pcp,
        "threads": pcp / "threads",
        "seeds": pcp / "seeds",
        "artifacts": pcp / "artifacts",
        "backups": home / "backups",
        "notes": home / "migration_notes.json",
        "current": home / "current_thread",
    }


def _seed_canonical_ids() -> list[str]:
    """The 5 canonical IDs of the bundled tinm-tour seed."""
    return [
        "pareto-plot",
        "wiki2hop-results",
        "tinm-substrate",
        "phase1-tokens-saved",
        "pcp-spec",
    ]


def _user_extra_ids() -> list[str]:
    return ["user-fake-1", "user-fake-2", "user-fake-3"]


def _install_polluted_fixture(home: pathlib.Path) -> None:
    """Populate the tmp TINM_HOME with the polluted tinm-tour fixture."""
    layout = _layout(home)
    layout["threads"].mkdir(parents=True, exist_ok=True)
    layout["artifacts"].mkdir(parents=True, exist_ok=True)

    shutil.copyfile(
        FIXTURES / "polluted_tinm_tour.json",
        layout["threads"] / "tinm-tour.json",
    )
    shutil.copyfile(
        FIXTURES / "polluted_tinm_tour_artifacts.json",
        layout["artifacts"] / "tinm-tour.json",
    )
    # Realistic state: a current_thread pointer to the polluted seed
    # (the exact production scenario).
    layout["current"].write_text("tinm-tour")


def _install_pure_user_thread(home: pathlib.Path, thread_id: str = "my-project") -> None:
    layout = _layout(home)
    layout["threads"].mkdir(parents=True, exist_ok=True)
    layout["artifacts"].mkdir(parents=True, exist_ok=True)
    (layout["threads"] / f"{thread_id}.json").write_text(
        json.dumps(
            {
                "pcp_version": "0.1",
                "thread_id": thread_id,
                "metadata": {
                    "title": "Pure user thread",
                    "created_at": "2026-05-14T00:00:00Z",
                    "last_updated": "2026-05-14T00:00:00Z",
                },
                "anchor": {"update_count": 0, "engaged_so_far": False},
                "trajectory": [],
            },
            indent=2,
        )
    )
    (layout["artifacts"] / f"{thread_id}.json").write_text(
        json.dumps(
            {
                "pcp_version": "0.1",
                "thread_id": thread_id,
                "artifacts": [
                    {"id": "design-doc-v1", "name": "Design v1", "source": "manual"},
                    {"id": "perf-bench-q1", "name": "Q1 bench", "source": "manual"},
                ],
            },
            indent=2,
        )
    )


def _install_pure_seed_thread(home: pathlib.Path) -> None:
    """Install a thread that exactly equals the canonical seed (no extras)."""
    layout = _layout(home)
    layout["threads"].mkdir(parents=True, exist_ok=True)
    layout["artifacts"].mkdir(parents=True, exist_ok=True)

    # Re-use the canonical fixture for thread.json, but a freshly-built
    # artifacts.json with only the 5 canonical IDs (no user extras).
    shutil.copyfile(
        FIXTURES / "seed_tinm_tour_canonical.json",
        layout["threads"] / "tinm-tour.json",
    )
    (layout["artifacts"] / "tinm-tour.json").write_text(
        json.dumps(
            {
                "pcp_version": "0.1",
                "thread_id": "tinm-tour",
                "artifacts": [
                    {"id": aid, "name": aid, "source": "seed"}
                    for aid in _seed_canonical_ids()
                ],
            },
            indent=2,
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_polluted_thread_split(tinm_tmp):
    """The headline scenario: 5 canonical + 3 user IDs -> clean split."""
    _install_polluted_fixture(tinm_tmp)
    layout = _layout(tinm_tmp)

    import migrate_v023

    summary = migrate_v023.migrate(verbose=False)
    assert summary["status"] == "success", summary.get("errors")

    # Seed lives at pcp/seeds/tinm-tour.json with exactly the canonical IDs.
    seed_thread_path = layout["seeds"] / "tinm-tour.json"
    seed_artifacts_path = layout["seeds"] / "tinm-tour.artifacts.json"
    assert seed_thread_path.exists()
    assert seed_artifacts_path.exists()

    seed_thread = json.loads(seed_thread_path.read_text())
    assert seed_thread["metadata"]["origin"] == "seed"
    assert "workspace_fingerprint" not in seed_thread["metadata"]

    seed_arts = json.loads(seed_artifacts_path.read_text())
    seed_ids = {a["id"] for a in seed_arts["artifacts"]}
    assert seed_ids == set(_seed_canonical_ids())

    # Userfork lives at pcp/threads/tinm-tour-userfork.json with the 3 user IDs.
    userfork_thread_path = layout["threads"] / "tinm-tour-userfork.json"
    userfork_artifacts_path = layout["artifacts"] / "tinm-tour-userfork.json"
    assert userfork_thread_path.exists()
    assert userfork_artifacts_path.exists()

    userfork_thread = json.loads(userfork_thread_path.read_text())
    assert userfork_thread["metadata"]["origin"] == "user"
    assert "workspace_bridges" in userfork_thread["metadata"]
    assert userfork_thread["thread_id"] == "tinm-tour-userfork"

    userfork_arts = json.loads(userfork_artifacts_path.read_text())
    userfork_ids = {a["id"] for a in userfork_arts["artifacts"]}
    assert userfork_ids == set(_user_extra_ids())

    # Original polluted files moved out of the active layout.
    assert not (layout["threads"] / "tinm-tour.json").exists()
    assert not (layout["artifacts"] / "tinm-tour.json").exists()

    # current_thread retired into backups (not silently deleted).
    assert not layout["current"].exists()
    retired = list(layout["backups"].glob("current_thread-pre-v023-*"))
    assert len(retired) == 1
    assert retired[0].read_text().strip() == "tinm-tour"

    # Quarantined polluted file present in backups.
    polluted_backups = list(layout["backups"].glob("polluted-tinm-tour-*.json"))
    assert len(polluted_backups) == 1
    polluted_bundle = json.loads(polluted_backups[0].read_text())
    assert polluted_bundle["thread"] is not None
    assert polluted_bundle["artifacts"] is not None

    # Tarball backup exists.
    tarballs = list(layout["backups"].glob("pcp-pre-v023-*.tar.gz"))
    assert len(tarballs) == 1

    # Migration notes present + correctly populated.
    notes = json.loads(layout["notes"].read_text())
    assert notes["migration_version"] == "0.2.3"
    assert notes["pre_migration_current_thread"] == "tinm-tour"
    assert len(notes["seed_forks_created"]) == 1
    fork = notes["seed_forks_created"][0]
    assert fork["seed_id"] == "tinm-tour"
    assert fork["userfork_id"] == "tinm-tour-userfork"
    assert fork["source_thread_id"] == "tinm-tour"


def test_idempotent(tinm_tmp):
    """Running migration twice on the same state is a no-op the second time."""
    _install_polluted_fixture(tinm_tmp)
    layout = _layout(tinm_tmp)

    import migrate_v023

    first = migrate_v023.migrate(verbose=False)
    assert first["status"] == "success"

    # Snapshot every file's content after run 1.
    def snapshot(root: pathlib.Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in sorted(root.rglob("*")):
            if p.is_file():
                # Skip the backup tarball (mtime-stamped name) and notes
                # (we expect status=noop on rerun, so notes ARE preserved
                # exactly because the script short-circuits).
                rel = p.relative_to(root).as_posix()
                if rel.startswith("backups/pcp-pre-v023-"):
                    continue
                out[rel] = p.read_text()
        return out

    before = snapshot(tinm_tmp)

    # Force re-import so module-level constants are fresh — the script
    # re-reads its idempotence guard from disk on every call, but caching
    # tinm_paths constants between calls is fine because the env is the
    # same. Still, this models how a user would re-invoke the script.
    sys.modules.pop("migrate_v023", None)
    import migrate_v023 as migrate_v023_second  # noqa: F811

    second = migrate_v023_second.migrate(verbose=False)
    assert second["status"] == "noop"

    after = snapshot(tinm_tmp)
    assert before == after, "second run should produce no on-disk changes"


def test_pure_user_thread_left_alone(tinm_tmp):
    """A thread with no canonical seed overlap gets origin=user, no fork."""
    _install_pure_user_thread(tinm_tmp, "my-project")
    layout = _layout(tinm_tmp)

    import migrate_v023

    summary = migrate_v023.migrate(verbose=False)
    assert summary["status"] == "success"
    assert summary["seed_forks_created"] == []

    thread_path = layout["threads"] / "my-project.json"
    assert thread_path.exists()
    thread = json.loads(thread_path.read_text())
    assert thread["metadata"]["origin"] == "user"
    assert "workspace_bridges" in thread["metadata"]
    # No userfork created.
    assert not (layout["threads"] / "my-project-userfork.json").exists()
    # The seeds directory should not contain my-project.
    if layout["seeds"].exists():
        assert not (layout["seeds"] / "my-project.json").exists()


def test_pure_seed_thread_moved(tinm_tmp):
    """A thread that's EXACTLY the canonical seed is moved to pcp/seeds/."""
    _install_pure_seed_thread(tinm_tmp)
    layout = _layout(tinm_tmp)

    import migrate_v023

    summary = migrate_v023.migrate(verbose=False)
    assert summary["status"] == "success"
    assert "tinm-tour" in summary["pure_seeds_moved"]
    assert summary["seed_forks_created"] == []

    # Seed in place.
    seed_thread_path = layout["seeds"] / "tinm-tour.json"
    seed_artifacts_path = layout["seeds"] / "tinm-tour.artifacts.json"
    assert seed_thread_path.exists()
    assert seed_artifacts_path.exists()

    seed_thread = json.loads(seed_thread_path.read_text())
    assert seed_thread["metadata"]["origin"] == "seed"

    seed_arts = json.loads(seed_artifacts_path.read_text())
    seed_ids = {a["id"] for a in seed_arts["artifacts"]}
    assert seed_ids == set(_seed_canonical_ids())

    # Original thread + artifacts removed from the user namespace.
    assert not (layout["threads"] / "tinm-tour.json").exists()
    assert not (layout["artifacts"] / "tinm-tour.json").exists()

    # No userfork — there was nothing to fork.
    assert not (layout["threads"] / "tinm-tour-userfork.json").exists()


def test_backup_created(tinm_tmp):
    """The tarball backup contains pcp/ + current_thread."""
    _install_polluted_fixture(tinm_tmp)
    layout = _layout(tinm_tmp)

    import migrate_v023

    summary = migrate_v023.migrate(verbose=False)
    backup_path = pathlib.Path(summary["backup_path"])
    assert backup_path.exists()
    assert backup_path.suffix == ".gz"

    with tarfile.open(backup_path, "r:gz") as tar:
        names = tar.getnames()
    # The tarball should contain the pcp/ subtree and current_thread.
    assert any(n == "pcp" or n.startswith("pcp/") for n in names)
    assert "pcp/threads/tinm-tour.json" in names
    assert "pcp/artifacts/tinm-tour.json" in names
    assert "current_thread" in names


def test_no_canonical_seeds_does_not_false_positive(tinm_tmp, monkeypatch):
    """If canonical seeds cannot be discovered, mixed threads stay as user.

    Models a defensive install where the bundled seed dir is missing and
    the hard-coded registry has been stripped. We must not misclassify.
    """
    _install_polluted_fixture(tinm_tmp)
    layout = _layout(tinm_tmp)

    import migrate_v023

    # Empty the canonical registry for this run.
    monkeypatch.setattr(migrate_v023, "_HARDCODED_SEED_IDS", {})
    # Also pretend the bundled seeds dir does not exist by pointing
    # _HERE at the tmp dir (no seeds/ subdir there).
    monkeypatch.setattr(
        migrate_v023, "_discover_canonical_seeds", lambda: {}
    )

    summary = migrate_v023.migrate(verbose=False)
    assert summary["status"] == "success"
    assert summary["seed_forks_created"] == []
    # The thread should still get tagged origin=user (safe default).
    tagged = json.loads(
        (layout["threads"] / "tinm-tour.json").read_text()
    )
    assert tagged["metadata"]["origin"] == "user"
