"""Read-time renderer for legacy TINM artifact bodies (v0.2.3 L3).

Many artifacts written by Claude during prior work sessions are jargon-dense.
They contain internal task slugs (`T0`, `T1-T2-T3`, `Lane A`), status sigils
(`BLOCKER`, `[CLEAR]`, `Path C strict`, `noop`), dated lock phrases
(`locked 2026-05-16`, `pinned 14:32Z`), raw thread IDs (`tinm-tour`,
`loremind-resume-2026-05-17`), and parenthetical noise (`(via [[...]])`).

These are useful for the writing agent (compact, unambiguous), but illegible
when surfaced back to the human user at session start (`tinm_load.py`) or via
anaphora resolution (`tinm_artifact.py find`).

This module implements **MVP v1: rule-based, no LLM**. It is intentionally
conservative — when a pattern is ambiguous, the original text is preserved.

Design contract:
- `humanize(body)` is idempotent: `humanize(humanize(x)) == humanize(x)`.
- Cheap (<10ms on typical artifact bodies, no per-char loops).
- Never mutates inputs; returns a new string.
- Failure mode: on any exception, return the original body unchanged.

Wire-in points: `tinm_load.py` (named/approved artifact summary printing) and
`tinm_artifact.py` (find results). Both gate on `TINM_RENDER_LEGACY=1`
(default on).

See design notes in `/root/.gstack/projects/tinm/design-humanize-resurface-2026-05-17.md`
section "v0.2.3 TODO — Read-time structural renderer" and the memory entry
`project_tinm_v023_renderer_todo.md`.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path

try:
    from tinm_paths import THREADS_DIR
except Exception:  # tinm_paths import side effects (mkdir, etc.) can fail in tests
    THREADS_DIR = Path.home() / ".tinm" / "pcp" / "threads"


# ─── Pattern 1: Task slugs ─────────────────────────────────────────────────────
# "T0", "T1", "T12" — standalone task identifiers.
# Range form: "T1-T2-T3" or "T1-T3" — task range.
# Boundary: must NOT be inside a word like "T1 lymphocyte", "TT0", "GPT4".
# Conservative: only fire when followed by whitespace/punctuation/EOL AND
# preceded by whitespace/start/punctuation.
_TASK_RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"        # boundary
    r"T(\d+)(?:-T?(\d+)){1,5}"  # T1-T3, T1-T2-T3
    r"(?![A-Za-z0-9_])"
)
_TASK_SINGLE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"T(\d+)"
    r"(?![A-Za-z0-9_-])"        # also not followed by - (avoid mid-range)
)

# Ordinals for first ~10 tasks; beyond that we fall back to "task N".
_ORDINALS = [
    "the first", "the second", "the third", "the fourth", "the fifth",
    "the sixth", "the seventh", "the eighth", "the ninth", "the tenth",
]


def _task_range_repl(m: re.Match) -> str:
    txt = m.group(0)
    nums = [int(n) for n in re.findall(r"\d+", txt)]
    if not nums:
        return txt
    lo, hi = min(nums), max(nums)
    return f"tasks {lo}-{hi}"


def _task_single_repl(m: re.Match) -> str:
    n = int(m.group(1))
    if 1 <= n <= len(_ORDINALS):
        return f"{_ORDINALS[n - 1]} task"
    if n == 0:
        return "the kickoff task"
    return f"task {n}"


# ─── Pattern 2: Lane slugs ─────────────────────────────────────────────────────
# "Lane A", "Lane B", "Lane B+C", "lanes A-F"
_LANE_RANGE_RE = re.compile(
    r"(?<![A-Za-z])lanes?\s+([A-F])\s*[-–+]\s*([A-F])(?![A-Za-z])",
    re.IGNORECASE,
)
_LANE_SINGLE_RE = re.compile(
    r"(?<![A-Za-z])Lane\s+([A-F])(?![A-Za-z])",
)

_LANE_ORDINALS = {
    "A": "the first workstream",
    "B": "the second workstream",
    "C": "the third workstream",
    "D": "the fourth workstream",
    "E": "the fifth workstream",
    "F": "the sixth workstream",
}


def _lane_range_repl(m: re.Match) -> str:
    a, b = m.group(1).upper(), m.group(2).upper()
    return f"workstreams {a}-{b}"


def _lane_single_repl(m: re.Match) -> str:
    letter = m.group(1).upper()
    return _LANE_ORDINALS.get(letter, m.group(0))


# ─── Pattern 3: Status sigils ──────────────────────────────────────────────────
# These are word-boundary replacements; we want plain English.
# Order matters: bracketed forms before bare forms.
_SIGIL_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\[CLEAR\]"), "approved"),
    (re.compile(r"\bCLEAR\b(?!\s+[a-z])"), "approved"),  # avoid "clear the cache"
    (re.compile(r"\bBLOCKER\b"), "blocking issue"),
    (re.compile(r"\bnoop\b"), "no change"),
    (re.compile(r"\bPath C strict\b"), "the strict approach C"),
    (re.compile(r"\bverrouill[ée]s?\b"), "locked in"),
    # "STOP" alone as a sigil — but not "STOP" in sentences like "Don't STOP"
    # is rare; we capitalize on the all-caps tag form.
    (re.compile(r"(?<![A-Za-z])STOP(?![A-Za-z])"), "stop"),
]


# ─── Pattern 4: Dated lock phrases ─────────────────────────────────────────────
# "locked 2026-05-16", "decided 2026-05-16", "verrouillé 2026-05-16", "pinned 14:32Z"
# These are surfaced as parentheticals or trailing fragments. We rewrite them
# as relative time.
_DATE_LOCK_RE = re.compile(
    r"\b(locked|decided|pinned|verrouill[ée]|fix[ée])\s+"
    r"(\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_TIME_LOCK_RE = re.compile(
    r"\b(pinned|locked)\s+\d{1,2}:\d{2}Z?\b",
    re.IGNORECASE,
)


def _today() -> date:
    return datetime.now().date()


def _relative_date_phrase(verb: str, iso_date: str) -> str:
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except ValueError:
        return f"{verb} {iso_date}"

    verb_clean = verb.lower()
    if verb_clean.startswith("verrouill") or verb_clean.startswith("fix"):
        verb_clean = "decided"

    delta = (_today() - d).days
    if delta == 0:
        return f"{verb_clean} earlier today"
    if delta == 1:
        return f"{verb_clean} yesterday"
    if 2 <= delta <= 3:
        return f"{verb_clean} {delta} days ago"
    # Older: spell out the month.
    return f"{verb_clean} on {d.strftime('%B %-d')}"


def _date_lock_repl(m: re.Match) -> str:
    return _relative_date_phrase(m.group(1), m.group(2))


def _time_lock_repl(m: re.Match) -> str:
    return f"{m.group(1).lower()} earlier today"


# ─── Pattern 5: Raw thread IDs ─────────────────────────────────────────────────
# Bare kebab-slug tokens like `tinm-tour`, `loremind-resume-2026-05-17`.
# Match candidates, then check existence on disk before quoting.
# Avoid: URLs (preceded by /), already-quoted strings, code in backticks.
_SLUG_CANDIDATE_RE = re.compile(
    r"(?<![\w\-/`\"])"          # not preceded by word, dash, slash, backtick, quote
    r"([a-z][a-z0-9]+(?:-[a-z0-9]+){1,8})"
    r"(?![\w\-/`\"])"           # not followed by same
)


def _thread_ids_on_disk() -> set[str]:
    """Cheap snapshot of existing thread IDs (cached per-process)."""
    cache = getattr(_thread_ids_on_disk, "_cache", None)
    if cache is not None:
        return cache
    ids: set[str] = set()
    try:
        if THREADS_DIR.exists():
            for p in THREADS_DIR.glob("*.json"):
                ids.add(p.stem)
    except Exception:
        pass
    _thread_ids_on_disk._cache = ids  # type: ignore[attr-defined]
    return ids


def _clear_thread_id_cache() -> None:
    """Test hook: drop the cached thread-id set."""
    if hasattr(_thread_ids_on_disk, "_cache"):
        delattr(_thread_ids_on_disk, "_cache")


def _quote_thread_ids(body: str) -> str:
    known = _thread_ids_on_disk()
    if not known:
        return body

    def _sub(m: re.Match) -> str:
        slug = m.group(1)
        if slug in known:
            return f'"{slug}"'
        return slug

    return _SLUG_CANDIDATE_RE.sub(_sub, body)


# ─── Pattern 6: Bullet / parenthetical noise ───────────────────────────────────
# Strip trailing fragments like " — locked 2026-05-16" only when they are
# pure meta-noise. This is intentionally narrow.
_PAREN_VIA_RE = re.compile(r"\s*\(via\s+\[\[[^\]]+\]\][^)]*\)")
_PAREN_VIA2_RE = re.compile(r"\s*\(via\s+`[^`]+`\s+memory\)")


# ─── Idempotence guard ────────────────────────────────────────────────────────
# Strings produced by the renderer should not be re-rewritten. We detect a
# minimum signal: if the body already contains a humanized phrase ("workstream",
# "the first task", etc.) AND none of the source sigils, we treat it as already
# rendered and return unchanged. Cheap heuristic.

_HUMANIZED_MARKERS = (
    "workstream",
    "the first task", "the second task", "the third task",
    "the strict approach C",
    "blocking issue",
    "decided yesterday", "decided earlier today",
    "approved",
)
_RAW_SIGIL_PROBE = re.compile(
    r"(?<![A-Za-z0-9_])T\d+(?![A-Za-z0-9_])"
    r"|\bLane\s+[A-F]\b"
    r"|\bBLOCKER\b"
    r"|\[CLEAR\]"
    r"|\bnoop\b"
    r"|\bverrouill[ée]"
    r"|\bPath C strict\b"
    r"|\b(?:locked|pinned|decided)\s+\d{4}-\d{2}-\d{2}\b"
)


def _looks_already_rendered(body: str) -> bool:
    if _RAW_SIGIL_PROBE.search(body):
        return False
    return any(m in body for m in _HUMANIZED_MARKERS)


# ─── Main entry point ─────────────────────────────────────────────────────────


def humanize(body: str) -> str:
    """Humanize a single artifact body.

    Pure function. Never raises on bad input — falls back to the input on any
    error so a bad rule cannot break the surfacing surface.
    """
    if not body or not isinstance(body, str):
        return body

    # Idempotence short-circuit
    if _looks_already_rendered(body):
        return body

    try:
        out = body

        # 1. Task slugs — ranges first, then singletons.
        out = _TASK_RANGE_RE.sub(_task_range_repl, out)
        out = _TASK_SINGLE_RE.sub(_task_single_repl, out)

        # 2. Lanes
        out = _LANE_RANGE_RE.sub(_lane_range_repl, out)
        out = _LANE_SINGLE_RE.sub(_lane_single_repl, out)

        # 3. Status sigils
        for pat, repl in _SIGIL_RULES:
            out = pat.sub(repl, out)

        # 4. Dated lock phrases
        out = _DATE_LOCK_RE.sub(_date_lock_repl, out)
        out = _TIME_LOCK_RE.sub(_time_lock_repl, out)

        # 5. Quote known thread IDs
        out = _quote_thread_ids(out)

        # 6. Strip parenthetical noise
        out = _PAREN_VIA_RE.sub("", out)
        out = _PAREN_VIA2_RE.sub("", out)

        return out
    except Exception:
        # Renderer must never break the caller. Fall back to raw.
        return body


def render_enabled() -> bool:
    """Feature flag check. Defaults ON; set TINM_RENDER_LEGACY=0 to disable."""
    return os.environ.get("TINM_RENDER_LEGACY", "1") == "1"


def humanize_if_enabled(body: str) -> str:
    """Apply humanize() only when the feature flag is on. Convenience for callers."""
    if not render_enabled():
        return body
    return humanize(body)


__all__ = [
    "humanize",
    "humanize_if_enabled",
    "render_enabled",
]
