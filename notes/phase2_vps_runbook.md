# Phase 2 — TINM cross-machine setup (Mac ↔ VPS Hostinger)

Approach locked on 2026-05-13: **git private repo as PCP storage**, no
new daemons. The PCP store (`$TINM_PCP_DIR`) is initialised as a git
repo on each host, pointing at a private GitHub repo. The TINM hooks
do the sync transparently:

- `SessionStart` → `git pull --rebase --autostash` (best-effort, silent).
- `UserPromptSubmit` → after the turn is persisted locally, fork a
  background `git add threads/ artifacts/ && git commit && git push`.

Per `mvp/pcp_v0_spec.md` §5, sync remains a filesystem-tool concern;
this just picks git as the tool. Conflicts (rare under single-user
serial access) surface as git merge errors and are resolved by hand.

---

## Architecture

```
   GitHub private repo  ──pull (SessionStart)──►  Mac    $TINM_PCP_DIR/.git/
        tinm-pcp                                  Mac    Claude Code + hooks
            ▲                                     │
            │                                     │ push after each turn
            │ push after each turn                │
            │                                     ▼
            └──pull (SessionStart)──────────►   VPS    $TINM_PCP_DIR/.git/
                                                VPS    Claude Code + hooks
```

`current_thread` and the Python `.venv` stay machine-local (top of
`$TINM_HOME`), so each host has its own active-session marker and its
own arch-specific deps.

---

## Step list (~15 min total)

### 1. Create the private GitHub repo (1 min, manual)

In a browser: github.com → New repository → **private**, name
`tinm-pcp`, **empty** (no README, no .gitignore). Copy the SSH URL
(`git@github.com:<you>/tinm-pcp.git`).

### 2. Wire the Mac side (3 min, scripted)

```bash
~/TNIM/mvp/scripts/setup_git_sync.sh git@github.com:<you>/tinm-pcp.git
```

This script:
- creates `$TINM_PCP_DIR/.git` (or reuses it),
- registers the remote,
- pushes the existing threads/artifacts on the first run.

Pre-req: the Mac already has an SSH key authorized for GitHub (the
script will tell you if it doesn't; verify with `ssh -T git@github.com`).

### 3. Wire the VPS side (handed off to the VPS Claude Code instance)

Open Claude Code on the VPS. Paste the bootstrap prompt provided
separately (see [`vps_bootstrap_prompt.md`](vps_bootstrap_prompt.md)).
The VPS Claude:
- installs Python 3.12 + git,
- clones the TINM repo,
- builds the venv + symlinks,
- generates an SSH key, prints the public key, **pauses for you to
  add it to GitHub**,
- clones the `tinm-pcp` repo into `$TINM_PCP_DIR`,
- wires `~/.claude.json` (MCP + hooks).

### 4. Verify end-to-end (2 min)

1. On the Mac, in Claude Code: `/tinm load tinm-paper-polish`.
2. On the VPS, in a fresh Claude Code session: the `SessionStart` hook
   pulls from GitHub, the trajectory + artifacts appear automatically.
3. On the VPS, ask Claude `tinm.artifact_add` for a small test
   artifact (e.g. name "Phase 2 VPS first artifact"). The hook
   auto-commits and pushes.
4. On the Mac, `/tinm load tinm-paper-polish` again → the new artifact
   is visible. Cross-machine loop closed.

---

## Conflict handling

Single-user serial access is the v0.1 contract. If you happen to write
on both hosts within a sync round-trip, `git pull --rebase` will fail
on the second SessionStart with an unresolved merge.

Symptoms: `SessionStart` hook stays silent, `tinm.load_thread_context`
returns an outdated view.

Recovery: from a terminal on the affected host,

```bash
cd "$TINM_PCP_DIR"
git status                  # see which file conflicts
# pick the winner manually, then:
git add <file>
git rebase --continue
git push
```

Or, if the local copy is the loser:
```bash
cd "$TINM_PCP_DIR"
git rebase --abort
git reset --hard origin/main
```

(`git reset --hard` is local-only here — it discards in-progress local
turns, not remote ones.)

---

## Claude Desktop Mac (hooks not supported)

The Claude Code hooks (`SessionStart`, `UserPromptSubmit`) only fire in
Claude Code CLI / desktop app in "Claude Code" mode. The macOS **Claude
Desktop** app (claude.ai/desktop) does **not** run hooks.

TINM still works on Claude Desktop via the MCP server. Two setup steps:

### Step 1 — Register the MCP server in Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
and add the `tinm` entry to `mcpServers` (same JSON block you have in
`~/.claude.json`):

```json
{
  "mcpServers": {
    "tinm": {
      "command": "/Users/<you>/.tinm/.venv/bin/python",
      "args": ["/Users/<you>/TNIM/mvp/mcp_server/tinm_server.py"]
    }
  }
}
```

Restart Claude Desktop. The `tinm.*` tools and the `tinm://current-thread`
resource should now appear.

### Step 2 — Add the universal system prompt

In Claude Desktop: **Settings → Custom Instructions** → paste the full
contents of [`mvp/clients/UNIVERSAL_SYSTEM_PROMPT.md`](../mvp/clients/UNIVERSAL_SYSTEM_PROMPT.md)
(the code block inside it, verbatim).

### What this covers

| Feature | Claude Code (hooks) | Claude Desktop (MCP only) |
|---|---|---|
| Session start context | `SessionStart` hook | `tinm://current-thread` resource (auto-loaded) |
| Per-turn trajectory | `UserPromptSubmit` hook | `record_turn` called by Claude per system prompt |
| Artifact find/add | `mcp__tinm__artifact_find/add` | same MCP tools |
| Phase 2 git sync | hooks push/pull | NOT automatic — Claude would need to trigger it or you run `git push` manually from terminal |

**Note on Phase 2 + Claude Desktop**: the background git push in
`user_prompt.sh` doesn't fire from Claude Desktop. If you want cross-machine
sync to also propagate Claude Desktop turns, run `git push` manually in
`$TINM_PCP_DIR` after important sessions, or accept that Claude Desktop turns
sync at the next Claude Code session start.

---

## What this approach does NOT cover

- **Cross-vendor sync** (Claude.ai, ChatGPT). Original Phase 2 of the
  charter, still deferred.
- **Encryption beyond GitHub's at-rest + in-transit**. The repo is
  private, but GitHub staff has technical access. Threads carry
  conversational metadata only, no secrets — if that ever changes,
  reconsider the storage layer.
- **Real-time push notifications**. Propagation is best-effort: a turn
  written on the Mac shows up on the VPS at the VPS's next
  `SessionStart`. For sub-second sync you'd want a daemon — out of
  scope for v0.1.
