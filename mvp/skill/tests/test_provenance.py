"""Unit tests for tinm_provenance (T8).

Covers the four public functions exposed by tinm_provenance and the
failure modes enumerated in design-thread-isolation-2026-05-17.md:
  - compute_fingerprint: realpath handling, non-existent cwd
  - check_thread_writable: origin=seed refused, fp match/mismatch,
    bridged cwd allowed, missing-fp legacy thread allowed
  - bridge_workspace: adds & persists, idempotent, validation errors
  - resolve_thread_for_cwd: git toplevel match, non-git basename+sha
    fallback, fingerprint tagging on creation, idempotent re-resolution

Cross-host bridge integration (T10) and full session-flow integration
(T9) live in their own files — this is pure unit coverage of the
provenance module itself.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def tinm_tmp(monkeypatch):
    """Isolated TINM_HOME so tests never touch the user's real state."""
    tmp = tempfile.mkdtemp(prefix="tinm-prov-test-")
    monkeypatch.setenv("TINM_HOME", tmp)
    monkeypatch.setenv("TINM_PCP_DIR", str(pathlib.Path(tmp) / "pcp"))
    for mod in ("tinm_paths", "tinm_init", "tinm_provenance"):
        sys.modules.pop(mod, None)
    yield pathlib.Path(tmp)


@pytest.fixture
def workdir():
    """A throwaway directory we can use as a fake $PWD."""
    d = tempfile.mkdtemp(prefix="tinm-prov-work-")
    yield pathlib.Path(d)


def _read_thread(tinm_home: pathlib.Path, thread_id: str) -> dict:
    return json.loads(
        (tinm_home / "pcp" / "threads" / f"{thread_id}.json").read_text()
    )


# ---------------------------------------------------------------------------
# compute_fingerprint
# ---------------------------------------------------------------------------


def test_fp_format_is_sha256_prefixed_32hex(tinm_tmp, workdir):
    from tinm_provenance import compute_fingerprint, FP_HEX_LEN, FP_PREFIX

    fp = compute_fingerprint(str(workdir))
    assert fp is not None
    assert fp.startswith(FP_PREFIX)
    hex_part = fp[len(FP_PREFIX):]
    assert len(hex_part) == FP_HEX_LEN
    int(hex_part, 16)  # raises if not pure hex


def test_fp_deterministic_for_same_path(tinm_tmp, workdir):
    from tinm_provenance import compute_fingerprint

    assert compute_fingerprint(str(workdir)) == compute_fingerprint(str(workdir))


def test_fp_differs_for_different_paths(tinm_tmp, workdir):
    from tinm_provenance import compute_fingerprint

    other = tempfile.mkdtemp(prefix="tinm-prov-other-")
    assert compute_fingerprint(str(workdir)) != compute_fingerprint(other)


def test_fp_none_when_path_missing(tinm_tmp):
    from tinm_provenance import compute_fingerprint

    assert compute_fingerprint("/this/path/does/not/exist/anywhere") is None


def test_fp_resolves_symlinks(tinm_tmp, workdir):
    from tinm_provenance import compute_fingerprint

    link = pathlib.Path(tempfile.mkdtemp(prefix="tinm-prov-link-")) / "alias"
    os.symlink(str(workdir), str(link))
    # Symlink target and the link itself should hash to the same fp
    # because we resolve via realpath (DEC-4).
    assert compute_fingerprint(str(link)) == compute_fingerprint(str(workdir))


def test_fp_defaults_to_getcwd(tinm_tmp, workdir, monkeypatch):
    from tinm_provenance import compute_fingerprint

    monkeypatch.chdir(workdir)
    assert compute_fingerprint() == compute_fingerprint(str(workdir))


# ---------------------------------------------------------------------------
# check_thread_writable
# ---------------------------------------------------------------------------


def test_writable_seed_origin_refused(tinm_tmp):
    from tinm_provenance import check_thread_writable

    thread = {"metadata": {"origin": "seed", "workspace_fingerprint": "sha256:" + "a" * 32}}
    ok, reason = check_thread_writable(thread, "sha256:" + "a" * 32)
    assert ok is False
    assert "seed" in reason.lower()


def test_writable_legacy_no_fingerprint_allowed(tinm_tmp):
    from tinm_provenance import check_thread_writable

    thread = {"metadata": {"origin": "user"}}
    ok, reason = check_thread_writable(thread, "sha256:" + "a" * 32)
    assert ok is True
    assert reason == ""


def test_writable_empty_metadata_allowed(tinm_tmp):
    from tinm_provenance import check_thread_writable

    ok, reason = check_thread_writable({}, "sha256:" + "a" * 32)
    assert ok is True


def test_writable_fp_match_allowed(tinm_tmp):
    from tinm_provenance import check_thread_writable

    fp = "sha256:" + "b" * 32
    thread = {"metadata": {"origin": "user", "workspace_fingerprint": fp}}
    ok, _ = check_thread_writable(thread, fp)
    assert ok is True


def test_writable_fp_mismatch_refused(tinm_tmp):
    from tinm_provenance import check_thread_writable

    thread = {
        "metadata": {
            "origin": "user",
            "workspace_fingerprint": "sha256:" + "b" * 32,
        }
    }
    ok, reason = check_thread_writable(thread, "sha256:" + "c" * 32)
    assert ok is False
    assert "mismatch" in reason.lower()
    assert "bridge" in reason.lower()


def test_writable_bridged_fp_allowed(tinm_tmp):
    from tinm_provenance import check_thread_writable

    canonical = "sha256:" + "b" * 32
    bridged = "sha256:" + "c" * 32
    thread = {
        "metadata": {
            "origin": "user",
            "workspace_fingerprint": canonical,
            "workspace_bridges": [bridged],
        }
    }
    ok, _ = check_thread_writable(thread, bridged)
    assert ok is True


def test_writable_cwd_fp_none_with_stored_fp_refused(tinm_tmp):
    from tinm_provenance import check_thread_writable

    thread = {
        "metadata": {
            "origin": "user",
            "workspace_fingerprint": "sha256:" + "b" * 32,
        }
    }
    ok, reason = check_thread_writable(thread, None)
    assert ok is False
    assert "fingerprint" in reason.lower()


def test_writable_imported_origin_treated_as_user(tinm_tmp):
    from tinm_provenance import check_thread_writable

    fp = "sha256:" + "d" * 32
    thread = {"metadata": {"origin": "imported", "workspace_fingerprint": fp}}
    ok, _ = check_thread_writable(thread, fp)
    assert ok is True


# ---------------------------------------------------------------------------
# bridge_workspace
# ---------------------------------------------------------------------------


def test_bridge_adds_and_persists(tinm_tmp, workdir):
    from tinm_provenance import bridge_workspace, resolve_thread_for_cwd

    thread_id = resolve_thread_for_cwd(str(workdir))
    assert thread_id is not None
    thread_path = tinm_tmp / "pcp" / "threads" / f"{thread_id}.json"

    other_fp = "sha256:" + "e" * 32
    assert bridge_workspace(thread_path, other_fp) is True

    data = json.loads(thread_path.read_text())
    assert other_fp in data["metadata"]["workspace_bridges"]


def test_bridge_idempotent(tinm_tmp, workdir):
    from tinm_provenance import bridge_workspace, resolve_thread_for_cwd

    thread_id = resolve_thread_for_cwd(str(workdir))
    thread_path = tinm_tmp / "pcp" / "threads" / f"{thread_id}.json"

    fp = "sha256:" + "f" * 32
    assert bridge_workspace(thread_path, fp) is True
    assert bridge_workspace(thread_path, fp) is False  # no-op second time

    data = json.loads(thread_path.read_text())
    # Counted exactly once
    assert data["metadata"]["workspace_bridges"].count(fp) == 1


def test_bridge_canonical_fp_is_noop(tinm_tmp, workdir):
    from tinm_provenance import bridge_workspace, resolve_thread_for_cwd, compute_fingerprint

    thread_id = resolve_thread_for_cwd(str(workdir))
    thread_path = tinm_tmp / "pcp" / "threads" / f"{thread_id}.json"

    canonical_fp = compute_fingerprint(str(workdir))
    # Already the canonical workspace — bridging is meaningless
    assert bridge_workspace(thread_path, canonical_fp) is False
    data = json.loads(thread_path.read_text())
    assert canonical_fp not in data["metadata"].get("workspace_bridges", [])


def test_bridge_empty_fp_raises(tinm_tmp, workdir):
    from tinm_provenance import bridge_workspace, resolve_thread_for_cwd

    thread_id = resolve_thread_for_cwd(str(workdir))
    thread_path = tinm_tmp / "pcp" / "threads" / f"{thread_id}.json"

    with pytest.raises(ValueError):
        bridge_workspace(thread_path, "")


def test_bridge_missing_thread_raises(tinm_tmp):
    from tinm_provenance import bridge_workspace

    with pytest.raises(FileNotFoundError):
        bridge_workspace(tinm_tmp / "pcp" / "threads" / "no-such.json", "sha256:" + "0" * 32)


# ---------------------------------------------------------------------------
# resolve_thread_for_cwd
# ---------------------------------------------------------------------------


def test_resolve_non_git_creates_user_thread(tinm_tmp, workdir):
    from tinm_provenance import compute_fingerprint, resolve_thread_for_cwd

    thread_id = resolve_thread_for_cwd(str(workdir))
    assert thread_id is not None
    data = _read_thread(tinm_tmp, thread_id)
    meta = data["metadata"]
    assert meta["origin"] == "user"
    assert meta["workspace_fingerprint"] == compute_fingerprint(str(workdir))
    assert meta["workspace_bridges"] == []


def test_resolve_non_git_slug_has_short_sha(tinm_tmp, workdir):
    """Non-git slugs end with a 6-char hex disambiguator (DEC-3)."""
    from tinm_provenance import resolve_thread_for_cwd

    thread_id = resolve_thread_for_cwd(str(workdir))
    # Last segment after the final hyphen should be 6 hex chars
    last = thread_id.rsplit("-", 1)[-1]
    assert len(last) == 6
    int(last, 16)


def test_resolve_idempotent_for_same_cwd(tinm_tmp, workdir):
    from tinm_provenance import resolve_thread_for_cwd

    a = resolve_thread_for_cwd(str(workdir))
    b = resolve_thread_for_cwd(str(workdir))
    assert a == b
    # Exactly one thread file should exist
    threads = list((tinm_tmp / "pcp" / "threads").glob("*.json"))
    assert len(threads) == 1


def test_resolve_different_cwds_different_threads(tinm_tmp, workdir):
    from tinm_provenance import resolve_thread_for_cwd

    other = tempfile.mkdtemp(prefix="tinm-prov-other-")
    a = resolve_thread_for_cwd(str(workdir))
    b = resolve_thread_for_cwd(other)
    assert a != b
    threads = list((tinm_tmp / "pcp" / "threads").glob("*.json"))
    assert len(threads) == 2


def test_resolve_returns_none_for_missing_cwd(tinm_tmp):
    from tinm_provenance import resolve_thread_for_cwd

    assert resolve_thread_for_cwd("/no/such/path/at/all") is None


def test_resolve_returns_none_for_file_cwd(tinm_tmp, workdir):
    """A path pointing to a file (not a directory) cannot resolve."""
    from tinm_provenance import resolve_thread_for_cwd

    f = workdir / "afile.txt"
    f.write_text("x")
    assert resolve_thread_for_cwd(str(f)) is None


def test_resolve_git_uses_toplevel_basename(tinm_tmp):
    """Inside a git work tree, the slug derives from the git toplevel,
    and metadata.project_root is the toplevel path."""
    from tinm_provenance import resolve_thread_for_cwd

    repo = pathlib.Path(tempfile.mkdtemp(prefix="my-cool-repo-"))
    subprocess.run(
        ["git", "init", "-q", str(repo)], check=True, capture_output=True
    )
    # Create a nested subdir so we exercise "toplevel-not-cwd" derivation
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)

    thread_id = resolve_thread_for_cwd(str(sub))
    assert thread_id is not None
    # Slug = slugified repo basename — strip the mkdtemp suffix tail
    assert thread_id.startswith("my-cool-repo")
    data = _read_thread(tinm_tmp, thread_id)
    assert data["metadata"]["project_root"] == os.path.realpath(str(repo))


def test_resolve_git_reuses_thread_for_same_project_root(tinm_tmp):
    """Two different cwds inside the same git work tree resolve to the
    same thread."""
    from tinm_provenance import resolve_thread_for_cwd

    repo = pathlib.Path(tempfile.mkdtemp(prefix="proj-"))
    subprocess.run(
        ["git", "init", "-q", str(repo)], check=True, capture_output=True
    )
    (repo / "a").mkdir()
    (repo / "b").mkdir()

    from_a = resolve_thread_for_cwd(str(repo / "a"))
    from_b = resolve_thread_for_cwd(str(repo / "b"))
    assert from_a == from_b


def test_resolve_defaults_to_getcwd(tinm_tmp, workdir, monkeypatch):
    from tinm_provenance import resolve_thread_for_cwd

    monkeypatch.chdir(workdir)
    a = resolve_thread_for_cwd()
    b = resolve_thread_for_cwd(str(workdir))
    assert a == b
