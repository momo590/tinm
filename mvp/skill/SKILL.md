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

The scripts live in this directory and rely on a Python environment
with `sentence-transformers` and `numpy` installed (see
`requirements.txt`). The exact Python interpreter to use is whichever
the user has set up — by default invoke them as `python <script>.py`
and let the user's shell/venv resolve.

| Command | What it does |
|---|---|
| `tinm_init.py <thread_id> --title "..."` | Create the two JSON files at `~/.tinm/threads/<thread_id>.json` and `~/.tinm/artifacts/<thread_id>.json`. Refuses overwrite if the thread already exists. |
| `tinm_load.py <thread_id> [--n-trajectory N]` | Print a markdown context block (title, last N turns, registered artifacts). Stream the output back to Claude as additional context. Default N=5. |
| `tinm_update.py <thread_id> --query "..." [--role user]` | Append a turn to the trajectory, EMA-update the anchor with the configured α (default 0.85), respecting the L1 activation threshold (turn ≥ 3 OR anaphora detected in the query). |
| `tinm_artifact.py <thread_id> add --name "..." --ref "..." --summary "..." [--aliases "..."]` | Register a named artifact (file path, plot, table, …) so later turns can resolve "like the X we did". |
| `tinm_artifact.py <thread_id> find <query>` | Lookup an artifact by alias substring (cheap) or by cosine similarity on the embedding (fallback). |

## Typical session opening

When the user opens Claude Code on a project that has an associated
thread, the expected flow is:

1. User says `/tinm load tinm-paper-polish` (or similar slug).
2. You run `tinm_load.py tinm-paper-polish` via Bash and quote its
   markdown output back as context.
3. Throughout the session, on each new user message you may call
   `tinm_update.py ... --query "<the user's message>" --role user` so
   the trajectory + anchor stay current. This is a low-overhead append
   (<50 ms typically).
4. When the user references a previous artifact, call
   `tinm_artifact.py ... find "<reference>"` and surface the hit.

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
