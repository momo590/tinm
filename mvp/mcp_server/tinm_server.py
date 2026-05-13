"""TINM MCP server — exposes thread/artifact operations to Claude Code.

What this server adds on top of the SessionStart + UserPromptSubmit hooks:

- Hooks handle silent persistence (auto-load at session start, auto-update
  on every user prompt). They do not need Claude to "remember" to call
  anything.
- This MCP server exposes the operations Claude does want to invoke
  contextually: looking up a named artifact when the user references one
  anaphorically ("modify the figure like we did before"), or registering
  a new artifact mid-conversation, or asking the protocol "what thread
  am I in right now?".

Transport: stdio (the default for FastMCP). Claude Code spawns this as a
subprocess via the `mcpServers` entry in ~/.claude.json. The lifetime is
one server-per-session — Claude Code starts us at session open and kills
us at session close.

Install dependency: `mcp[cli]` (see mvp/requirements.txt). The server is
expected to be invoked as

    ~/.tinm/.venv/bin/python ~/.claude/skills/tinm/mcp_server/tinm_server.py

(or with absolute paths — see mvp/README.md).
"""
from __future__ import annotations

import sys
from pathlib import Path


# The CLI scripts (tinm_init / load / update / artifact) live one
# directory level up under skill/. Make them importable so we can reuse
# their parsing + IO logic without duplicating it.
_HERE = Path(__file__).resolve().parent
_SKILL_DIR = _HERE.parent / "skill"
sys.path.insert(0, str(_SKILL_DIR))

import tinm_artifact  # noqa: E402  (sys.path tweak above is intentional)
import tinm_load  # noqa: E402
from tinm_paths import CURRENT_FILE  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("tinm")


def _resolve_thread_id(thread_id: str | None) -> str:
    """Use the explicit thread_id when given, else fall back to the
    currently-loaded thread stored at `$TINM_HOME/current_thread`
    (default `~/.tinm/current_thread`).

    Raises a ValueError if neither is available — Claude should surface
    the message verbatim and ask the user to /tinm load <slug>.
    """
    if thread_id:
        return thread_id
    if CURRENT_FILE.exists():
        text = CURRENT_FILE.read_text().strip()
        if text:
            return text
    raise ValueError(
        "No current TINM thread set. Run `tinm_init.py <slug> --title ...` "
        "or `tinm_load.py <slug>` to mark a thread as current."
    )


@mcp.tool()
def current_thread() -> str:
    """Return the slug of the currently-active TINM thread (machine-local
    marker at `$TINM_HOME/current_thread`), or 'none' if no thread has
    been loaded or initialised yet on this host. Use this when the user
    asks "what am I working on?" or before invoking any other tinm tool
    to confirm a thread exists.
    """
    if CURRENT_FILE.exists():
        text = CURRENT_FILE.read_text().strip()
        return text or "none"
    return "none"


@mcp.tool()
def load_thread_context(
    thread_id: str | None = None,
    n_trajectory: int = 5,
) -> str:
    """Return the markdown context block for a TINM thread (title, last N
    trajectory turns, registered artifacts, anchor diagnostics). If
    `thread_id` is omitted, uses the current thread. Useful when a user
    explicitly asks "remind me where I was on X" mid-conversation, or
    when the SessionStart hook output was lost.
    """
    return tinm_load.load_thread(_resolve_thread_id(thread_id), n_trajectory)


@mcp.tool()
def artifact_find(
    query: str,
    thread_id: str | None = None,
    k: int = 3,
) -> str:
    """Resolve an anaphoric reference to a named artifact in the current
    TINM thread. First tries substring match on name + aliases (cheap and
    high-precision), then falls back to cosine similarity on the stored
    embeddings (semantic). Returns the top-K matches as a markdown block
    with name, ref, and summary so Claude can quote them back to the
    user. Invoke whenever the user says "like the X we did", "the figure
    from last session", "where are the Y results", etc.
    """
    hits = tinm_artifact.cmd_find(_resolve_thread_id(thread_id), query, k=k)
    return tinm_artifact._format_hits_md(hits)


@mcp.tool()
def artifact_add(
    artifact_id: str,
    name: str,
    ref: str,
    summary: str,
    thread_id: str | None = None,
    aliases: list[str] | None = None,
) -> str:
    """Register a named artifact (file path, figure, table, …) in the
    current TINM thread so later turns and later sessions can resolve
    anaphoric references to it. `artifact_id` is a stable kebab-case
    slug; `name` is the human-readable label; `ref` is a path or URL;
    `summary` is a 1-2 sentence description suitable for context
    injection; `aliases` is an optional list of alternate phrasings the
    user might use. Invoke after producing a substantive artifact during
    the session.
    """
    entry = tinm_artifact.cmd_add(
        _resolve_thread_id(thread_id),
        artifact_id=artifact_id,
        name=name,
        ref=ref,
        summary=summary,
        aliases=aliases,
    )
    return f"Registered artifact {entry['id']!r}: {entry['name']}"


if __name__ == "__main__":
    # FastMCP's .run() defaults to stdio and manages its own event loop.
    mcp.run()
