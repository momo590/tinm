# RFC (draft) — MCP Lifecycle Hooks

**Status**: draft, not yet submitted upstream.
**Authors**: Makhtar DIOP, TINM project.
**Created**: 2026-05-13.
**Target spec**: Model Context Protocol — server capability extension.

## Problem

Today's MCP protocol lets a server expose three kinds of capabilities
to a client: **tools** (callable functions), **resources** (data the
client may include in context), and **prompts** (templates the user
may invoke). All three are *pulled* by the client — the server cannot
*push* on lifecycle events.

This is a problem for any MCP server that needs to maintain state
synchronised with the user's conversation flow. Concrete example
(TINM, but the pattern is general):

- The server wants to know **when a new session starts**, so it can
  return a relevant memory snapshot to the client.
- The server wants to know **when the user sends a message**, so it
  can append that message to a persistent trajectory the next session
  will load.
- The server wants to know **when the session ends**, so it can flush
  state or trigger summarisation.

Today, these tasks fall on the client (cf. Claude Code's
`SessionStart` / `UserPromptSubmit` hooks). The result: a great
experience on Claude Code, but degraded "almost-passive" behaviour on
every other MCP client because they don't expose those hooks. Each
MCP server that needs the same lifecycle awareness ends up
re-inventing the same workaround (instructing the model via prompt to
call a tool on every turn) — fragile, model-dependent, and inelegant.

## Proposal

Add a **`lifecycleHooks`** capability to the MCP server announcement.
A server that advertises this capability declares which lifecycle
events it wants the client to notify it of. The client, on each event,
sends a `notifications/lifecycle/<event>` JSON-RPC notification to the
server.

### Capability negotiation

In the server's `initialize` response, alongside `tools`, `resources`,
`prompts`:

```json
{
  "capabilities": {
    "lifecycleHooks": {
      "events": ["session/start", "user/message", "session/end"]
    }
  }
}
```

The client MUST respond with which of those events it can deliver
(some clients can't observe all of them):

```json
{
  "capabilities": {
    "lifecycleHooks": {
      "supportedEvents": ["session/start", "user/message"]
    }
  }
}
```

The server then knows whether to expect events or fall back to its
prompt-instructed workaround.

### Notification format

```jsonc
// On session start
{
  "jsonrpc": "2.0",
  "method": "notifications/lifecycle/session_start",
  "params": {
    "sessionId": "abc-123",
    "client": { "name": "claude-desktop", "version": "1.4.2" },
    "workspace": "/home/user/some-project"  // optional
  }
}

// On user message
{
  "jsonrpc": "2.0",
  "method": "notifications/lifecycle/user_message",
  "params": {
    "sessionId": "abc-123",
    "text": "<verbatim user message>",
    "ts": "2026-05-13T16:00:00Z"
  }
}

// On session end
{
  "jsonrpc": "2.0",
  "method": "notifications/lifecycle/session_end",
  "params": {
    "sessionId": "abc-123",
    "reason": "user_close" | "timeout" | "error"
  }
}
```

Notifications are fire-and-forget (no response expected) so the
client's UX is never blocked by the server.

### Server-side response

The server's notification handler may, as a side effect, push **prepended
context** back to the client via a follow-up JSON-RPC method
`notifications/context/prepend` so e.g. on `session_start` the server
can inject the loaded memory snapshot into the conversation:

```jsonc
{
  "jsonrpc": "2.0",
  "method": "notifications/context/prepend",
  "params": {
    "sessionId": "abc-123",
    "role": "system",  // or "user"
    "content": "# TINM thread: ...\n## Recent trajectory: ..."
  }
}
```

This replaces today's pattern of "tell Claude in the system prompt to
call `tinm.load_thread_context()`": the server pushes the context, the
client injects, the model sees it natively. Same UX as Claude Code's
`SessionStart` hook stdout-to-context channel, but standardised.

## Backwards compatibility

- Clients that don't advertise `lifecycleHooks` get the legacy
  experience (servers fall back to their prompt-instructed workaround).
- Servers that don't need lifecycle hooks ignore the capability —
  unchanged.
- The notification names are namespaced (`notifications/lifecycle/*`,
  `notifications/context/*`) so they don't collide with existing MCP
  surface.

## Use cases this unblocks

1. **TINM-style cross-session memory** — auto-load at session start,
   auto-persist every user turn, across every MCP client (the
   motivating case).
2. **Conversation telemetry / audit servers** — record session
   boundaries and turn counts for compliance / billing.
3. **Per-session resource setup** — open a DB connection on
   `session_start`, close it on `session_end`. Today this requires
   either lazy-on-first-tool-call setup or polling.
4. **Multi-server choreography** — a primary server can publish
   `session_start` to subordinate servers via a shared event bus
   (out of scope of this RFC but enabled by it).

## Open questions

- **Privacy**: should `user_message.text` be opt-in per server (the
  client passes only `text_hash` by default)? TINM specifically wants
  the verbatim text. Other servers may not. Probably: client-side
  per-server allow-list.
- **Backpressure**: if the server is slow to ack `user_message`, does
  the client block? Probably no — fire-and-forget, with optional
  `result/lifecycle/acknowledged` for servers that want delivery
  receipts.
- **Concurrency**: multiple servers receive the same lifecycle event
  in parallel. Defined; nothing to spec beyond "the client MUST send
  to all subscribed servers, order undefined".
- **Naming**: `lifecycleHooks` vs `events` vs `notifications`. Worth
  bikeshedding once the substance is reviewed.

## Reference implementation

The TINM project (https://github.com/momo590/tinm) will provide a
FastMCP-based reference implementation of the server side in the
`mvp/mcp_server/` directory, and a small "lifecycle-hooks demo" client
in `mvp/clients/lifecycle-hooks-demo/` to validate the spec against.
Both should land within ~1 week of the spec landing in MCP's repo.

## Next steps

1. **Internal review** — Anthropic project owners (TINM author) and
   any Anthropic engineer touching MCP.
2. **Filing** — open an RFC issue at github.com/modelcontextprotocol/spec
   with this content (lightly edited for MCP conventions).
3. **Prototype** — extend the TINM FastMCP server to publish
   `lifecycleHooks` and the Claude Code client to honour
   `session_start` / `user_message` over MCP (in addition to its
   existing native hooks).
4. **Iterate** based on review.

## Acknowledgements

The pattern is heavily inspired by Claude Code's hook system
(`SessionStart`, `UserPromptSubmit`, …) which has shipped since 2025
and proven the value of server-side lifecycle awareness within a
single product. This RFC just generalises the pattern across MCP
clients.
