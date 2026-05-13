# TINM — Clients roadmap

How we extend TINM's reach beyond Claude Code, one client at a time,
and the open design question (auto-persist without hooks) we have to
solve before MCP-only clients can match the Claude Code experience.

This is a **living document**. When a client is added or its status
changes, update both the matrix here and the status table in the
top-level [`README.md`](../README.md).

---

## Compatibility matrix (May 2026)

Status legend:
- ✓ shipped and dogfooded
- ◐ partial — works manually, no auto-persist
- ◇ feasible, planned, not built
- ✗ blocked by missing protocol surface

| Client | MCP server | Auto-load context (session start) | Auto-persist turn (user prompt) | Auto-init project thread | E2E status |
|---|---|---|---|---|---|
| **Claude Code** (CLI, Mac/Linux) | ✓ stdio | ✓ SessionStart hook | ✓ UserPromptSubmit hook | ✓ via `tinm_auto_init.py` | ✓ Phase 1+2 shipped, dogfooded |
| **Claude Desktop** (Mac/Windows) | ✓ stdio | ◐ via MCP resources if client auto-reads them | ◐ rules-file instruction (see §3) | ◐ user runs `tinm.thread_init` once via Claude | ◇ next priority |
| **Cursor** (IDE) | ✓ stdio | ◇ `.cursorrules` instruction | ◐ `.cursorrules` instruction | ◐ Claude tool call | ◇ planned |
| **OpenClaw** (open-source Claude client) | ✓ stdio + ["Claude-specific channel notifications"](https://docs.openclaw.ai/cli/mcp) | ◇ unknown — may map to hooks | ◇ unknown | ◇ unknown | ◇ research needed; matrix to refine after a probe session |
| **Cline** (VS Code extension) | ✓ stdio | ◐ `.clinerules` instruction | ◐ `.clinerules` instruction | ◐ Claude tool call | ◇ planned |
| **Claude.ai** (web/desktop browser) | requires remote MCP HTTPS server | ✗ until we host the remote server | ✗ same | ✗ same | ◇ deferred — needs an HTTPS endpoint on the VPS |
| **ChatGPT** | non-MCP — Custom GPT actions | ✗ different protocol entirely | ✗ | ✗ | ✗ deprioritised |

The MCP server in `mvp/mcp_server/tinm_server.py` is **already
reusable for every MCP-stdio client** — the same binary, the same five
tools (`current_thread`, `load_thread_context`, `thread_init`,
`artifact_find`, `artifact_add`). What is missing for the ◐ clients is
not the server but the *triggering* layer: who calls these tools, when,
without the user typing anything.

---

## 1. The auto-persist-without-hooks problem

Claude Code is the only client today that gives us first-class hook
points (`SessionStart`, `UserPromptSubmit`) where TINM can run silently
on every session boundary and every user prompt. For every other MCP
client, the protocol does not yet have a "lifecycle hooks" surface —
the client decides when to call MCP tools, not the server.

Consequence: on Claude Desktop / Cursor / OpenClaw / Cline, TINM
defaults to **read-only "consultative" mode**:

- The MCP server is available.
- Claude can call `load_thread_context` when the user asks "where was
  I?" — and it works perfectly.
- But the thread does not *grow* automatically as the user has new
  conversations on that client. Only Claude Code sessions append turns
  to the trajectory.

For Phase 2 (multi-host single-vendor Claude Code) this is fine — both
hosts use Claude Code, both have hooks. For Phase 2-cross-vendor
(adding Claude Desktop, Cursor, …), it becomes the load-bearing
question.

### 1.1 Options on the table

| # | Approach | Effort | UX | Coverage |
|---|---|---|---|---|
| **A** | **Per-client rules file** — `.cursorrules`, `.clinerules`, Claude Desktop system prompt: "after every user message, call `tinm.record_turn`; at session start, call `tinm.load_thread_context`" | low (~half-day) | one-time per-project setup; "almost passive" once installed | all rules-supporting clients |
| **B** | **MCP `prompt` capability** — server exposes a discoverable "Load TINM context" prompt the user can invoke (Cmd-P style) at session start | low (~half-day) | manual click per session — covers load, not persist | clients that surface MCP prompts |
| **C** | **MCP `resource` auto-load** — server exposes `tinm://current-thread` as a resource that some clients auto-include in context | low — already a few lines in tinm_server.py | passive load only; no persist | clients that auto-load MCP resources (Claude Desktop, some) |
| **D** | **Local daemon side-channel** — watch each client's session log (`~/.cursor/sessions`, `~/.cline/`, …), parse turns, replay into `tinm_update.py` | high (~2-4 days per client) | fully passive — matches Claude Code UX | every client that writes session logs |
| **E** | **Propose MCP lifecycle-hooks extension upstream** — get `SessionStart` / `OnTurn` standardised in MCP so server-side handlers run on every client | very high (months — protocol change) | best long-term UX, zero per-client work | every MCP client once they adopt the new spec |
| **F** | **Open-source client plugins** — write a TINM plugin for OpenClaw, Cline, Aider, etc. that hooks into their own extension points | medium (~1 day per client) | passive, native | only open-source / pluggable clients |

### 1.2 Recommended sequencing

1. **Short-term (this week)**: ship Option **A** (rules files) for
   Cursor + Cline + Claude Desktop, plus Option **C** (MCP resource)
   for free. That brings the ◐ clients to "auto-load passive,
   prompt-instructed persist" — almost as good as Claude Code with one
   small per-project setup step.
2. **Medium-term (2-4 weeks of real usage)**: pick the most-used non-
   Claude-Code client, write a real plugin (Option **F**) so TINM
   becomes fully passive there too. The order is data-driven — wait
   to see which client you actually use.
3. **Long-term (months)**: file an MCP RFC for lifecycle hooks
   (Option **E**) and contribute the reference implementation. Once
   merged, every MCP client gets passive TINM for free.

Option **D** (daemon) stays as a fallback if a high-value client
refuses to expose extension points and the user volume justifies the
maintenance.

---

## 2. Per-client plans

### 2.1 Claude Desktop (next priority)

Per Anthropic's docs, Claude Desktop runs MCP servers configured in
its settings file and supports MCP resources. To bring TINM to it:

- Reuse `mvp/mcp_server/tinm_server.py` as-is — it's stdio + FastMCP,
  identical setup.
- Add a `tinm://current-thread` MCP resource exposing the markdown
  context block (Option **C**). Tiny patch to the server.
- Write `mvp/clients/claude-desktop/system-prompt.md` containing the
  Option **A** rules ("at every turn, call `tinm.record_turn`…").
  Document where to paste it in Claude Desktop settings.
- Document the install in
  `mvp/clients/claude-desktop/README.md`.

### 2.2 Cursor

- Reuse the MCP server.
- Add `mvp/clients/cursor/.cursorrules` shippable file.
- Document the install in `mvp/clients/cursor/README.md`.

### 2.3 OpenClaw (research first)

OpenClaw docs mention "Claude-specific channel notifications" as an
MCP extension. Spend half a day reading
https://docs.openclaw.ai/cli/mcp and https://github.com/freema/openclaw-mcp
to find out whether those notifications fire on session boundaries or
user prompts — if yes, OpenClaw might give us true hooks parity with
Claude Code. Update the matrix and `2.3.x` plan after the probe.

### 2.4 Cline

- Reuse the MCP server.
- Add `mvp/clients/cline/.clinerules` shippable file.

### 2.5 Claude.ai (web)

- Build a remote MCP HTTPS endpoint (FastMCP supports HTTP transport).
- Deploy on the VPS Hostinger (already provisioned).
- Document OAuth2 / API key handling.
- This is significantly bigger than the rules-file clients (~2 days)
  and is *not* the next step — it gates on the user choosing to
  prioritise it.

### 2.6 ChatGPT

- Deprioritised. The protocol is OpenAI Actions / Custom GPTs, which
  is a completely different shape than MCP. We'd build a separate
  HTTPS endpoint with OpenAPI spec, manage OAuth there, etc.
- Worth ~3 days when (and if) we decide it's strategically necessary.

---

## 3. How to add a new client (template)

When you add a new MCP-compatible client to TINM:

1. Add a row to the matrix above (status `◇`).
2. Create `mvp/clients/<client>/` with:
   - `README.md` — install steps for this client specifically.
   - `system-prompt.md` or `.<client>rules` — the Option-A instruction
     block, copy-pasteable.
   - Any client-specific config snippet (the equivalent of
     `mvp/install_snippet.json` for Claude Code).
3. Update the matrix status as you progress from `◇` to `◐` to `✓`.
4. Update the status table in the top-level `README.md`.
5. Sanity-check end-to-end: in a fresh session on this client, run the
   "where was I?" use case and confirm the trajectory loads.
