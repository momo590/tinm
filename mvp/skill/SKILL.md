---
name: tinm
description: Cross-session continuity for the user's TINM-style work threads, persisted in PCP v0 format at ~/.tinm/. Use this skill whenever the user runs `/tinm <subcommand>`, mentions a thread_id, says "resume <slug>", or asks to recall a named artifact ("like the Pareto plot we did") from earlier sessions.
---

# TINM — cross-session continuity skill (PCP v0)

This skill maintains a compressed thread of the user's work across
Claude Code sessions (and eventually across other LLM clients that
speak PCP v0). The protocol is specified in the TINM repo's
`mvp/pcp_v0_spec.md`.

## When to invoke

Invoke this skill when the user:
- Types `/tinm <subcommand>` (load, init, update, artifact).
- Mentions a thread_id that exists in `~/.tinm/threads/`.
- Says "resume X", "load context for X", "remember what we did on X".
- Uses an anaphoric reference to a named artifact ("modify like we did
  for the Pareto plot", "the figure from last session").

## Scripts (all CLI, invoked via Bash)

The scripts require the dedicated Python environment at
`~/.tinm/.venv/` (install steps in `mvp/README.md`).

**Always invoke with the absolute interpreter path and absolute script
path**:

- Interpreter: `~/.tinm/.venv/bin/python`
- Script: `~/.claude/skills/tinm/<name>.py` (resolves through the
  symlink to the repo source)

Relying on the user's default `python` will fail with
`ModuleNotFoundError: sentence_transformers` if the shell PATH does not
put the TINM venv first; relying on a relative script path will fail
unless the working directory happens to be the skill folder. The
commands below always use both absolute paths.

| Command (run via Bash) | What it does |
|---|---|
| `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_init.py <thread_id> --title "..."` | Create the two JSON files at `~/.tinm/threads/<thread_id>.json` and `~/.tinm/artifacts/<thread_id>.json`. Refuses overwrite if the thread already exists. |
| `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_load.py <thread_id> [--n-trajectory N]` | Print a markdown context block (title, last N turns, registered artifacts). Stream the output back to Claude as additional context. Default N=5. |
| `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_update.py <thread_id> --query "..." [--role user]` | Append a turn to the trajectory, EMA-update the anchor with the configured α (default 0.85), respecting the L1 activation threshold (turn ≥ 3 OR anaphora detected in the query). |
| `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_artifact.py <thread_id> add --id <slug> --name "..." --ref "..." --summary "..." [--alias "..." --alias "..."]` | Register a named artifact (file path, plot, table, …) so later turns can resolve "like the X we did". |
| `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_artifact.py <thread_id> find "<query>" [--k N]` | Look up an artifact by alias substring (cheap) or by cosine similarity on the embedding (fallback). |

## Typical session opening

When the user opens Claude Code on a project that has an associated
thread, the expected flow is:

1. User says `/tinm load tinm-paper-polish` (or similar slug).
2. You run
   `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_load.py tinm-paper-polish`
   via Bash and quote its markdown output back as context.
3. Throughout the session, on each new user message you may call
   `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_update.py <thread_id> --query "<msg>" --role user --client claude-code`
   so the trajectory + anchor stay current. First call per session
   takes ~2 s (model load), subsequent calls ~100 ms.
4. When the user references a previous artifact (anaphoric phrasing,
   "like the X we did"), call
   `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_artifact.py <thread_id> find "<reference>"`
   and surface the top hit (name + ref + summary) to the user.

## What NOT to do

- Do not create a new thread without explicit user instruction; threads
  are user-named.
- Do not write to `~/.tinm/` outside of these scripts.
- Do not echo the raw anchor vector to the conversation — it is opaque
  numeric state, not user-readable content.

## PCP v0 file format reference

Full schema in `mvp/pcp_v0_spec.md` of the TINM repo. v0.1 contract is
file-based JSON, no network, no auth, single-user. Future versions will
add atomic write semantics and cross-machine sync.
