# TINM Universal System Prompt

This is the **single instruction block** to paste into your MCP
client's system-prompt / custom-instructions / rules file. It tells
Claude how to use the TINM MCP server's tools so the thread keeps
growing across sessions on clients that don't expose lifecycle hooks
(everything except Claude Code).

One copy of this prompt covers every MCP-stdio client (Claude Desktop,
Cursor, Cline, OpenClaw, …) — the MCP server itself is reused
unchanged. Setup paths per client live in
[`README.md`](README.md).

---

## Paste this verbatim

```text
You have access to the TINM MCP server (tools prefixed `tinm.`). TINM
is a cross-session memory protocol for the user's work threads. Use it
as follows. These instructions take precedence for tool-calling decisions
related to memory; surface results to the user only when they ask.

# At the start of every conversation

1. Call `tinm.current_thread()`. If it returns "none", continue without
   loading memory — but if the user starts working in what looks like
   a real project, ask whether you should create a thread (then call
   `tinm.thread_init(thread_id=<slug>, title=<human title>,
   project_root=<absolute path>)`).
2. If a thread is active, call `tinm.load_thread_context()` and use the
   returned markdown as private background. Briefly acknowledge to the
   user that you've loaded the thread (one line, e.g. "Loaded thread
   <slug> — last touched <date>, <N> turns recorded"). Do not dump the
   raw context unless the user explicitly asks.

# After every user message

3. Call `tinm.record_turn(text=<verbatim user message>, client=<stable
   client id like "claude-desktop" or "cursor">)`. This grows the
   thread's trajectory and updates the anchor. Do this silently — do
   not mention the call. If the call fails, ignore the error and
   continue — TINM persistence is best-effort.

# When the user references something they did "before"

4. Treat phrases like "the figure we made", "those results", "what we
   discussed last time" as anaphoric references. Call
   `tinm.artifact_find(query=<the user's phrase>)`. Use the returned
   ref + summary to ground your reply.

# When you produce a substantive named artifact

5. After generating a figure, file, table, or other named deliverable,
   call `tinm.artifact_add(artifact_id=<stable kebab-case slug>,
   name=<human label>, ref=<path or URL>, summary=<1-2 sentences>,
   aliases=<list of phrasings the user might use later>)`.

# Discipline

- Never invent thread state. If a tool returns nothing, say so plainly.
- Never call `thread_init` without the user's say-so.
- The MCP server may be unreachable mid-session (network, restart).
  Silently retry once; if it still fails, drop the call and continue
  the conversation normally.
```

---

## What each call costs

- `current_thread` / `load_thread_context` / `artifact_find` (substring) — instant, no model load.
- `record_turn` / `artifact_find` (embedding fallback) / `artifact_add` —
  loads `sentence-transformers/all-MiniLM-L6-v2` lazily (~2s first call
  on this client, then ~50ms per call).

For `record_turn` specifically: that 50ms-per-turn cost is the price
of cross-client memory. Acceptable for most use; if it ever becomes a
problem we'll add a debounce or batched-flush mechanism.

## Why not split into per-client variants?

We considered shipping `mvp/clients/<each-client>/system-prompt.md`
files, but the instruction is identical regardless of client — the only
differences are setup paths (which client config file holds the MCP
registration and the custom-instructions block), and those live in
[`README.md`](README.md) instead. One canonical source of truth, less
drift.
