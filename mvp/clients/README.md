# TINM for MCP clients other than Claude Code

Claude Code gets TINM "fully passive" via SessionStart / UserPromptSubmit
hooks. Every other MCP-stdio client (Claude Desktop, Cursor, Cline,
OpenClaw, …) gets TINM in **almost-passive** mode via:

- the **MCP resource `tinm://current-thread`** — auto-loaded at
  session start by clients that support MCP resources;
- the **universal system prompt** — pasted once into the client's
  custom-instructions / rules surface, telling Claude to call
  `tinm.record_turn` on every user message and to use the other tinm
  tools as appropriate.

Common pattern across all clients:

1. Install the TINM venv + symlinks on this host (same as
   `../README.md` steps 1-3 — the Python deps are identical).
2. Register the TINM MCP server in **this client**'s MCP config.
3. Paste [`UNIVERSAL_SYSTEM_PROMPT.md`](UNIVERSAL_SYSTEM_PROMPT.md) into
   **this client**'s system-prompt / custom-instructions / rules surface.
4. Quit and reopen the client.

Per-client paths are listed below. The MCP server (`mvp/mcp_server/
tinm_server.py`) and the universal prompt do not change.

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
  (MCP server config + system prompt).
- You should not need to repeat the install per project. The MCP server
  is per-host; the universal prompt at the user-level / global rules
  surface covers every project.
- You should not need to manually call any `tinm.*` tool — Claude does
  it driven by the system prompt.
