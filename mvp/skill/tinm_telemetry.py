"""
TINM self-instrumented telemetry — measure the value TINM actually generates.

Wedge: identified 2026-05-14 turn 71 (Mouhamadou). See memory
[[tinm-telemetry-wedge]]. Must ship with v0.2, BEFORE Jordan's first install,
so his first session already produces datapoints.

Privacy contract (NON-NEGOTIABLE):
  - Opt-in explicit at install time. Default = OFF.
  - Local-first. JSONL at ~/.tinm/telemetry.jsonl. Never uploaded by default.
  - Mode "share" requires explicit `tinm telemetry on --share` — uploads
    only aggregates (counts, sums, percentiles), never raw events with content.
  - No content capture. Ever. No prompts, no file contents, no chat text.
    Only event_type + numeric/categorical metadata.
  - Always revocable: `tinm telemetry off` stops new events and offers
    `tinm telemetry purge` to delete the local file.

5 events for v0.2:
  cross_session_hit       — artifact_find returned a used result
  tokens_saved_estimated  — bytes/tokens TINM injected vs hypothetical re-paste
  digest_injection        — digest fired (v0.2, wired in Wave 2)
  latency_added_ms        — hook overhead per event-type
  user_explicit_action    — /tinm pin | save | status invoked

Schema is versioned. Bump SCHEMA_VERSION on any change.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1
TELEMETRY_FILE = Path.home() / ".tinm" / "telemetry.jsonl"
CONFIG_FILE = Path.home() / ".tinm" / "telemetry.config.json"

EventType = Literal[
    "cross_session_hit",
    "tokens_saved_estimated",
    "digest_injection",
    "latency_added_ms",
    "user_explicit_action",
    "approval_signal_classified",
    "assistant_turn_captured",
]

EVENT_FIELDS: dict[str, set[str]] = {
    "cross_session_hit": {"thread_id", "k_results", "top_score"},
    "tokens_saved_estimated": {"tokens_injected", "tokens_avoided_estimated", "source"},
    "digest_injection": {"turns_compressed", "digest_tokens", "trigger_token_count"},
    "latency_added_ms": {"hook", "duration_ms"},
    "user_explicit_action": {"action"},
    "approval_signal_classified": {"label", "signal_w", "thread_id"},
    "assistant_turn_captured": {"decision", "signal_w", "thread_id"},
}

ALLOWED_PAYLOAD_TYPES = (str, int, float, bool, type(None))


# ---------------------------------------------------------------------------
# Config & opt-in state
# ---------------------------------------------------------------------------

def _read_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {"enabled": False, "share_aggregates": False, "install_id": None}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"enabled": False, "share_aggregates": False, "install_id": None}


def _write_config(cfg: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def is_enabled() -> bool:
    return _read_config().get("enabled", False) is True


def enable(*, share_aggregates: bool = False) -> None:
    cfg = _read_config()
    cfg["enabled"] = True
    cfg["share_aggregates"] = bool(share_aggregates)
    if not cfg.get("install_id"):
        cfg["install_id"] = uuid.uuid4().hex[:12]
    _write_config(cfg)


def disable() -> None:
    cfg = _read_config()
    cfg["enabled"] = False
    cfg["share_aggregates"] = False
    _write_config(cfg)


def purge() -> None:
    if TELEMETRY_FILE.exists():
        TELEMETRY_FILE.unlink()


def status() -> dict[str, Any]:
    cfg = _read_config()
    event_count = 0
    if TELEMETRY_FILE.exists():
        with TELEMETRY_FILE.open() as f:
            event_count = sum(1 for _ in f)
    return {
        "enabled": cfg.get("enabled", False),
        "share_aggregates": cfg.get("share_aggregates", False),
        "install_id": cfg.get("install_id"),
        "events_logged": event_count,
        "log_path": str(TELEMETRY_FILE),
    }


# ---------------------------------------------------------------------------
# Event logging
# ---------------------------------------------------------------------------

def _validate_payload(event_type: EventType, payload: dict[str, Any]) -> dict[str, Any]:
    """Enforce schema + drop any field not in the event's allowlist.

    Raises ValueError if event_type is not in EVENT_FIELDS (programming
    error — surfaced loudly so tests catch typos). Unknown FIELDS within a
    valid event are silently dropped (defense in depth against accidental
    leakage of new payload keys).
    """
    allowed = EVENT_FIELDS.get(event_type)
    if allowed is None:
        raise ValueError(f"unknown event_type: {event_type}")
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k not in allowed:
            continue
        if not isinstance(v, ALLOWED_PAYLOAD_TYPES):
            continue
        if isinstance(v, str) and len(v) > 64:
            v = v[:64]
        out[k] = v
    return out


def log_event(event_type: EventType, payload: dict[str, Any]) -> None:
    """Append an event to the local JSONL. No-op if telemetry is disabled.

    IO failures (disk full, permission denied) are swallowed silently —
    telemetry must NEVER break user flow. Schema/typing errors (unknown
    event_type) DO raise so bugs in the wiring layer surface in tests.
    """
    if not is_enabled():
        return
    safe_payload = _validate_payload(event_type, payload)
    entry = {
        "v": SCHEMA_VERSION,
        "ts": time.time(),
        "type": event_type,
        "install_id": _read_config().get("install_id"),
        **safe_payload,
    }
    try:
        TELEMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with TELEMETRY_FILE.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


@contextmanager
def measure_latency(hook_name: str):
    """Context manager that logs latency_added_ms for the wrapped block.

    Always logs on exit (success or exception). Latency is recorded even
    if telemetry is disabled at exit time — `log_event` handles the gate.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        log_event("latency_added_ms", {"hook": hook_name, "duration_ms": duration_ms})


# ---------------------------------------------------------------------------
# Aggregation & export
# ---------------------------------------------------------------------------

def export_aggregates() -> dict[str, Any]:
    """Compute aggregates suitable for sharing — never raw events.

    Output contains: counts per event_type, latency p50/p95 per hook,
    sums of `tokens_avoided_estimated`, action-name frequency. No
    `thread_id`, no raw payloads, no timestamps.
    """
    if not TELEMETRY_FILE.exists():
        return {"events_total": 0}

    counts: dict[str, int] = {}
    tokens_saved_total = 0
    digest_count = 0
    digest_turns_compressed_total = 0
    cross_session_hits = 0
    latency_samples: dict[str, list[float]] = {}
    user_actions: dict[str, int] = {}

    with TELEMETRY_FILE.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type", "?")
            counts[t] = counts.get(t, 0) + 1

            if t == "tokens_saved_estimated":
                tokens_saved_total += int(ev.get("tokens_avoided_estimated", 0))
            elif t == "digest_injection":
                digest_count += 1
                digest_turns_compressed_total += int(ev.get("turns_compressed", 0))
            elif t == "cross_session_hit":
                cross_session_hits += 1
            elif t == "latency_added_ms":
                latency_samples.setdefault(ev.get("hook", "?"), []).append(
                    float(ev.get("duration_ms", 0))
                )
            elif t == "user_explicit_action":
                action = ev.get("action", "?")
                user_actions[action] = user_actions.get(action, 0) + 1

    def _percentile(vals: list[float], p: float) -> float:
        if not vals:
            return 0.0
        vals = sorted(vals)
        k = int(round((len(vals) - 1) * p))
        return vals[k]

    latency_summary = {
        hook: {
            "p50_ms": _percentile(samples, 0.50),
            "p95_ms": _percentile(samples, 0.95),
            "count": len(samples),
        }
        for hook, samples in latency_samples.items()
    }

    return {
        "install_id": _read_config().get("install_id"),
        "schema_version": SCHEMA_VERSION,
        "events_total": sum(counts.values()),
        "event_counts": counts,
        "tokens_saved_total": tokens_saved_total,
        "cross_session_hits": cross_session_hits,
        "digest_injections": digest_count,
        "digest_turns_compressed_total": digest_turns_compressed_total,
        "latency_by_hook": latency_summary,
        "user_actions": user_actions,
    }


def export_json(out_path: Path | str) -> Path:
    out = Path(out_path)
    out.write_text(json.dumps(export_aggregates(), indent=2))
    return out


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

USAGE = (
    "usage: tinm_telemetry.py [status | on [--share] | off | purge |\n"
    "                          export <path> | install]\n"
)


def cli(args: list[str]) -> int:
    """Minimal CLI. Invoke via `python mvp/skill/tinm_telemetry.py <cmd>`."""
    if not args or args[0] == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if args[0] == "on":
        share = "--share" in args
        enable(share_aggregates=share)
        print(f"telemetry ON (share_aggregates={share}). data at {TELEMETRY_FILE}")
        return 0
    if args[0] == "off":
        disable()
        print("telemetry OFF. existing data kept. run 'tinm telemetry purge' to delete.")
        return 0
    if args[0] == "purge":
        purge()
        print("telemetry data purged.")
        return 0
    if args[0] == "export":
        out = Path(args[1]) if len(args) > 1 else Path("tinm_telemetry_export.json")
        export_json(out)
        print(f"aggregates written to {out}")
        return 0
    if args[0] == "install":
        install_opt_in_interactive()
        return 0
    print(USAGE, file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Install-time opt-in prompt — invoked by the (eventual) one-liner installer
# or by `python tinm_telemetry.py install` directly.
# ---------------------------------------------------------------------------

OPT_IN_PROMPT = """\
TINM can record a small set of local-only metrics so you (and we) can see
how much it actually helps you:
  - how often it recalls something across sessions
  - estimated tokens saved
  - digest events
  - hook latency (p50 / p95)
  - your /tinm command usage

What we never capture: prompts, file contents, chat text. Ever.

Data lives at ~/.tinm/telemetry.jsonl, on your machine only.

Enable telemetry?
  [y] yes, local-only (recommended for early users)
  [s] yes, and share aggregates with Mouhamadou (no content, just counts)
  [n] no thanks
"""


def install_opt_in_interactive() -> None:
    """Show the 3-choice prompt and write the user's decision."""
    print(OPT_IN_PROMPT)
    try:
        choice = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n(no input — defaulting to disabled)")
        disable()
        return
    if choice == "y":
        enable(share_aggregates=False)
        print("telemetry enabled (local-only). Thanks!")
    elif choice == "s":
        enable(share_aggregates=True)
        print("telemetry enabled (aggregates shared). Thanks!")
    else:
        disable()
        print("telemetry disabled. You can enable later with `tinm telemetry on`.")


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
