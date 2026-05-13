# TINM clients

Per-client setup paths. The TINM MCP server (`mvp/mcp_server/
tinm_server.py`) is the same binary for every client; what differs is:

- where to register it (the client's MCP config file),
- whether the client supports auto-load of MCP resources,
- whether the client supports lifecycle hooks natively or needs the
  universal system prompt to drive the tool calls.

**Claude Code** is the only client today with native lifecycle hooks,
which is why TINM is fully passive there. Every other MCP-stdio client
runs in **almost-passive** mode via:

- the **MCP resource `tinm://current-thread`** — auto-loaded at
  session start by clients that support MCP resources;
- the **universal system prompt** — pasted once into the client's
  custom-instructions / rules surface, telling Claude to call
  `tinm.record_turn` on every user message and to use the other tinm
  tools as appropriate.

Common pattern (steps 2-4 below):

1. Install the TINM venv + symlinks on this host (same as
   `../README.md` steps 1-3 — the Python deps are identical).
2. Register the TINM MCP server in **this client**'s MCP config.
3. Paste [`UNIVERSAL_SYSTEM_PROMPT.md`](UNIVERSAL_SYSTEM_PROMPT.md) into
   **this client**'s system-prompt / custom-instructions / rules
   surface. *(Claude Code skips this step — it uses hooks instead.)*
4. Quit and reopen the client.

Per-client paths are listed below. None of them differs by hardware
(Mac mini vs MacBook vs Linux server vs Mac vs Windows on the same OS
share the same paths).

---

## Claude Code

The reference / canonical client. Has a dedicated walk-through in
[`../README.md`](../README.md) and a ready-to-merge snippet at
[`../install_snippet.json`](../install_snippet.json).

> ⚠️ **Two distinct config files depending on the build you use.** We
> learned the hard way on 2026-05-13: Claude Code ships as **two
> different products** that read **different config files** for hooks
> and MCP servers. Putting the snippet in the wrong file means the
> hooks silently never fire — the file you edited just isn't the file
> that Claude Code reads.

| Build | Config file for hooks + MCP | Typical platform |
|---|---|---|
| **Claude Code Desktop** (the GUI `Claude.app`) | `~/.claude.json` | Mac, Windows |
| **Claude Code CLI** (the `claude` command in a terminal, installed via npm or installer) | `~/.claude/settings.json` | Linux server / VPS, also available on Mac and Windows |

Both builds use the same `mcpServers` + `hooks` schema below; only the
file path differs.

**Quick check on the box you're configuring**: if `claude --version`
works in a terminal, you're on the **CLI** build → use
`~/.claude/settings.json`. If you only ever launch Claude Code by
double-clicking the Claude app icon, you're on **Desktop** → use
`~/.claude.json`. You may have both on the same machine (e.g. Mac with
the app + CLI installed) — in that case configure each in its own
file.

**Snippet to merge** (replace `<REPO>` with the absolute path of your
TINM repo on this host — e.g. `/Users/<you>/TNIM` on Mac, `/root/TNIM`
or `/home/<you>/TNIM` on Linux/VPS, `C:\Users\<you>\TNIM` on Windows):

```json
{
  "mcpServers": {
    "tinm": {
      "command": "<TINM_HOME>/.venv/bin/python",
      "args": ["<REPO>/mvp/mcp_server/tinm_server.py"]
    }
  },
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command",
                    "command": "<REPO>/mvp/hooks/session_start.sh" }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command",
                    "command": "<REPO>/mvp/hooks/user_prompt.sh" }] }
    ]
  }
}
```

> ⚠️ **The hook commands must point at the wrappers `session_start.sh`
> and `user_prompt.sh`, not at an inline shell command.** The wrappers
> do three things: (1) call the TINM CLI to update the local trajectory,
> (2) `git add && commit` the change, and (3) `git push` in the
> background. An inline command that only calls `tinm_update.py`
> persists locally but never publishes — and your other machines never
> see what you wrote. We hit this on 2026-05-13 with the VPS.

**No system prompt needed** on Claude Code — the `SessionStart` hook
injects the thread context directly into Claude's view, and
`UserPromptSubmit` persists every turn. The universal system prompt
is for clients *without* hooks.

**Multi-host**: same install on each host. Cross-machine memory comes
from the git-backed PCP store (see `../../notes/phase2_vps_runbook.md`
for the Mac↔VPS / Mac↔Mac-mini / Mac↔Linux walk-through).

**Apply changes**: hooks and MCP servers are **read at process start**,
never reloaded at runtime. After editing the config file, fully quit
Claude Code (Cmd+Q on Mac, `exit` then re-launch for the CLI) — not
just "new session". Existing sessions keep the old (or empty) hook
table until the process is restarted.

---

## Claude Desktop (Mac / Windows)

**MCP config**: `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).

Merge into the `mcpServers` key:

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

**Custom instructions**: Settings → Profile → Custom Instructions.
Paste the block from [`UNIVERSAL_SYSTEM_PROMPT.md`](UNIVERSAL_SYSTEM_PROMPT.md)
("Paste this verbatim" section).

**Resource auto-load**: Claude Desktop loads MCP resources into context
when the user references them, not always automatically — that's why
the system prompt explicitly instructs `tinm.load_thread_context()` at
session start.

**Verify**: open a new chat, ask "What thread am I on?". Claude should
call `tinm.current_thread`, then `tinm.load_thread_context`, and
report the active thread.

---

## Cursor

**MCP config**: Settings → MCP → "Add new MCP Server". Configure with:
- Command: `/Users/<you>/.tinm/.venv/bin/python`
- Args: `/Users/<you>/TNIM/mvp/mcp_server/tinm_server.py`

**Rules file**: `.cursorrules` at the project root. Paste the block
from [`UNIVERSAL_SYSTEM_PROMPT.md`](UNIVERSAL_SYSTEM_PROMPT.md). The
rule file scopes to that project — if you want TINM globally, put it
in Cursor's "General Rules" instead (Settings → Rules → General).

---

## Cline (VS Code extension)

**MCP config**: VS Code Settings, search "Cline MCP Servers", add:

```json
{
  "tinm": {
    "command": "/Users/<you>/.tinm/.venv/bin/python",
    "args": ["/Users/<you>/TNIM/mvp/mcp_server/tinm_server.py"]
  }
}
```

**Rules file**: `.clinerules` at the project root. Paste the universal
prompt block.

---

## OpenClaw

OpenClaw's MCP integration is documented at
https://docs.openclaw.ai/cli/mcp — paths and config format pending a
probe session (see `notes/clients_roadmap.md` §2.3). The universal
prompt should apply once the registration step is mapped.

---

## What you should NOT need to do

- You should not need to write any code, just paste two blocks
  (MCP server config + system prompt — or just one block on Claude
  Code, where the hooks take over).
- You should not need to repeat the install per project. The MCP server
  is per-host; the universal prompt at the user-level / global rules
  surface covers every project.
- You should not need to manually call any `tinm.*` tool — Claude does
  it driven by the system prompt (or by the hooks on Claude Code).

## Multi-host setup

If you run TINM on more than one machine (Mac + VPS, Mac + Mac mini,
laptop + desktop, …), each host gets its own install but shares the
PCP store (threads + artifacts) via a private git repo. The
cross-machine setup is one walk-through, regardless of which clients
each host runs:

[../../notes/phase2_vps_runbook.md](../../notes/phase2_vps_runbook.md)

On a host where you run multiple clients (e.g. Claude Code AND Claude
Desktop on the same Mac), do step 2-3 once per client — the MCP
server, venv, and PCP store are shared.

---

## Troubleshooting — known pitfalls

These are the install bugs we have hit and how to recognise them. If
your install looks like one of these, do the fix listed; don't chase
ghosts.

### 1. Hooks never fire — the log file stays empty

**Symptom**: Claude is happily replying, but `git -C ~/.tinm/pcp log
--oneline` shows no new commits no matter how many messages you type.
Inserting a `echo "fired" >> /tmp/tinm_hook.log` at the top of
`user_prompt.sh` does not produce the log either — the script is
literally not being invoked.

**Root cause**: the hook is in the wrong config file for your build of
Claude Code. Desktop reads `~/.claude.json`. CLI reads
`~/.claude/settings.json`. Check `claude --version` to know which build
you're on, then verify the hook is in the right file:

```bash
~/.tinm/.venv/bin/python -c "
import json, os
for p in [os.path.expanduser('~/.claude.json'),
          os.path.expanduser('~/.claude/settings.json')]:
    if not os.path.exists(p): continue
    c = json.load(open(p))
    print(p, '→', 'hooks present' if c.get('hooks',{}).get('UserPromptSubmit') else 'no hooks')
"
```

### 2. Hooks fire, the trajectory grows, but the other machine never sees the writes

**Symptom**: `git -C ~/.tinm/pcp log` shows new commits locally, but
your peer machine never picks them up even after restart.

**Root cause**: the hook command does `tinm_update.py` (or the
equivalent inline) but does not `git push`. The PCP store is updated
on this machine and nowhere else.

**Fix**: point the hook at the wrapper, not at an inline command. The
canonical commands are `<REPO>/mvp/hooks/session_start.sh` and
`<REPO>/mvp/hooks/user_prompt.sh` — both include the
`git add && commit && push` step after the update. If you have a
hook entry that calls `tinm_update.py` directly with no follow-up
`git push`, replace it with the wrapper.

### 3. Configured the right file, still no commits

**Symptom**: hook entries are in the right config file, the
`/tmp/tinm_hook.log` smoke test from §1 shows entries — but new commits
still don't appear when you write a real message in Claude Code.

**Root cause**: your Claude Code session was started **before** you
edited the config file. Hooks and MCP servers are loaded once at
process start, never reloaded at runtime.

**Fix**: fully quit Claude Code (Cmd+Q for Desktop; `exit` and reopen
for the CLI) — not just "new session inside the same Claude process".
Then write a message and re-check `git log`.

### 4. The local hook works in `bash -x`, fails when Claude Code runs it

**Symptom**: manual test `echo '{"prompt":"x"}' | bash -x
.../user_prompt.sh` works end-to-end, including the `git push`. But
Claude Code's invocation of the same script never reaches the
`git push` (you see a local commit, but the branch stays "ahead of
origin/main").

**Root cause**: the `git push` runs in a detached subshell (`& at end
of the block`), and the subshell takes ~1-2s to finish the network
round-trip. If you check `git status` right after typing a message,
you may be checking before the push has completed. Wait ~5s and
check again.

If the commit *never* arrives at origin even minutes later, the SSH
key on this host is not authorised for the GitHub repo — test with
`ssh -T git@github.com` and `git -C ~/.tinm/pcp push` to surface the
real error.
