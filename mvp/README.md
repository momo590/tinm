# TINM MVP — Personal Context Protocol v0 skill for Claude Code

A weekend-scope prototype that gives Claude Code cross-session
continuity by maintaining a TINM-style anchor + trajectory + artifact
index per "work thread", persisted as JSON in `~/.tinm/`. The format
(PCP v0.1) is documented in [`pcp_v0_spec.md`](pcp_v0_spec.md) and is
designed to be readable by any second client (Claude.ai, ChatGPT,
OpenClaw, …) that implements it.

## What it does today (v0.1)

- Initialise a thread (`tinm init`).
- Load a thread at session start — surfaces the title, recent turns,
  and named artifacts to Claude as context (`tinm load`).
- Update the anchor + trajectory on each user turn (`tinm update`).
  Respects the L1 activation threshold from the paper (default on:
  TINM engages from turn 3 or when anaphora is detected).
- Register and resolve named artifacts (`tinm artifact add` / `find`).

## What it does NOT do yet

- No background watcher; the user must invoke `/tinm …` explicitly.
- No second-client validation. Until a Claude.ai or ChatGPT client
  reads a thread written by Claude Code, the PCP v0 contract is only
  half-validated.
- No encryption, no remote sync, no concurrency control (v0.1 trusts
  single-user serial access).
- The auto-discovered "skill" path in Claude Code did not work for us
  in practice (the skill folder was present but the skill was not
  loaded into `available-skills` even after a full app restart). We
  ship a slash command `~/.claude/commands/tinm.md` as the primary
  invocation path; the SKILL.md remains in the skill folder for the
  day auto-discovery starts working.

See [`pcp_v0_spec.md`](pcp_v0_spec.md) §5 for the full list of what is
out of scope for v0.1.

## Install

The `<REPO>` placeholder below is the absolute path to the TINM repo on
your machine. If you are still on the worktree branch
`claude/loving-saha-f468b4`, that is
`/Users/user/TNIM/.claude/worktrees/loving-saha-f468b4`. After the
branch is merged into `main` and the worktree removed, it becomes
`/Users/user/TNIM`.

```bash
# 1. Symlink the skill source (keeps SKILL.md scripts in one place)
mkdir -p ~/.claude/skills
ln -s <REPO>/mvp/skill ~/.claude/skills/tinm

# 2. Symlink the slash command — this is what makes `/tinm …` work
mkdir -p ~/.claude/commands
ln -s <REPO>/mvp/commands/tinm.md ~/.claude/commands/tinm.md

# 3. Create the TINM virtualenv (Py 3.9 system or any 3.9–3.12 you have)
/usr/bin/python3 -m venv ~/.tinm/.venv
~/.tinm/.venv/bin/pip install --no-cache-dir -r <REPO>/mvp/requirements.txt
```

The `requirements.txt` pins `numpy<2` because torch 2.2 (the wheel
sentence-transformers pulls on Py 3.9) is built against the numpy 1.x
ABI. Without the pin, `encode()` raises "Numpy is not available" at
runtime.

The slash command and the scripts both reference the venv interpreter
by absolute path (`~/.tinm/.venv/bin/python`), so you do not need to
adjust `PATH`.

## Usage in a Claude Code session

```text
You:     /tinm init tinm-paper-polish --title "TINM paper polish"
Claude:  (creates ~/.tinm/threads/tinm-paper-polish.json)

You:     /tinm load tinm-paper-polish
Claude:  (reads the file, surfaces context as markdown)
         # TINM thread: TINM paper polish
         _id: tinm-paper-polish · 0 anchor updates · L1-engaged=False_
         ## Recent trajectory
         _(empty — this is a fresh thread)_

You:     (ask anything; the skill captures the turn in the background)

You:     "Modify the Pareto plot like we did before"
Claude:  (recognises the anaphora, calls tinm_artifact.py find …,
          surfaces the canonical ref + summary)
```

## File layout

```
~/.tinm/
├── threads/
│   └── <thread_id>.json
└── artifacts/
    └── <thread_id>.json
```

JSON schema: [`pcp_v0_spec.md`](pcp_v0_spec.md) §2 (trajectory),
§3 (artifacts).

## Repo layout

```
mvp/
├── pcp_v0_spec.md     # the protocol (read this first)
├── README.md          # this file
├── requirements.txt   # Python deps (numpy<2 pinned)
├── commands/
│   └── tinm.md        # the /tinm slash command (primary invocation path)
└── skill/             # the (currently auto-load-broken) Claude Code skill
    ├── SKILL.md       # triggering instructions for Claude
    ├── tinm_init.py
    ├── tinm_load.py
    ├── tinm_update.py
    └── tinm_artifact.py
```
