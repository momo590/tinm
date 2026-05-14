"""Tests for tinm_update_check — semver, cache TTL, silent on network error."""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

import tinm_update_check as uc  # noqa: E402


# ---------------------------------------------------------------------------
# semver
# ---------------------------------------------------------------------------

def test_semver_gt_basic():
    assert uc._semver_gt("1.0.0", "0.9.0") is True
    assert uc._semver_gt("0.2.0", "0.1.9") is True
    assert uc._semver_gt("0.1.1", "0.1.0") is True


def test_semver_gt_strict_less_than():
    assert uc._semver_gt("0.1.0", "0.2.0") is False
    assert uc._semver_gt("0.1.0", "1.0.0") is False


def test_semver_gt_equal_is_false():
    assert uc._semver_gt("0.1.0", "0.1.0") is False


def test_semver_gt_malformed_defensive():
    # Should not raise — malformed input falls back to (0,)
    assert uc._semver_gt("not-a-version", "0.1.0") is False
    assert uc._semver_gt("0.1.0", "also-bad") is True


# ---------------------------------------------------------------------------
# get_local_version
# ---------------------------------------------------------------------------

def test_get_local_version_reads_file(monkeypatch, tmp_path):
    vfile = tmp_path / "VERSION"
    vfile.write_text("9.9.9\n")
    monkeypatch.setattr(uc, "LOCAL_VERSION_FILE", vfile)
    assert uc.get_local_version() == "9.9.9"


def test_get_local_version_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(uc, "LOCAL_VERSION_FILE", tmp_path / "DOES_NOT_EXIST")
    assert uc.get_local_version() == "unknown"


# ---------------------------------------------------------------------------
# check_for_update — cache + network behavior
# ---------------------------------------------------------------------------

def _setup(monkeypatch, tmp_path, local_version="0.1.0"):
    vfile = tmp_path / "VERSION"
    vfile.write_text(local_version + "\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(uc, "LOCAL_VERSION_FILE", vfile)
    monkeypatch.setattr(uc, "CACHE_FILE", cache)
    return cache


def test_check_happy_path_update_available(monkeypatch, tmp_path):
    cache = _setup(monkeypatch, tmp_path, local_version="0.1.0")
    monkeypatch.setattr(uc, "_fetch_remote_version", lambda: "0.2.0")
    r = uc.check_for_update()
    assert r["current"] == "0.1.0"
    assert r["latest"] == "0.2.0"
    assert r["has_update"] is True
    assert r["source"] == "remote"
    # Cache file should have been written.
    cached = json.loads(cache.read_text())
    assert cached["has_update"] is True


def test_check_happy_path_already_latest(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, local_version="0.2.0")
    monkeypatch.setattr(uc, "_fetch_remote_version", lambda: "0.2.0")
    r = uc.check_for_update()
    assert r["has_update"] is False
    assert r["source"] == "remote"


def test_cache_respects_ttl(monkeypatch, tmp_path):
    cache = _setup(monkeypatch, tmp_path, local_version="0.1.0")
    # Pre-populate cache with a "fresh" entry from now-1s.
    cache.write_text(json.dumps({
        "current": "0.1.0",
        "latest": "0.5.0",
        "has_update": True,
        "ts": int(time.time()) - 1,
    }))
    # Wire fetch to raise — should NOT be called because cache is fresh.
    def boom():
        raise AssertionError("network was hit despite valid cache")
    monkeypatch.setattr(uc, "_fetch_remote_version", boom)
    r = uc.check_for_update()
    assert r["source"] == "cache"
    assert r["has_update"] is True


def test_cache_expires_after_ttl(monkeypatch, tmp_path):
    cache = _setup(monkeypatch, tmp_path, local_version="0.1.0")
    # Stale cache: ts is older than TTL.
    cache.write_text(json.dumps({
        "current": "0.1.0",
        "latest": "0.5.0",
        "has_update": True,
        "ts": int(time.time()) - uc.CACHE_TTL_S - 100,
    }))
    monkeypatch.setattr(uc, "_fetch_remote_version", lambda: "0.3.0")
    r = uc.check_for_update()
    assert r["source"] == "remote"
    assert r["latest"] == "0.3.0"


def test_force_bypasses_cache(monkeypatch, tmp_path):
    cache = _setup(monkeypatch, tmp_path, local_version="0.1.0")
    cache.write_text(json.dumps({
        "current": "0.1.0",
        "latest": "0.5.0",
        "has_update": True,
        "ts": int(time.time()),
    }))
    monkeypatch.setattr(uc, "_fetch_remote_version", lambda: "0.9.0")
    r = uc.check_for_update(force=True)
    assert r["source"] == "remote"
    assert r["latest"] == "0.9.0"


def test_silent_on_network_error(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, local_version="0.1.0")

    def raises(*_args, **_kwargs):
        raise urllib.request.URLError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", raises)
    r = uc.check_for_update(force=True)
    # Must NOT raise. Must return error source + has_update=False.
    assert r["has_update"] is False
    assert r["source"] == "error"
    assert r["latest"] is None
    assert r["current"] == "0.1.0"
