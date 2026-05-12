---
description: TINM cross-session continuity — load/init/update/find against a PCP v0 thread.
argument-hint: load|init|update|artifact <args>
---

The user invoked `/tinm $ARGUMENTS`.

Parse `$ARGUMENTS` as `<subcommand> <args>` and execute the matching tinm
script via Bash. **Always use absolute paths** for the Python interpreter
and the script files:

- Interpreter: `~/.tinm/.venv/bin/python`
- Scripts:     `~/.claude/skills/tinm/<name>.py` (resolves through the
  symlink into the MVP repo)

If the subcommand is `load`, surface the script's markdown output back to
the user verbatim (it is the session-start context block). For other
subcommands, run the script and report success/failure concisely.

## Subcommand mapping

| Subcommand pattern | Command to run |
|---|---|
| `load <thread_id> [--n-trajectory N]` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_load.py <thread_id> [--n-trajectory N]` |
| `init <thread_id> --title "<title>" [--project-root <path>]` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_init.py <thread_id> --title "<title>" [--project-root <path>]` |
| `update <thread_id> --query "<text>"` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_update.py <thread_id> --query "<text>" --role user --client claude-code` |
| `artifact add <thread_id> --id <slug> --name "<name>" --ref "<ref>" --summary "<summary>" [--alias "<alias>" ...]` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_artifact.py <thread_id> add --id <slug> --name "<name>" --ref "<ref>" --summary "<summary>" [--alias "<alias>" ...]` |
| `artifact find <thread_id> "<query>" [--k N]` | `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_artifact.py <thread_id> find "<query>" [--k N]` |

## Behaviour

- If `$ARGUMENTS` is empty: print the subcommand list above and stop, do
  not invoke anything.
- If the subcommand is unknown: print the same list with a note that the
  subcommand was unrecognised; do not invoke anything.
- If the script exits non-zero (e.g. thread not found, embedding
  mismatch): surface the stderr text verbatim to the user and do not
  retry.
- After a successful `load`, ask the user what they want to enchaîner;
  do not assume the next action.

## References

- PCP v0 spec: `mvp/pcp_v0_spec.md` in the TINM repo (worktree-dependent
  path; post-merge canonical path is `/Users/user/TNIM/mvp/pcp_v0_spec.md`).
- Skill triggering instructions (for the auto-detected case, if that
  pathway ever starts working): `~/.claude/skills/tinm/SKILL.md`.
