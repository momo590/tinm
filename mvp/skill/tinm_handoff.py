"""Generate a copy-pasteable handoff prompt for the next AI agent.

The user often jongles between agents — Claude Code on Mac, Claude
Code on a Linux VPS, openClaw on a third box. Today the handoff is
manual: paste a recap into the next agent. This module bakes that
recap into a deterministic template so the user can run one command
before leaving the current agent and copy a ready-to-paste prompt.

v1: pure string composition, **no LLM call**. A future v2 may rewrite
with an LLM for prose smoothness — out of scope here.

Usage:
    python tinm_handoff.py [<thread_id>] [--target claude-code|openclaw|generic]
                           [--clipboard]

If `thread_id` is omitted, the current cwd's thread is resolved via
`tinm_provenance.resolve_thread_for_cwd($PWD)`.

The `--target` flag tunes the framing very lightly so the receiving
agent recognises a "load this thread" command it knows:

  claude-code  →  prepends "/tinm load <slug>  # if TINM is installed"
  openclaw     →  prepends "@bumblebee load thread <slug>"
  generic      →  no extra header (default)

`--clipboard` pipes the result to pbcopy (macOS) or xclip (Linux). If
neither is present, the prompt is just printed and a note goes to
stderr — never fails the user's terminal.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from tinm_paths import ARTIFACTS_DIR, SEEDS_DIR, THREADS_DIR


# How many of each section to surface in the handoff prompt. These are
# tuned for "fits in a single paste" — generous enough to give the next
# agent useful context, tight enough not to bury the user in noise.
MAX_TRAJECTORY_TURNS = 5      # last 3-5 user turns
MIN_TRAJECTORY_TURNS = 3
MAX_ANCHOR_TERMS = 8          # top 5-8 anchor terms
MAX_ARTIFACTS = 8             # most recent named artifacts
MAX_LAST_USER_TURN_CHARS = 200  # cap on quoted "last thing I asked"

VALID_TARGETS = ("claude-code", "openclaw", "generic")


# ---- resolution -----------------------------------------------------------


def _resolve_paths(thread_id: str) -> tuple[Path, Path] | None:
    """Find the thread + artifacts JSON for `thread_id`.

    Mirrors `tinm_load._resolve_paths`: user threads in `pcp/threads/`
    win over seeds with the same id. Returns None when the id is in
    neither namespace.
    """
    user_thread = THREADS_DIR / f"{thread_id}.json"
    if user_thread.exists():
        return user_thread, ARTIFACTS_DIR / f"{thread_id}.json"
    seed_thread = SEEDS_DIR / f"{thread_id}.json"
    if seed_thread.exists():
        return seed_thread, SEEDS_DIR / f"{thread_id}.artifacts.json"
    return None


def _load_thread(thread_id: str) -> tuple[dict, dict]:
    """Return (thread_dict, artifacts_dict). Raises FileNotFoundError."""
    resolved = _resolve_paths(thread_id)
    if resolved is None:
        raise FileNotFoundError(
            f"Thread {thread_id!r} not found in {THREADS_DIR} or {SEEDS_DIR}."
        )
    thread_path, arts_path = resolved
    thread = json.loads(thread_path.read_text())
    if arts_path.exists():
        artifacts = json.loads(arts_path.read_text())
    else:
        artifacts = {"artifacts": []}
    return thread, artifacts


# ---- extraction -----------------------------------------------------------


def _top_terms(thread: dict) -> list[str]:
    """Extract anchor.top_terms with a fallback to top-level top_terms.

    Newer code (post-v0.2.x) stores top_terms inside `anchor` (see
    tinm_update.py); the bundled `tinm-tour` seed uses the older
    top-level key. Support both — readers should never crash on either
    layout.
    """
    anchor = thread.get("anchor") or {}
    terms = anchor.get("top_terms") or thread.get("top_terms") or []
    return [t for t in terms if isinstance(t, str)]


def _user_turns(trajectory: list[dict]) -> list[dict]:
    return [t for t in trajectory if (t.get("role") == "user")]


def _last_user_text(trajectory: list[dict]) -> str:
    users = _user_turns(trajectory)
    if not users:
        return ""
    return (users[-1].get("text") or "").strip()


def _recent_user_turns(trajectory: list[dict]) -> list[dict]:
    users = _user_turns(trajectory)
    if not users:
        return []
    # The wedge says "last 3-5 user turns". We always show what we
    # have up to MAX, and if there are fewer than MIN that's fine —
    # better to honest-up than pad.
    return users[-MAX_TRAJECTORY_TURNS:]


def _short_fingerprint(thread: dict) -> str:
    fp = (thread.get("metadata") or {}).get("workspace_fingerprint") or ""
    if not fp:
        return "(no fingerprint)"
    # fp is "sha256:<32hex>" — show prefix + first 8 hex for readability
    if ":" in fp:
        scheme, hex_part = fp.split(":", 1)
        return f"{scheme}:{hex_part[:8]}…"
    return fp[:16] + ("…" if len(fp) > 16 else "")


def _named_artifacts(artifacts: dict) -> list[dict]:
    """Most recent named artifacts (max MAX_ARTIFACTS).

    Sort by created_at when available; fall back to original order
    (callers that build artifacts in-order get a stable result either
    way). Approved-exchange entries are filtered out — they're a
    different beast from named artifacts the user explicitly registered.
    """
    arts = artifacts.get("artifacts") or []
    named = [
        a for a in arts
        if a.get("source", "legacy_manual") != "approved_exchange"
    ]
    named.sort(key=lambda a: a.get("created_at", ""))
    return named[-MAX_ARTIFACTS:]


# ---- template -------------------------------------------------------------


def _suggested_next_prompt(thread: dict) -> str:
    """Deterministic synthesis — no LLM, just string composition.

    Builds:
        "Continue working on <title>. Most recent topic: <terms>.
         Last thing I asked: '<last user turn[:200]>'..."

    Each piece degrades gracefully if missing.
    """
    title = (thread.get("metadata") or {}).get("title") or thread.get("thread_id", "this thread")
    terms = _top_terms(thread)[:3]
    last_user = _last_user_text(thread.get("trajectory") or [])

    parts = [f"Continue working on {title}."]
    if terms:
        parts.append(f"Most recent topic: {', '.join(terms)}.")
    if last_user:
        snippet = last_user[:MAX_LAST_USER_TURN_CHARS]
        ellipsis = "…" if len(last_user) > MAX_LAST_USER_TURN_CHARS else ""
        # Strip newlines so the snippet stays a single quoted line.
        snippet = " ".join(snippet.split())
        parts.append(f"Last thing I asked: \"{snippet}{ellipsis}\"")
    return " ".join(parts)


def _section_where_we_were(trajectory: list[dict]) -> list[str]:
    recent = _recent_user_turns(trajectory)
    if not recent:
        return ["_(no user turns yet)_"]
    lines = []
    for t in recent:
        text = (t.get("text") or "").strip()
        # Keep each turn on its own bullet, single-line where possible.
        text = " ".join(text.split())
        if len(text) > 280:
            text = text[:280] + "…"
        turn_num = t.get("turn")
        prefix = f"- **t{turn_num}**: " if turn_num is not None else "- "
        lines.append(f"{prefix}{text}")
    return lines


def _section_anchor(thread: dict) -> list[str] | None:
    terms = _top_terms(thread)[:MAX_ANCHOR_TERMS]
    if not terms:
        return None
    return ["- " + ", ".join(f"`{t}`" for t in terms)]


def _section_artifacts(artifacts: dict) -> list[str] | None:
    named = _named_artifacts(artifacts)
    if not named:
        return None
    lines = []
    for a in named:
        name = a.get("name") or a.get("id") or "(unnamed)"
        summary = (a.get("summary") or "").strip()
        summary = " ".join(summary.split())
        if len(summary) > 180:
            summary = summary[:180] + "…"
        ref = a.get("ref")
        ref_part = f" → `{ref}`" if ref else ""
        if summary:
            lines.append(f"- **{name}**{ref_part}: {summary}")
        else:
            lines.append(f"- **{name}**{ref_part}")
    return lines


def render_handoff(thread: dict, artifacts: dict, *, target: str = "generic") -> str:
    """Render the markdown handoff block.

    Pure function over already-loaded thread + artifacts dicts so tests
    don't have to round-trip through disk. The CLI is a thin wrapper.
    """
    if target not in VALID_TARGETS:
        raise ValueError(f"target must be one of {VALID_TARGETS}, got {target!r}")

    meta = thread.get("metadata") or {}
    title = meta.get("title") or thread.get("thread_id", "(untitled)")
    slug = thread.get("thread_id") or "(unknown)"
    last_updated = meta.get("last_updated") or meta.get("created_at") or "?"
    fp_short = _short_fingerprint(thread)

    lines: list[str] = []

    # Optional target header — only one line so the user can paste the
    # whole block as-is into the next agent and the load command is the
    # first thing it sees.
    if target == "claude-code":
        lines.append(f"/tinm load {slug}  # if TINM is installed on this machine")
        lines.append("")
    elif target == "openclaw":
        lines.append(f"@bumblebee load thread {slug}")
        lines.append("")

    lines.append(f"# Handoff — {title}")
    lines.append("")
    lines.append(
        f"**Thread:** `{slug}` · **Last active:** {last_updated}"
    )
    lines.append(f"**Workspace fingerprint:** {fp_short}")
    lines.append("")

    # ## Where we were
    lines.append("## Where we were")
    lines.extend(_section_where_we_were(thread.get("trajectory") or []))
    lines.append("")

    # ## Anchor — skipped entirely when top_terms is absent.
    anchor_lines = _section_anchor(thread)
    if anchor_lines is not None:
        lines.append("## Anchor (what's in working memory)")
        lines.extend(anchor_lines)
        lines.append("")

    # ## Named artifacts — skipped when none registered.
    art_lines = _section_artifacts(artifacts)
    if art_lines is not None:
        lines.append("## Named artifacts you may need")
        lines.extend(art_lines)
        lines.append("")

    # ## Suggested next prompt — always present.
    lines.append("## Suggested next prompt")
    lines.append(_suggested_next_prompt(thread))
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---- clipboard ------------------------------------------------------------


def _copy_to_clipboard(text: str) -> tuple[bool, str]:
    """Attempt to copy `text` to the system clipboard.

    Returns (ok, mechanism). On failure (no tool available, or tool
    errored) returns (False, reason) — callers should fall back to
    printing the text.
    """
    if shutil.which("pbcopy"):
        try:
            subprocess.run(
                ["pbcopy"], input=text, text=True, check=True
            )
            return True, "pbcopy"
        except subprocess.SubprocessError as e:
            return False, f"pbcopy failed: {e}"

    if shutil.which("xclip"):
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                text=True,
                check=True,
            )
            return True, "xclip"
        except subprocess.SubprocessError as e:
            return False, f"xclip failed: {e}"

    if shutil.which("wl-copy"):
        # Wayland support — small bonus, same fallback pattern.
        try:
            subprocess.run(["wl-copy"], input=text, text=True, check=True)
            return True, "wl-copy"
        except subprocess.SubprocessError as e:
            return False, f"wl-copy failed: {e}"

    return False, "no clipboard tool available (pbcopy/xclip/wl-copy)"


# ---- CLI -----------------------------------------------------------------


def _resolve_thread_id(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    # Lazy import: tinm_provenance pulls tinm_init at module load, which
    # imports tinm_telemetry, etc. Defer until we actually need it so the
    # `--help` path stays snappy and the module is import-cheap in tests.
    try:
        from tinm_provenance import resolve_thread_for_cwd
    except ImportError:
        return None
    try:
        return resolve_thread_for_cwd()
    except Exception:
        # Never let provenance failures crash the handoff — the user can
        # always pass an explicit thread_id.
        return None


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tinm_handoff.py",
        description=(
            "Generate a copy-pasteable handoff prompt for the next AI agent. "
            "v1: deterministic template, no LLM."
        ),
    )
    parser.add_argument(
        "thread_id",
        nargs="?",
        default=None,
        help=(
            "Thread to hand off. Defaults to the thread resolved for the "
            "current cwd via tinm_provenance.resolve_thread_for_cwd()."
        ),
    )
    parser.add_argument(
        "--target",
        choices=VALID_TARGETS,
        default="generic",
        help=(
            "Lightly tune the framing for the receiving agent. "
            "`claude-code` and `openclaw` add a one-line load hint at the top."
        ),
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help=(
            "Copy the rendered prompt to the system clipboard "
            "(pbcopy / xclip / wl-copy). Falls back to stdout if none is "
            "available."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    thread_id = _resolve_thread_id(args.thread_id)
    if not thread_id:
        print(
            "error: no thread_id provided and could not resolve a thread "
            "for the current cwd. Pass one explicitly.",
            file=sys.stderr,
        )
        return 1

    try:
        thread, artifacts = _load_thread(thread_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"error: thread JSON is malformed: {e}", file=sys.stderr)
        return 1

    rendered = render_handoff(thread, artifacts, target=args.target)

    if args.clipboard:
        ok, mechanism = _copy_to_clipboard(rendered)
        if ok:
            print(f"[copied to clipboard via {mechanism}]", file=sys.stderr)
            # Still print to stdout so the user can pipe / tee if they
            # want to. Clipboard is a convenience, not a redirect.
            sys.stdout.write(rendered)
            return 0
        # Graceful fallback — print to stdout, warn on stderr.
        print(
            f"[clipboard unavailable: {mechanism}; printing to stdout]",
            file=sys.stderr,
        )

    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
