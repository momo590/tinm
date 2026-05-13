---
description: TINM cross-session continuity — `/tinm` alone loads the current thread; `/tinm load <slug>` switches threads.
argument-hint: [load <slug>]
---

The user invoked `/tinm $ARGUMENTS`.

This slash command is intentionally minimal. The TINM protocol persists
the trajectory and artifacts in the background through the SessionStart
and UserPromptSubmit hooks; the user does not need to type anything to
make TINM work. The two situations that need an explicit user action
are:

- **"Where was I?"** — typed by the user when they want to see the
  current thread's context block again (the same block the SessionStart
  hook injected silently). This is `/tinm` with no arguments.
- **"Switch projects"** — typed when the user wants to read or
  contribute to a different thread than the one this host currently
  has marked as active. This is `/tinm load <slug>`.

Everything else (creating a new thread, recording a turn, registering
or looking up an artifact) is handled either automatically by the hooks
or by Claude itself via the `tinm` MCP server. The user should not have
to type `init`, `update`, `artifact`, etc.

## Behaviour

**Always use absolute paths** for the Python interpreter and script
files. The interpreter is `~/.tinm/.venv/bin/python`, and the scripts
live at `~/.claude/skills/tinm/<name>.py` (resolved through a symlink
into the MVP repo).

- **If `$ARGUMENTS` is empty** (the default case):
  1. Read the current thread id from `~/.tinm/current_thread` (one line,
     strip whitespace).
  2. If the file is missing or empty, print:
     > No current thread on this host yet. Start working in a git
     > project and the next session will auto-create one — or run
     > `/tinm load <slug>` to switch to a thread that already exists in
     > `$TINM_PCP_DIR/threads/` (default `~/.tinm/pcp/threads/`).

     Do not invoke anything else.
  3. Otherwise, run
     `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_load.py <thread_id>`
     and stream its markdown output back to the user verbatim.

- **If the first token of `$ARGUMENTS` is `load`**: run
  `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_load.py <thread_id> [--n-trajectory N]`
  with whatever `<thread_id>` and `--n-trajectory N` followed. Stream
  the markdown stdout verbatim — that is the session-start context.

- **Otherwise** (unknown subcommand): print
  > `/tinm` accepts no arguments (show current thread) or
  > `load <slug> [--n-trajectory N]` (switch threads). Everything else
  > happens automatically or via Claude's tinm MCP tools.

  Do not invoke anything.

- If the script exits non-zero (e.g. thread not found, embedding
  mismatch): surface the stderr text verbatim to the user and do not
  retry.

- After a successful load (explicit or implicit via empty args), ask
  what the user wants to do next; do not assume the next action.

## References

- PCP v0 spec: [`mvp/pcp_v0_spec.md`](../pcp_v0_spec.md).
- Other tinm scripts available on disk but not exposed here:
  `tinm_init.py` (used by auto-init at SessionStart and by the MCP
  `thread_init` tool), `tinm_update.py` (used by UserPromptSubmit),
  `tinm_artifact.py` (used by the MCP `artifact_add` /
  `artifact_find` tools), `tinm_auto_init.py` (the auto-init policy
  itself). None of these is meant to be invoked by the user directly.
