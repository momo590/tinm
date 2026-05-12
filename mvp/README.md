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

- No background watcher; the skill is invoked manually via `/tinm …`.
- No second-client validation. Until a Claude.ai or ChatGPT client
  reads a thread written by Claude Code, the PCP v0 contract is only
  half-validated.
- No encryption, no remote sync, no concurrency control (v0.1 trusts
  single-user serial access).

See [`pcp_v0_spec.md`](pcp_v0_spec.md) §5 for the full list of what is
out of scope for v0.1.

## Install

```bash
# 1. Symlink the skill source into Claude Code's user skills directory
mkdir -p ~/.claude/skills
ln -s /Users/user/TNIM/mvp/skill ~/.claude/skills/tinm

# 2. Install Python dependencies in a venv that Claude Code can call
python3.11 -m venv ~/.tinm/.venv
source ~/.tinm/.venv/bin/activate
pip install -r /Users/user/TNIM/mvp/requirements.txt

# 3. Add the venv to PATH (optional, makes the scripts callable as commands)
# Edit ~/.zshrc:
#   export PATH="$HOME/.tinm/.venv/bin:$PATH"
```

The skill assumes `python` resolves to an interpreter that has
`sentence-transformers` and `numpy` available. Adjust the install
step if you prefer a project-local venv.

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
├── requirements.txt   # Python deps
└── skill/             # source of the Claude Code skill
    ├── SKILL.md       # triggering instructions for Claude
    ├── tinm_init.py
    ├── tinm_load.py
    ├── tinm_update.py
    └── tinm_artifact.py
```
