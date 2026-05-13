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

## Claude Code (CLI — Mac, Linux, Windows)

The reference / canonical client. Has its own dedicated walk-through
in [`../README.md`](../README.md) and a ready-to-merge snippet at
[`../install_snippet.json`](../install_snippet.json).

**MCP + hooks config file**: `~/.claude.json` (same on every OS —
the path is the OS-agnostic per-user config).

**Snippet to merge** (replace `<REPO>` with the absolute path of your
TINM repo on this host — e.g. `/Users/<you>/TNIM` on Mac /Mac mini,
`/home/<you>/TNIM` on Linux/VPS, `C:\Users\<you>\TNIM` on Windows):

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

**No system prompt needed** — Claude Code's SessionStart hook injects
the thread context directly into Claude's view, and UserPromptSubmit
persists every turn. The universal system prompt is for clients
without hooks.

**Multi-host**: same install on each host. Cross-machine memory comes
from the git-backed PCP store (see `../../notes/phase2_vps_runbook.md`
for the Mac↔VPS / Mac↔Mac-mini / Mac↔Linux walk-through).

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
