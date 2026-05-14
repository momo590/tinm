"""TINM update check — gstack-style notification, 24h cache, silent on error.

Reads the local `mvp/VERSION` (shipped alongside this file) and compares
to the remote `mvp/VERSION` fetched from the public repo. Decision
record: the check is best-effort. Network failure must NEVER block a
SessionStart hook — silent fall-through, return `has_update=False`.

Schema of the cache file (`~/.tinm/.update-check.json`):
    {"current": "0.1.0", "latest": "0.1.0", "has_update": false,
     "ts": 1737937420, "source": "remote"}

Public API:
    get_local_version() -> str        # reads mvp/VERSION next to this file
    check_for_update(force=False) -> dict
    cli(args=None) -> int             # printable status (CLI entry)

CLI:
    python tinm_update_check.py            # cached status (defaults to TTL)
    python tinm_update_check.py --force    # bypass cache, hit remote
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

UPSTREAM_VERSION_URL = "https://raw.githubusercontent.com/momo590/tinm/main/mvp/VERSION"
CACHE_FILE = Path.home() / ".tinm" / ".update-check.json"
CACHE_TTL_S = 86400  # 24h

# VERSION ships next to mvp/ — this file is mvp/skill/tinm_update_check.py
# so mvp/ is one level up from this file's parent.
LOCAL_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

NETWORK_TIMEOUT_S = 3.0


def get_local_version() -> str:
    """Read mvp/VERSION shipped with this install. Returns 'unknown' if missing."""
    try:
        return LOCAL_VERSION_FILE.read_text().strip()
    except OSError:
        return "unknown"


def _fetch_remote_version() -> str | None:
    """Hit raw.githubusercontent.com for the upstream VERSION. None on error."""
    try:
        with urllib.request.urlopen(UPSTREAM_VERSION_URL, timeout=NETWORK_TIMEOUT_S) as r:
            return r.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return None


def _read_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(d: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(d))
    except OSError:
        # Telemetry / update-check must never break user flow on disk error.
        pass


def _semver_gt(a: str, b: str) -> bool:
    """True iff a > b in (major, minor, patch) order. Malformed → False side."""
    def parts(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return (0,)
    return parts(a) > parts(b)


def check_for_update(force: bool = False) -> dict:
    """Returns {current, latest, has_update, source: 'cache'|'remote'|'error'}.

    Source legend:
        cache  — TTL still valid, no network call performed
        remote — fresh network fetch (or first run)
        error  — network failed; has_update will be False
    """
    cached = _read_cache()
    if not force and cached.get("ts", 0) + CACHE_TTL_S > time.time():
        return {
            "current": cached.get("current", get_local_version()),
            "latest": cached.get("latest"),
            "has_update": cached.get("has_update", False),
            "ts": cached.get("ts"),
            "source": "cache",
        }

    current = get_local_version()
    latest = _fetch_remote_version()
    if latest is None:
        return {
            "current": current,
            "latest": None,
            "has_update": False,
            "source": "error",
        }

    has_update = _semver_gt(latest, current)
    result = {
        "current": current,
        "latest": latest,
        "has_update": has_update,
        "ts": int(time.time()),
        "source": "remote",
    }
    _write_cache(result)
    return result


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check TINM update status.")
    parser.add_argument("--force", action="store_true",
                        help="Bypass the 24h cache and hit the remote.")
    args = parser.parse_args(argv)
    r = check_for_update(force=args.force)
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(cli())
