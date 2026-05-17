# TINM MVP — Personal Context Protocol v0 for Claude Code

A small daemon-shaped extension to Claude Code that gives it
**cross-session continuity per work thread**: trajectory, EMA anchor,
named artifact index, persisted as JSON under `$TINM_HOME` (default
`~/.tinm/`). The format ([PCP v0.1](pcp_v0_spec.md)) is intentionally
vendor-neutral — any second client (Claude.ai, OpenClaw, …) that
speaks PCP can read the same threads later.

This is the **Phase 1** scope from the project charter: Claude Code
only, no cross-vendor yet. Cross-vendor and an autonomous UI (Stipple)
come in Phase 2-cross-vendor (see `notes/project_charter.md`).

**Phase 2 cross-machine sync** (same vendor, multiple hosts — e.g. a
Mac laptop ↔ a Linux server over Syncthing-on-Tailscale) is supported as an opt-in
layout: set `TINM_HOME` / `TINM_PCP_DIR` to split the synced PCP store
from the machine-local venv + session marker. See
[`../notes/phase2_vps_runbook.md`](../notes/phase2_vps_runbook.md) for
the full deployment guide.

## What you get

Once installed and Claude Code is restarted, every session on a
TINM-tracked project will:

1. **Auto-inject** the current thread's context (title + last N user
   turns + registered artifacts) at session start, via a
   `SessionStart` hook running `tinm_load.py`.
2. **Auto-persist** every user turn into the trajectory + EMA-update
   the anchor, silently, via a `UserPromptSubmit` hook running
   `tinm_update.py`. (Activation threshold L1 from the paper is on by
   default — TINM engages from turn 3 or on detected anaphora.)
3. **Expose tools** to Claude on demand — `tinm.current_thread`,
   `tinm.load_thread_context`, `tinm.artifact_find`, `tinm.artifact_add`
   — via an MCP server `mvp/mcp_server/tinm_server.py` started by
   Claude Code over stdio.
4. **Slash command `/tinm …`** as a fallback / debug entry point (the
   user can explicitly invoke load/init/update/artifact when wanting
   visible side effects).

## What it does NOT do yet (Phase 1)

- No remote endpoint, no cross-vendor. Only Claude Code reads/writes
  `~/.tinm/`. Adding Claude.ai requires wrapping this MCP server as a
  remote HTTPS endpoint (Phase 2).
- No autonomous UI. The "Stipple-like" UX (floating button,
  next-step suggestions, multi-session assistance) is Phase 2.
- No encryption, no remote sync, no concurrency control (v0.1 trusts
  single-user serial access).
- Anaphora detection in `tinm_update.py` is English-only. The user's
  French queries do not trigger L1 by anaphora, only by turn count
  (≥3).

See [`pcp_v0_spec.md`](pcp_v0_spec.md) §5 for the full deferred-feature
list.

## Install (one-time, ~10 min)

The `<REPO>` placeholder below is the absolute path to the TINM repo on
your machine. If you are still on the worktree branch
`claude/loving-saha-f468b4`, that is
`/Users/user/TNIM/.claude/worktrees/loving-saha-f468b4`. Post-merge,
it becomes `/Users/user/TNIM`.

### Step 1 — Create the TINM virtualenv with Python 3.12+

The MCP Python SDK requires Python ≥ 3.10. On macOS the cleanest path
is brew:

```bash
brew install python@3.12
/usr/local/opt/python@3.12/bin/python3.12 -m venv ~/.tinm/.venv
~/.tinm/.venv/bin/pip install --no-cache-dir -r <REPO>/mvp/requirements.txt
```

The `requirements.txt` pins `numpy<2` because torch (which
sentence-transformers pulls in) is built against the numpy 1.x ABI;
without the pin, `encode()` raises "Numpy is not available" at
runtime.

### Step 2 — Symlink the skill folder (used by hooks + slash command)

```bash
mkdir -p ~/.claude/skills
ln -s <REPO>/mvp/skill ~/.claude/skills/tinm
```

This **must be a symlink, not a copy** — the hooks and the slash
command both invoke scripts at `~/.claude/skills/tinm/tinm_*.py`, and
they need to track the live repo source. If you `cp -r` the folder
instead, you will run stale versions of the scripts.

(Despite the path, this is **not** the Claude Code auto-discovered
"skill" — that path never loaded `tinm` into `available-skills` for
us. The folder is reused here only as a stable, well-known location
for the helper scripts.)

### Step 3 — Symlink the slash command (fallback / debug)

```bash
mkdir -p ~/.claude/commands
ln -s <REPO>/mvp/commands/tinm.md ~/.claude/commands/tinm.md
```

This makes `/tinm load tinm-paper-polish` etc. work in any Claude Code
session.

### Step 4 — Configure the MCP server + hooks in Claude Code

⚠️ **Which config file to edit depends on your Claude Code build**:

- **Claude Code Desktop** (the GUI `Claude.app`) → `~/.claude.json`
- **Claude Code CLI** (the `claude` terminal command, e.g. on a Linux
  VPS) → `~/.claude/settings.json`

Quick check on the box: if `claude --version` works in a terminal,
you're on the CLI build → edit `~/.claude/settings.json`. If you only
ever launch Claude Code by double-clicking the app icon, you're on
Desktop → edit `~/.claude.json`. Both can coexist on the same machine
(Mac with both installed) — configure each in its own file.

Open (or create) the right file and merge in the snippet below.
Replace `<REPO>` with your repo absolute path. **The hooks and the MCP
server all use absolute paths — they will fail silently if you keep
`<REPO>` as a literal placeholder.**

```json
{
  "mcpServers": {
    "tinm": {
      "command": "/Users/user/.tinm/.venv/bin/python",
      "args": ["<REPO>/mvp/mcp_server/tinm_server.py"]
    }
  },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<REPO>/mvp/hooks/session_start.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<REPO>/mvp/hooks/user_prompt.sh"
          }
        ]
      }
    ]
  }
}
```

### Step 5 — Restart Claude Code

Quit Claude Code completely (Cmd+Q on macOS, not just close the
window) and reopen. The MCP server registers and the hooks become
active on the next session.

### Step 6 — Create your first thread

In a Claude Code session, type:

```text
/tinm init tinm-paper-polish --title "TINM paper polish"
```

This creates `~/.tinm/threads/tinm-paper-polish.json` and marks it as
the current thread. Subsequent sessions will auto-load it via the
`SessionStart` hook.

## Usage — what a typical session looks like

```text
[you open a new Claude Code session on the TINM project]

(SessionStart hook fires invisibly; the markdown block from
 tinm_load.py enters Claude's context — title, recent turns, artifacts)

You:    Where were we on the paper polish?
Claude: (replies citing the trajectory it just received as context —
         the BibTeX verification was done, draft is at ~95%, next is
         the LaTeX conversion once the venue is locked.)

(at each of your messages, the UserPromptSubmit hook fires invisibly,
 appending your turn to the thread and EMA-updating the anchor)

You:    Modify the Pareto plot like we did before.
Claude: (invokes the `tinm.artifact_find` MCP tool with query="the
         Pareto plot", gets back the canonical ref to
         paper/figures/fig1_pareto.pdf with summary, applies the
         requested modification.)
```

## Debug / verify the install

```bash
# (a) Is the venv healthy?
~/.tinm/.venv/bin/python -c "import mcp; import sentence_transformers; print('OK')"

# (b) Does the MCP server import cleanly?
~/.tinm/.venv/bin/python -c "
import sys
sys.path.insert(0, '<REPO>/mvp/skill')
from mvp.mcp_server import tinm_server  # noqa
print('MCP server importable')
"

# (c) Does session_start.sh emit the markdown for the current thread?
<REPO>/mvp/hooks/session_start.sh

# (d) Does user_prompt.sh accept a mock prompt?
echo '{"prompt": "test prompt", "session_id": "x"}' \
    | <REPO>/mvp/hooks/user_prompt.sh

# (e) Slash command — try in Claude Code:
/tinm load tinm-paper-polish
```

If (a) fails → venv or deps issue (re-run Step 1).
If (b) fails → import path issue in `tinm_server.py`.
If (c) emits nothing → no thread resolved for this cwd. Under v0.2.3
   the SessionStart hook auto-creates a thread from the cwd (git
   toplevel basename inside a repo, `<basename>-<short-sha>` outside),
   so this should be rare. Inspect `~/.tinm/hook-warn-<host>.log` for
   reasons. To force a specific thread, `/tinm init <slug>` once.
If (d) errors → `~/.claude/skills/tinm` is not a symlink (you `cp -r`'d
   the folder instead). Re-run Step 2.

## File layout (state)

```
$TINM_HOME/                       # default ~/.tinm — machine-local
├── session-<id>.thread           # per-session frozen thread name (v0.2.3+);
│                                 #   written by SessionStart, deleted by Stop.
├── .venv/                        # Python venv (sentence-transformers, mcp, ...)
│                                 #   arch-specific, NEVER sync across hosts
├── hook-warn-<host>.log          # per-host hook diagnostics (gate refusals, etc.)
└── pcp/                          # = $TINM_PCP_DIR; default $TINM_HOME/pcp.
    │                             #   SAFE to point at a synced folder
    │                             #   (Syncthing / iCloud / Tailscale Drive).
    ├── threads/
    │   └── <thread_id>.json      # user threads — trajectory + anchor + metadata
    ├── artifacts/
    │   └── <thread_id>.json      # named artifacts (L4)
    └── seeds/                    # v0.2.3+: bundled demo seeds (read-only).
        ├── <seed_id>.json
        └── <seed_id>.artifacts.json
```

The split is the single load-bearing design decision for cross-machine
sync: only `$TINM_PCP_DIR` needs to be shared, while per-session
handoff files and `.venv/` (architecture-specific Python deps) must
stay local. v0.2.3 retired the legacy `current_thread` global pointer
— see [`../MIGRATION_v023.md`](../MIGRATION_v023.md).

Override either path via env (e.g. in `~/.zshrc`):

```bash
export TINM_HOME="$HOME/.tinm"                    # machine-local base
export TINM_PCP_DIR="$HOME/Syncthing/tinm-pcp"    # synced folder
```

Migrating an existing Phase-1 install (`~/.tinm/threads`,
`~/.tinm/artifacts`) to the new layout is a single command:

```bash
<REPO>/mvp/scripts/migrate_to_pcp_subdir.sh
```

The script is idempotent and refuses to overwrite a non-empty target.

## Repo layout (source)

```
mvp/
├── pcp_v0_spec.md                # the PCP v0.1 protocol (read this first)
├── README.md                     # this file
├── requirements.txt              # Python deps (numpy<2 pinned, mcp[cli])
├── commands/
│   └── tinm.md                   # the /tinm slash command (fallback)
├── hooks/
│   ├── session_start.sh          # SessionStart hook — auto-load
│   └── user_prompt.sh            # UserPromptSubmit hook — auto-update
├── mcp_server/
│   └── tinm_server.py            # FastMCP server (4 tools, stdio)
├── scripts/
│   └── migrate_to_pcp_subdir.sh  # one-time Phase 1 -> Phase 2 layout migration
└── skill/                        # CLI scripts (called by hooks + MCP server)
    ├── SKILL.md                  # legacy skill manifest (unused path —
    │                             #   auto-discovery never worked, see
    │                             #   commit 2644508 for the diagnosis)
    ├── tinm_paths.py             # TINM_HOME / TINM_PCP_DIR resolver
    ├── tinm_init.py
    ├── tinm_load.py
    ├── tinm_update.py
    └── tinm_artifact.py
```

`skill/SKILL.md` is kept for archival reasons (and in case Claude
Code's user-level skill auto-discovery starts working in a future
release). It is **not** part of the active invocation path.
