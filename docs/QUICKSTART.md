# TINM v0.1 — 2-minute setup

Someone DM'd you this? Read 30 seconds of context first, then run one line.

**What this is.** TINM watches your Claude Code sessions and stores
named artifacts in a local PCP store (`~/.tinm/pcp/`). Next time you
open Claude Code on a related project, the relevant artifacts come
back automatically — no `/load`, no copy-paste, no expanded
`CLAUDE.md`. Cross-session memory for your AI pair-programmer.

**What it removes.** That ten minutes at the start of every new
session where you re-explain yesterday to Claude.

## Install (macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/momo590/tinm/main/mvp/scripts/install.sh | bash
```

The installer asks one question (telemetry — say `y` for local-only
metrics, `n` if you'd rather not, both are fine). About 3 minutes
total; most of it is the `pip install` of the sentence-transformers
wheel. Everything goes under `~/.tinm/` and `~/.claude/skills/tinm/`,
nothing else touched.

## See cross-session recall on bundled sample data

A fresh install has an empty PCP store, so there's nothing for TINM
to recall yet. The bundled `tinm-tour` demo gives you sample
history to test against:

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py
```

This imports a 5-artifact walkthrough of a real research session
from the TINM paper. Open a fresh Claude Code session and paste,
verbatim:

> What was the biggest absolute effect we measured on the 2WikiMultihopQA pilot?

Claude will answer with a specific number from the demo's stored
artifacts — one it could not have produced without TINM pulling
that artifact into context. That is the mechanism: prior-session
content surfaced into a fresh session, no manual load, no file
read.

To remove the demo cleanly when you're done:

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py --remove
```

## Use it on your own work

```
/tinm init my-project          # creates a real thread under your name
/tinm save "the architecture decision"
```

Keep working. Next time you open Claude Code and ask about anything
that touches that decision, TINM pulls the artifact back. No
`/tinm load` needed — the SessionStart hook handles it.

## Check what TINM is doing

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_status.py
```

Current thread, anchor terms, trajectory length, artifact count. If
you ever wonder "is it working?", this is the answer.

```bash
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_telemetry.py status
```

Telemetry state — opt-out at any moment with `tinm_telemetry.py off`.

## Privacy in 3 lines

Everything is in `~/.tinm/` on your machine. Nothing leaves unless
you ran `tinm_telemetry.py on --share`, and even then it's aggregate
counts (events per skill, install id) — never prompts, never file
contents. The privacy contract is tested in
`mvp/tests/test_telemetry.py`.

## Uninstall (fully reversible)

```bash
bash ~/.tinm/source/mvp/scripts/uninstall.sh
```

Removes `~/.tinm/`, `~/.claude/skills/tinm/`, and the TINM-registered
hooks from `~/.claude/settings.json`. Your other hooks are preserved.
A backup of `settings.json` is left at the same path with a
`.pre-uninstall` suffix.

Keep your thread history for a future reinstall:

```bash
TINM_KEEP_PCP=1 bash ~/.tinm/source/mvp/scripts/uninstall.sh
```

The PCP store moves to `~/tinm-pcp-keep-<timestamp>/` instead of being
deleted.

## Feedback (the data this stage actually needs)

If you tried it: DM [@MmakhtarDiop](https://x.com/MmakhtarDiop) on X with the
one thing that surprised you, broke for you, or did nothing for you.
Beta v0.1 is collecting Sean Ellis answers — *would you be upset if
TINM disappeared tomorrow?* A 1-line "yes because X" or "no because
Y" is worth more than any GitHub star.
