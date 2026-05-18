"""TINM next-action signal extractor (L4 wedge, v1 MVP).

Goal
----
TINM should deduce the user's *next step* automatically — so a new
session opens with a short "Picking up from..." block instead of "what
do you want to do today?". v1 is **signal detection only**: a thin layer
that watches trajectory turns for deterministic patterns ("done", "next",
"waiting on", failures, decisions, unblock events) and surfaces the
freshest few at SessionStart.

This is *not* an LLM-based next-step deducer — that's v3+ scope. v1
must:
  1. Be deterministic and dirt cheap (regex + tiny JSONL append).
  2. Add zero round-trips to the user prompt path (best-effort, errors
     swallowed — TINM hooks already follow this discipline).
  3. Survive multi-host setups (signals JSONL goes under TINM_PCP_DIR
     alongside threads/, so Syncthing/git-pcp carries it for free).

Layout
------
    ${TINM_PCP_DIR}/signals/<thread_id>.jsonl    # append-only, one JSON per line

Each line:
    {
      "ts": "2026-05-17T14:50:00Z",
      "turn": 47,
      "role": "user",
      "signal_type": "task_done",
      "match": "T8 done",
      "raw_context": "...up to 200 chars from the turn text..."
    }

Public API
----------
    extract_signals(turn_text, role) -> list[Signal]
        Stateless pure-function: text → list of signal dicts (no ts/turn).
        The caller adds ts + turn before persisting.

    record_signals(thread_id, turn, role, turn_text, ts=None) -> int
        Convenience wrapper: extract + append to the sidecar JSONL.
        Returns the number of signals persisted.

    next_action_block(thread_id, *, window=20, max_signals=5) -> str | None
        Read-time surface: returns a short markdown block recapping the
        freshest signals from the last `window` turns. Returns None when
        nothing recent was detected so the SessionStart loader can skip
        the section entirely.

Patterns
--------
Six families, French + English (the user switches freely between the
two), word-boundary anchored. Patterns are intentionally narrow — we
prefer false negatives over false positives because a wrong "Picking up
from" line is more annoying than no line at all.

The pattern table is exposed as `SIGNAL_PATTERNS` for tests + future
expansion. Each entry: (signal_type, compiled regex, short label
formatter). The formatter takes the regex Match and the raw text and
returns the short `match` field stored in the JSONL — kept compact so
the surface block stays readable.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, TypedDict

from tinm_paths import TINM_PCP_DIR


# ─────────────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────────────


class Signal(TypedDict, total=False):
    """A single detected signal. ts + turn + role added at record time."""

    ts: str
    turn: int
    role: str
    signal_type: str
    match: str
    raw_context: str


# ─────────────────────────────────────────────────────────────────────────────
# Patterns
# ─────────────────────────────────────────────────────────────────────────────
#
# All patterns are case-insensitive (compiled with re.IGNORECASE).
# Word-boundary anchors keep "fini" from matching "finition", etc.
#
# Order matters: a turn that mentions both "done" and "next" should
# produce both signals. The detector runs every pattern on every turn
# and dedupes only on exact (signal_type, match) tuples.

# A done-ish verb followed (within ~80 chars) by a task identifier.
# Identifiers we currently recognise:
#   - T<digit>         e.g. "T8 done"
#   - Lane <A-F>       e.g. "Lane C tested"
#   - step <digit>     e.g. "step 3 done"
#   - étape <digit>    e.g. "étape 4 fini"
#   - PR #<digit>      e.g. "PR #42 mergé"
#   - phase <digit/word>  e.g. "phase 2 livré"
#
# Notes on form choice:
#   - Past-participle/past-tense verbs only ("done", "merged", "shipped",
#     "fini", "mergé") — *not* infinitives ("to merge", "merger"). This
#     avoids the "Waiting for Alice to merge PR #42" false positive
#     where "merge PR #42" trips a forward-order match.
#   - "PR #N" is dropped from the forward-order alternation because it
#     pulls in too many "to merge PR" / "review PR" false positives. We
#     keep the reverse-order ("PR #42 mergé") which is the actual ack form.
_DONE_VERB_FORWARD = (
    r"(?:done|fini(?:e|s)?|fait(?:e|s)?|termin[ée](?:e|s)?|shipped|livr[ée](?:e|s)?|"
    r"merged|merg[ée](?:e|s)?|completed?)"
)
_DONE_VERB_REVERSE = (
    r"(?:done|fini(?:e|s)?|fait(?:e|s)?|termin[ée](?:e|s)?|shipped|"
    r"livr[ée](?:e|s)?|merged|merg[ée](?:e|s)?|✅)"
)
_TASK_ID_FORWARD = (
    # Forward order: verb first, id after. PR #N excluded — too noisy in this
    # direction (see comment above).
    r"(?:T\d+|Lane\s+[A-F]|step\s+\d+|[ée]tape\s+\d+|phase\s+\w+)"
)
_TASK_ID_REVERSE = (
    # Reverse order: id first ("PR #42 merged") — PR #N safe here.
    r"(?:T\d+|Lane\s+[A-F]|step\s+\d+|[ée]tape\s+\d+|PR\s*#?\d+|phase\s+\w+)"
)
_TASK_DONE_RE = re.compile(
    rf"\b{_DONE_VERB_FORWARD}\b[^.\n]{{0,80}}?\b{_TASK_ID_FORWARD}\b"
    rf"|\b{_TASK_ID_REVERSE}\b[^.\n]{{0,40}}?\b{_DONE_VERB_REVERSE}\b"
    rf"|✅\s*{_TASK_ID_REVERSE}\b",
    re.IGNORECASE,
)

# Generic "something just got unblocked / verified" — no task id required.
_UNBLOCK_RE = re.compile(
    r"\b(?:unblocked|d[ée]bloqu[ée](?:e|s)?|tested|test[ée](?:e|s)?|verified|"
    r"v[ée]rifi[ée](?:e|s)?|ready|pr[êe]t(?:e|s)?|green|passing|all\s+tests?\s+pass)\b",
    re.IGNORECASE,
)

# "Waiting on X" / "blocked on X" / "en attente de X" — captures the
# 30-char tail so the surface block can show *what* we're waiting on.
_PENDING_RE = re.compile(
    r"\b(?:waiting\s+for|waiting\s+on|en\s+attente\s+(?:de|du|d'|of)?|"
    r"blocked\s+on|stuck\s+on|need(?:s|ed)?\s+to|on\s+attend|"
    r"attendre|attend(?:s|ons)?)\b\s*(?:de\s+|du\s+|d'|to\s+|on\s+|for\s+)?"
    r"([^\n.!?]{1,60})",
    re.IGNORECASE,
)

# "Next step is X" / "prochaine étape" / "ensuite" / "then we".
# We keep the trailing 80 chars for the surface block.
#
# Two variants:
#   1. "Next:" / "Next," / "Next " followed by content (no need for
#      step|action|up|is|on after — bare "Next:" is the common shorthand).
#   2. "prochaine étape" / "ensuite" / "après ça" / "then we|I|on" full forms.
#
# Word "next" alone (without trailing colon/comma/space-then-content)
# would over-match (e.g. "next week"). We require either a colon/comma
# OR an explicit step|action|up|is|on continuation.
_NEXT_RE = re.compile(
    r"\bnext\s*[:,]\s*([^\n!?]{1,80})"
    r"|\bnext\s+(?:step|action|up|is|on)\b[^\n!?]{0,8}([^\n!?]{1,80})"
    r"|\bprochaine\s+[ée]tape\b[^\n!?]{0,8}([^\n!?]{1,80})"
    r"|\bensuite\b[:,]?\s*([^\n!?]{1,80})"
    r"|\bapr[èe]s\s+(?:[çc]a|cela)\b[:,]?\s*([^\n!?]{1,80})"
    r"|\bthen\s+(?:we|I|on)\b[^\n!?]{0,8}([^\n!?]{1,80})",
    re.IGNORECASE,
)

# Decision-language. "On choisit", "verrouillé", "locked in", "decided".
_DECISION_RE = re.compile(
    r"\b(?:decided|on\s+choisit|on\s+part\s+sur|on\s+va\s+(?:avec|sur)|"
    r"verrouill[ée](?:e|s)?|locked(?:\s+in|\s+decision)?|approved|"
    r"approuv[ée](?:e|s)?|valid[ée](?:e|s)?)\b"
    r"[^\n.!?]{0,80}",
    re.IGNORECASE,
)

# Failure / breakage.
_FAILURE_RE = re.compile(
    r"\b(?:failed|fail(?:ing|s)?|cass[ée](?:e|s)?|erreur|error|broken|"
    r"broke|reverted|revert(?:s|ed)?|crash(?:ed|ing)?|exception|"
    r"trace(?:back)?|stack\s*trace)\b"
    r"[^\n.!?]{0,80}",
    re.IGNORECASE,
)


def _label_match(m: re.Match, _raw: str) -> str:
    """Default label = the bit of text the regex actually matched."""
    return _shrink(m.group(0))


def _label_pending(m: re.Match, _raw: str) -> str:
    """Pending → include the captured tail so we know *what* we wait for."""
    tail = (m.group(1) or "").strip(" .,;:")
    head = m.group(0).split(tail, 1)[0].rstrip() if tail else m.group(0)
    return _shrink(f"{head} {tail}".strip())


def _label_next(m: re.Match, _raw: str) -> str:
    """Build a label like 'Next: ship v0.2.3'.

    `_NEXT_RE` has multiple alternatives; only one capture group is
    populated per match. We pick the first non-empty one as the "tail".
    """
    tail = ""
    for g in m.groups():
        if g and g.strip():
            tail = g.strip(" .,;:")
            break
    head = m.group(0).split(tail, 1)[0].rstrip() if tail else m.group(0)
    return _shrink(f"{head} {tail}".strip())


def _shrink(s: str, lim: int = 120) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= lim else s[: lim - 1] + "…"


# Public pattern table — used by tests and by `extract_signals`.
SIGNAL_PATTERNS: list[tuple[str, re.Pattern, Callable[[re.Match, str], str]]] = [
    ("task_done", _TASK_DONE_RE, _label_match),
    ("unblock", _UNBLOCK_RE, _label_match),
    ("pending_ack", _PENDING_RE, _label_pending),
    ("next_explicit", _NEXT_RE, _label_next),
    ("decision_made", _DECISION_RE, _label_match),
    ("failure", _FAILURE_RE, _label_match),
]


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_signals(turn_text: str, role: str = "user") -> list[Signal]:
    """Return a list of Signal dicts (without ts/turn) for one turn.

    Pure function — no I/O. Empty input → empty list.
    """
    if not turn_text or not turn_text.strip():
        return []

    seen: set[tuple[str, str]] = set()
    signals: list[Signal] = []
    for sig_type, pattern, labeller in SIGNAL_PATTERNS:
        for m in pattern.finditer(turn_text):
            label = labeller(m, turn_text)
            key = (sig_type, label.lower())
            if key in seen:
                continue
            seen.add(key)
            signals.append(
                {
                    "role": role,
                    "signal_type": sig_type,
                    "match": label,
                    "raw_context": _shrink(turn_text, 200),
                }
            )
    return signals


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────


def _signals_dir() -> Path:
    return TINM_PCP_DIR / "signals"


def signals_path(thread_id: str) -> Path:
    """Return the sidecar JSONL path for a thread (no I/O)."""
    return _signals_dir() / f"{thread_id}.jsonl"


def _utcnow() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def record_signals(
    thread_id: str,
    turn: int,
    role: str,
    turn_text: str,
    *,
    ts: str | None = None,
) -> int:
    """Detect + persist signals for one turn. Returns the count written.

    Best-effort: any IO error is swallowed (returns 0) — the prompt
    path must never break because the sidecar JSONL was unwritable.
    """
    try:
        sigs = extract_signals(turn_text, role)
        if not sigs:
            return 0
        ts = ts or _utcnow()
        path = signals_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for s in sigs:
            entry = {
                "ts": ts,
                "turn": turn,
                "role": role,
                "signal_type": s["signal_type"],
                "match": s["match"],
                "raw_context": s["raw_context"],
            }
            lines.append(json.dumps(entry, ensure_ascii=False))
        # Append atomically-enough: one open(append) call. JSONL append
        # is naturally line-atomic on POSIX for writes < PIPE_BUF, which
        # holds for our line sizes (~300 bytes).
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return len(sigs)
    except (OSError, ValueError):
        return 0


def _iter_signals(thread_id: str) -> Iterable[dict]:
    path = signals_path(thread_id)
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    # Skip malformed lines (e.g. partial write from a
                    # crashed peer host) — JSONL recovery semantics.
                    continue
    except OSError:
        return


# ─────────────────────────────────────────────────────────────────────────────
# Read-time surface
# ─────────────────────────────────────────────────────────────────────────────


# Emoji + human prefix per signal_type. Kept in one place so the surface
# stays consistent if we add types later.
_SURFACE_PREFIX: dict[str, str] = {
    "task_done": "✅ Done",
    "unblock": "🟢 Unblocked",
    "pending_ack": "⏳ Waiting on",
    "next_explicit": "🎯 Next mentioned",
    "decision_made": "🔒 Decided",
    "failure": "❌ Failure",
}


def _max_turn_for(thread_id: str) -> int | None:
    """Read the trajectory file (if present) and return its highest turn.

    Used as the 'current' point for the K-turn window. Falls back to
    the max turn seen in the signals JSONL when the thread JSON is
    missing (defensive — tests can persist signals without ever writing
    a thread file).
    """
    from tinm_paths import THREADS_DIR  # local import: keep top imports lean

    p = THREADS_DIR / f"{thread_id}.json"
    if p.exists():
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            traj = t.get("trajectory", [])
            if traj:
                return max(int(x.get("turn", 0)) for x in traj)
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    # Fallback to signals' max turn.
    max_turn = None
    for entry in _iter_signals(thread_id):
        try:
            t = int(entry.get("turn", 0))
        except (TypeError, ValueError):
            continue
        if max_turn is None or t > max_turn:
            max_turn = t
    return max_turn


def next_action_block(
    thread_id: str,
    *,
    window: int = 20,
    max_signals: int = 5,
) -> str | None:
    """Render the SessionStart 'Picking up from' block, or None.

    Returns None when the signals file is missing, empty, or has no
    entries within the last `window` turns. Otherwise returns a short
    markdown section ready to inline into the loader's output.

    The window is computed against the *current* turn (the max turn in
    the trajectory). This means a thread that has not been written to
    in days will still show its last signals on resume — exactly the
    UX the user asked for ("don't make me re-explain where I was").
    """
    entries = list(_iter_signals(thread_id))
    if not entries:
        return None

    cur = _max_turn_for(thread_id)
    if cur is None:
        # No turn info anywhere — surface latest entries regardless of
        # window. This is the "first-ever record" edge case.
        recent = entries
    else:
        floor = max(0, cur - window + 1)
        recent = [e for e in entries if int(e.get("turn", 0)) >= floor]

    if not recent:
        return None

    # Sort by turn descending, then ts as a tiebreak. Keep the freshest.
    recent.sort(
        key=lambda e: (int(e.get("turn", 0)), e.get("ts", "")),
        reverse=True,
    )
    picked = recent[:max_signals]
    # Within the picked set, re-sort by signal_type priority so the
    # block reads naturally: done → unblock → next → waiting → decision
    # → failure. Tie-broken by turn desc.
    priority = {
        "task_done": 0,
        "unblock": 1,
        "next_explicit": 2,
        "pending_ack": 3,
        "decision_made": 4,
        "failure": 5,
    }
    picked.sort(
        key=lambda e: (
            priority.get(e.get("signal_type", ""), 99),
            -int(e.get("turn", 0)),
        )
    )

    lines = ["## Picking up from"]
    for e in picked:
        sig_type = e.get("signal_type", "")
        prefix = _SURFACE_PREFIX.get(sig_type, "·")
        match = e.get("match", "").strip()
        turn = e.get("turn", "?")
        # Trim labels to keep the block compact in Claude's context.
        if len(match) > 90:
            match = match[:89] + "…"
        lines.append(f"- {prefix}: {match} _(turn {turn})_")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI — diagnostic only
# ─────────────────────────────────────────────────────────────────────────────


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="TINM next-action signal extractor (diagnostic CLI)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_block = sub.add_parser("block", help="Print next_action_block for a thread.")
    p_block.add_argument("thread_id")
    p_block.add_argument("--window", type=int, default=20)
    p_block.add_argument("--max-signals", type=int, default=5)

    p_scan = sub.add_parser(
        "scan",
        help="Scan a thread's trajectory JSON and print detected signals "
        "(does NOT write to the signals sidecar — read-only).",
    )
    p_scan.add_argument("thread_id")
    p_scan.add_argument("--n-turns", type=int, default=50)

    p_record = sub.add_parser(
        "record",
        help="Scan a thread's trajectory and APPEND detected signals to "
        "the sidecar JSONL. Idempotent at the file level — duplicates "
        "are allowed and deduped at read time.",
    )
    p_record.add_argument("thread_id")
    p_record.add_argument("--n-turns", type=int, default=200)

    args = parser.parse_args()

    if args.cmd == "block":
        out = next_action_block(
            args.thread_id, window=args.window, max_signals=args.max_signals
        )
        if out:
            print(out)
        return

    from tinm_paths import THREADS_DIR

    tp = THREADS_DIR / f"{args.thread_id}.json"
    if not tp.exists():
        raise SystemExit(f"thread not found: {tp}")
    t = json.loads(tp.read_text(encoding="utf-8"))
    traj = t.get("trajectory", [])[-args.n_turns :]

    if args.cmd == "scan":
        n_total = 0
        for turn in traj:
            sigs = extract_signals(turn.get("text", ""), turn.get("role", "user"))
            for s in sigs:
                print(
                    f"t{turn.get('turn', '?')} [{s['signal_type']}] "
                    f"role={s['role']} match={s['match']}"
                )
                n_total += 1
        print(f"--- {n_total} signals detected across {len(traj)} turns ---")
        return

    if args.cmd == "record":
        written = 0
        for turn in traj:
            written += record_signals(
                args.thread_id,
                int(turn.get("turn", 0)),
                turn.get("role", "user"),
                turn.get("text", ""),
                ts=turn.get("ts"),
            )
        print(f"--- {written} signals appended to {signals_path(args.thread_id)} ---")
        return


if __name__ == "__main__":
    _main()
