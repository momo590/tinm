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
| `~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py [--reset|--remove]` | Install (or remove) the bundled `tinm-tour` demo thread so a fresh installer can experience the cross-session-recall whoa moment without 24h of real usage first. Idempotent; `--reset` re-imports. |

## First-install demo (`/tinm demo`)

A first-time installer has an empty PCP store, so cross-session recall
cannot fire yet. When the user runs `/tinm demo` (or asks something like
"how do I see the demo / try it / show me the whoa moment"), run
`tinm_demo.py` via Bash and then tell the user verbatim:

> Open a fresh Claude Code session and paste:
>
> "What was the biggest absolute effect we measured on the 2WikiMultihopQA pilot?"
>
> TINM will surface the `wiki2hop-results` artifact (`+0.114 F1, t=4.03`)
> without reading any file. That is the whoa moment the README promises.

The seed lives at `mvp/seeds/tinm-tour/` in the TINM repo and is copied
to the user's PCP store on demand. It also sets `~/.tinm/current_thread`
so the next session picks it up automatically. The demo is removable
with `--remove` without touching the user's real threads.

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

## Writing artifact summaries (read this before every `tinm_artifact.py add`)

The `--summary` you pass to `tinm_artifact.py add` is what the user will
read back at session start, days or weeks later, with none of the in-session
context that made the shorthand legible. Write it for re-reading by a human,
not as a compressed agent-internal reference note.

Rules, all enforced by your own care since this is a prompt-time concern, not
a runtime one:

1. **Plain prose, not a reference dump.** One or two coherent sentences (up to
   a short paragraph). Not a slash-separated list of fields, not a checklist.
2. **One language per summary.** Match the language the user has been writing
   in this thread. Do not code-switch French/English mid-sentence even if your
   internal reasoning did.
3. **Expand project shorthand on first use, or skip it.** If you must mention a
   task code like `T2`, write "the LLM provider abstraction task (T2)" the
   first time. Better: omit the code entirely and describe the work. The user
   does not memorise their own task numbers between sessions.
4. **No status emojis in the summary body** (✅ ❌ ⚠️ 🚧). They are visual
   decoration that adds zero recall value. Status belongs in dedicated fields
   if at all.
5. **No inline dated lock phrases.** Do not write "locked 2026-05-16",
   "verrouillé hier", "decided yesterday". The artifact's `created_at` is
   already on disk and surfaces relatively at display time. Inline dates rot.
6. **No file path soup as prose.** If the artifact references a file, put it
   in `--ref`, not inline in the summary. Inline paths break the sentence flow
   and the user already gets the ref displayed alongside.
7. **Self-contained.** Assume the user reads the summary with no surrounding
   conversation. If the summary requires reading three other artifacts to make
   sense, it is not done.

### Good vs bad

**Bad** (what the writer agent tends to produce naturally — fictional example):

> Plan d'architecture verrouillé pour PaymentService v2 après /plan-eng-review +
> outside voice. Stack: Node core + ProviderAdapter abstraction (stripe default,
> braintree opt-in) + Redis idempotency layer + Kafka event bus. 14 tasks
> T1-T14 avec lanes A-F. Approach C strict + deadline 5 jours. T0 BLOCKER
> tête de Lane A.

**Good** (the same artifact, rewritten):

> We locked the PaymentService v2 engineering plan. The service runs on Node
> with an abstraction layer that defaults to Stripe and can switch to
> Braintree if a customer needs it. Redis holds idempotency keys so retries
> are safe, and a Kafka bus emits events to downstream subscribers. The
> first task is to extract the common gateway logic into its own module —
> nothing else can start until that lands. Target landing date is five
> working days out.

The good version is longer in characters and shorter in cognitive load. That
is the trade you are making — and the right one. The bad version is dense to
read, ages poorly, and assumes the reader has the whole project graph in their
head. The good version reads cleanly with zero context.

## What NOT to do

- Do not create a new thread without explicit user instruction; threads
  are user-named.
- Do not write to `~/.tinm/` outside of these scripts.
- Do not echo the raw anchor vector to the conversation — it is opaque
  numeric state, not user-readable content.
- Do not write artifact summaries in shorthand. See "Writing artifact
  summaries" above — the summary you write is what the human reads next
  session, not a note to your future-self.

## PCP v0 file format reference

Full schema in `mvp/pcp_v0_spec.md` of the TINM repo. v0.1 contract is
file-based JSON, no network, no auth, single-user. Future versions will
add atomic write semantics and cross-machine sync.
