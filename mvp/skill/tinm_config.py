"""TINM user config — `~/.tinm/config.json`, opinionated key→JSON-value store.

Public defaults documented at the top so end-users + ``tinm_config.py list``
both surface the same truth.

Public API:
    get_config() -> dict
    set_value(key: str, value: Any) -> None
    get(key: str, default=None) -> Any
    DEFAULTS

CLI:
    python tinm_config.py get <key>
    python tinm_config.py set <key> <value>      # value parsed: on/off, true/false,
                                                 # null, ints, then string
    python tinm_config.py list                   # merged defaults + user overrides
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CONFIG_FILE = Path.home() / ".tinm" / "config.json"

# Defaults shown by `list`; user values override via `set`.
DEFAULTS: dict[str, Any] = {
    "update_notify": True,
    "auto_upgrade": False,
}


def _read_raw() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_raw(d: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(CONFIG_FILE)


def get_config() -> dict:
    """Return DEFAULTS merged with the user's overrides."""
    merged = dict(DEFAULTS)
    merged.update(_read_raw())
    return merged


def get(key: str, default: Any = None) -> Any:
    """Single-key accessor. Falls back to DEFAULTS then `default`."""
    cfg = get_config()
    if key in cfg:
        return cfg[key]
    return default


def set_value(key: str, value: Any) -> None:
    raw = _read_raw()
    raw[key] = value
    _write_raw(raw)


def _parse_cli_value(s: str) -> Any:
    """on/off → bool, true/false → bool, null → None, int parsable → int, else str."""
    low = s.strip().lower()
    if low in {"on", "true", "yes"}:
        return True
    if low in {"off", "false", "no"}:
        return False
    if low in {"null", "none"}:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TINM user config — get/set/list.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="Read a single key.")
    g.add_argument("key")

    s = sub.add_parser("set", help="Write a key (on/off/true/false/null/int/float/str).")
    s.add_argument("key")
    s.add_argument("value")

    sub.add_parser("list", help="Print merged defaults + user overrides.")

    args = parser.parse_args(argv)

    if args.cmd == "get":
        v = get(args.key)
        if v is None and args.key not in get_config():
            print(f"<key '{args.key}' not set>", file=sys.stderr)
            return 1
        print(json.dumps(v))
        return 0

    if args.cmd == "set":
        parsed = _parse_cli_value(args.value)
        set_value(args.key, parsed)
        print(f"{args.key} = {json.dumps(parsed)}")
        return 0

    if args.cmd == "list":
        print(json.dumps(get_config(), indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(cli())
