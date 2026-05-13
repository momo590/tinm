---
description: TINM cross-session continuity — `/tinm` alone loads the current thread; subcommands handle init/update/artifact.
argument-hint: [load|init|update|artifact <args>]
---

The user invoked `/tinm $ARGUMENTS`.

Parse `$ARGUMENTS` as `<subcommand> <args>` and execute the matching tinm
script via Bash. **Always use absolute paths** for the Python interpreter
and the script files:

- Interpreter: `~/.tinm/.venv/bin/python`
- Scripts:     `~/.claude/skills/tinm/<name>.py` (resolves through the
  symlink into the MVP repo)

If `$ARGUMENTS` is empty or the subcommand is `load`, surface the
script's markdown output back to the user verbatim (it is the
session-start context block). For other subcommands, run the script and
report success/failure concisely.

## Subcommand mapping

| Subcommand pattern | Command to run |
|---|---|
| `load <thread_id> [--n-trajectory N]` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_load.py <thread_id> [--n-trajectory N]` |
| `init <thread_id> --title "<title>" [--project-root <path>]` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_init.py <thread_id> --title "<title>" [--project-root <path>]` |
| `update <thread_id> --query "<text>"` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_update.py <thread_id> --query "<text>" --role user --client claude-code` |
| `artifact add <thread_id> --id <slug> --name "<name>" --ref "<ref>" --summary "<summary>" [--alias "<alias>" ...]` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_artifact.py <thread_id> add --id <slug> --name "<name>" --ref "<ref>" --summary "<summary>" [--alias "<alias>" ...]` |
| `artifact find <thread_id> "<query>" [--k N]` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_artifact.py <thread_id> find "<query>" [--k N]` |

## Behaviour

- **If `$ARGUMENTS` is empty**: this is the "remind me where I am" use
  case — the default action when the user just types `/tinm`.
  1. Read the current thread id from `~/.tinm/current_thread` (one line,
     strip whitespace).
  2. If the file is missing or empty, print:
     > No current thread on this host. Run `/tinm init <slug> --title "..."`
     > to start a new thread, or `/tinm load <slug>` to switch to an
     > existing one. The list of available threads lives in
     > `$TINM_PCP_DIR/threads/` (default `~/.tinm/pcp/threads/`).

     Do not invoke anything else.
  3. Otherwise, run
     `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_load.py <thread_id>`
     and stream its markdown output back to the user verbatim.
- If the subcommand is unknown: print the subcommand list above with a
  note that the subcommand was unrecognised; do not invoke anything.
- If the script exits non-zero (e.g. thread not found, embedding
  mismatch): surface the stderr text verbatim to the user and do not
  retry.
- After a successful `load` (explicit or implicit via empty args), ask
  the user what they want to do next; do not assume the next action.

## References

- PCP v0 spec: `mvp/pcp_v0_spec.md` in the TINM repo (worktree-dependent
  path; post-merge canonical path is `/Users/user/TNIM/mvp/pcp_v0_spec.md`).
- Skill triggering instructions (for the auto-detected case, if that
  pathway ever starts working): `~/.claude/skills/tinm/SKILL.md`.
