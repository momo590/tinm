# TINM — Cross-vendor memory for AI coding agents

Open protocol (PCP), works across Claude Code, Cursor, OpenClaw, Aider, Codex, Continue.dev, plus browser-based AI. Reproducible benchmarks.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-blue)
![Status](https://img.shields.io/badge/status-beta-orange)

```bash
curl -fsSL https://raw.githubusercontent.com/momo590/tinm/main/mvp/scripts/install.sh | bash
```

![TINM — new session, yesterday's +0.114 F1 surfaces without reading any file](docs/assets/replay.gif)

<details><summary>Read it instead of watching</summary>

```
# Yesterday I shipped Phase 1 of the TINM paper.
# Today, new terminal, new session, no /load, no file open.
# TINM seeded with `tinm demo` (see README).

$ claude -p "What was the biggest absolute effect we
              measured on the 2WikiMultihopQA pilot?"
**+0.114 absolute lift** for tinm_a085 over rag_baseline
(t=4.03, paired, n=50). tinm_a085 scored 0.481 — the
largest measured effect in the Phase 1 pilot set.

Source: benchmark/runs/pilot_wiki2hop_results.json
(per the loaded TINM thread).

# +0.114 surfaced from yesterday — no file read, no context paste.
```

</details>

macOS / Linux, ~3 minutes (most of it is `pip install`), fully reversible. The walkthrough is in [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

---

## The problem

Every Claude Code session ends the same way: `/export` the conversation, or ask Claude to save the discussion to memory. Every new session starts the same way: 10 minutes re-explaining yesterday's decisions before any real work happens.

Switch devices or clients and it gets worse — same repo access to grant, same docs to point to, same RAG index to wire up, then still re-explain because no client truly remembers between sessions.

## The solution

- **Auto-recall.** Past decisions, designs, and answers surface into your next session automatically — no `/load`, no file paste.
- **Works with what you already use.** Three small hooks plug into Claude Code. Nothing else in your setup changes.
- **Not locked to one client.** Memory is stored in an open format (PCP) — a simple JSON layout any AI client can read. Your context belongs to you, not to a vendor.
- **Works with browser AI too.** ChatGPT, Claude.ai, Perplexity, Notion AI — copy any response with `@tinm save` on the first line and it lands in your TINM memory. Native desktop notification confirms each capture.

> **What's PCP?** Personal Context Protocol. A vendor-neutral way to store AI conversation memory on disk. Other clients (Cursor, OpenClaw, Aider, …) can read the same memory through bundled adapters.

## Try it

The install ships a short demo thread so you can see cross-session recall on bundled sample data, without first having to accumulate your own history.

```bash
# 1. Verify the hooks loaded
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_status.py

# 2. Install the bundled demo thread (a real research session from the TINM paper)
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py

# 3. Open a fresh Claude Code session and paste this prompt verbatim:
#    "What was the biggest absolute effect we measured on the 2WikiMultihopQA pilot?"
```

Claude will answer with a specific number from the demo's stored memory — one it could not have produced without TINM pulling that memory into its context. That's the mechanism: prior-session content surfaced into a fresh session, no manual load, no file read.

When you're ready to use TINM on your own work:

```
/tinm init my-project                    # create your first real thread
/tinm save "the architecture decision"   # mark a key decision for future sessions
```

Next session, ask anything that touches that decision — TINM brings it back without you re-pasting.

## Capture from any AI tool (ChatGPT, Claude.ai, Perplexity, …)

The browsers where you actually use ChatGPT or Perplexity have no hooks. TINM bridges them with an opt-in clipboard watcher: copy any response prefixed with `@tinm save` and it lands in your TINM memory with a native desktop notification.

```bash
# One-time enable (auto-starts with every future Claude Code session)
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_clipboard.py --enable
```

Then anywhere — ChatGPT web, Claude.ai, Perplexity, Notion AI, even a plain text file:

```
@tinm save

<paste the AI response you want to remember>
```

Copy that block (Cmd/Ctrl+C). ~3 seconds later you get a system notification: `TINM captured to <thread>`. Done. The content is now searchable cross-session like any other TINM memory.

**Privacy.** The watcher does nothing unless `@tinm save` is the first non-empty line — case-insensitive but exact. Trigger line is stripped, never stored. Clipboard is read, never modified.

macOS and Linux. Disable anytime: `tinm_clipboard.py --disable`. Full docs in [`mvp/clients/clipboard_install.md`](mvp/clients/clipboard_install.md).

## How it works

- **Three hooks.** Claude Code fires `SessionStart`, `UserPromptSubmit`, and `Stop` events as you work. TINM listens to all three.
- **At session start**, TINM resolves the thread for your current working directory (one per project, derived from the git toplevel or basename), freezes that name for the session, and loads the relevant past notes into Claude's context.
- **On every prompt**, TINM scores your message against everything it remembers and surfaces the matches into context, automatically. `cd`'ing mid-session does NOT switch threads — the per-session freeze guarantees one logical conversation stays in one thread.
- **At session end**, TINM captures the answers you implicitly approved (by moving on without correction) — so next session knows what you agreed on, not just what you asked.
- **Storage.** Everything lives in `~/.tinm/pcp/` as plain JSON files — `threads/` for your work, `seeds/` for bundled demo data (read-only, separately namespaced so private work cannot accidentally pile onto a public seed). Open them in any text editor. Nothing leaves your machine.

## Research

TINM-lite (the algorithm behind the product) reaches **+0.114 F1** over a strong RAG baseline on the 2WikiMultihopQA benchmark (n=50, paired t=4.03). Pareto-best on 4 of 5 long-context benchmarks. Full evaluation in [`paper/`](paper/); the MVP applies the same mechanism to your Claude Code sessions.

## Privacy

Everything is local in `~/.tinm/` by default. Nothing leaves your machine unless you explicitly enable telemetry sharing — and even then only **aggregate counts**, never prompts or file contents. The privacy contract is enforced and tested in [`mvp/tests/test_telemetry.py`](mvp/tests/test_telemetry.py).

```bash
python ~/.claude/skills/tinm/tinm_telemetry.py status   # check current state
python ~/.claude/skills/tinm/tinm_telemetry.py off      # opt out anytime
```

## Other clients (beyond Claude Code)

Adapters ship for Cursor, OpenClaw, Windsurf, Cline, Aider, Codex CLI, and Continue.dev (all [BETA]). A universal clipboard watcher captures conversations from ChatGPT web, Claude.ai, Perplexity, and any other browser-based AI tool via an opt-in trigger phrase. See [`mvp/clients/`](mvp/clients/) for install guides.

## Status

- **v0.2.3** — per-cwd thread isolation, seed namespace separation, workspace-provenance gate. Upgrade-time migration splits any threads polluted by the v0.2.2 seed-vs-current_thread bug. See [`MIGRATION_v023.md`](MIGRATION_v023.md).
- **v0.2.2** — cross-session memory, intra-session relevance scoring, 8 vendor adapters. Stable on macOS and Linux.
- **Next** — handoff-prompt generator (compose the perfect prompt for the next agent/machine), telemetry-tuned scoring weights.

## Uninstall (fully reversible)

```bash
bash ~/.tinm/source/mvp/scripts/uninstall.sh
```

Leaves a `~/.claude/settings.json.pre-uninstall` backup so you can roll back manually if needed. `TINM_KEEP_PCP=1 bash ...` preserves your thread history at `~/tinm-pcp-keep-<ts>/` for future reinstalls.

## Repo layout

- [`mvp/`](mvp/) — the Claude Code MVP (skills, hooks, scripts, tests). The thing you install.
  - [`mvp/pcp_v0_spec.md`](mvp/pcp_v0_spec.md) — the open standard. Start here if you only read one file.
  - [`mvp/seeds/`](mvp/seeds/) — bundled demo threads, including `tinm-tour`.
  - [`mvp/clients/`](mvp/clients/) — MCP server and non-Claude-Code adapters (Cursor, OpenClaw, Windsurf, Cline, Aider, Codex, Continue.dev, clipboard).
- [`paper/`](paper/) — paper draft + figures ([Fig 1 Pareto plot](paper/figures/fig1_pareto.pdf)).
- [`benchmark/`](benchmark/) — 5 long-context benchmarks comparing TINM-lite vs RAG baselines.
- [`notes/`](notes/) — internal ADRs and design docs (kept public for transparency).

## License

MIT — see [LICENSE](LICENSE).

## Contributing / feedback

If you try it, **DM [@MmakhtarDiop](https://x.com/MmakhtarDiop) on X** what you noticed — magical, weird, boring, or broken. That is the data this stage needs more than any commit.

---

⭐ **Star if it saves you time — it helps others find it.**
