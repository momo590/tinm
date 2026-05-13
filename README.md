# TINM — memory for multi-turn LLM agents

TINM (Turtle-Inspired Navigation Memory; also written TNIM in earlier
documents — same thing) is a research-and-product project on **memory
for LLM agents in multi-turn settings**, with:

- a **research substrate** (math + algorithms in `tinm_substrate.md`,
  benchmarks in `benchmark/`, paper draft in `paper/`),
- a **practical MVP for Claude Code** (`mvp/`) that gives the CLI
  cross-session continuity per work thread — auto-loaded trajectory,
  EMA-updated anchor, named-artifact index, persisted as JSON in
  `~/.tinm/`.

The MVP follows the [PCP v0.1 protocol](mvp/pcp_v0_spec.md), an
intentionally vendor-neutral format so a second client (Claude.ai,
ChatGPT, …) can speak the same threads later.

## Quick start — Phase 1 (single host, Mac)

Phase 1 = one machine, no sync, ~10 min install. Full instructions in
[`mvp/README.md`](mvp/README.md). The short version:

```bash
# 1. Python ≥ 3.10 venv (MCP SDK needs it)
brew install python@3.12
python3.12 -m venv ~/.tinm/.venv
~/.tinm/.venv/bin/pip install -r mvp/requirements.txt

# 2. Symlinks for the hooks + slash command
ln -s "$PWD/mvp/skill"            ~/.claude/skills/tinm
ln -s "$PWD/mvp/commands/tinm.md" ~/.claude/commands/tinm.md

# 3. Merge mvp/install_snippet.json into ~/.claude.json (sub <REPO> first)
# 4. Quit + reopen Claude Code
# 5. /tinm init my-first-thread --title "My first TINM thread"
```

That's it. Subsequent sessions on this thread auto-load context at
SessionStart and auto-persist each turn.

## Quick start — Phase 2 (two hosts, e.g. Mac ↔ VPS)

Phase 2 = shared memory across two machines via a private git repo as
the PCP store. ~15 min total. See
[`notes/phase2_vps_runbook.md`](notes/phase2_vps_runbook.md). Outline:

1. Create a private GitHub repo (e.g. `tinm-pcp`).
2. `mvp/scripts/setup_git_sync.sh <repo-url>` on the first host —
   wires `$TINM_PCP_DIR/.git`, pushes the existing threads.
3. On the second host, run the bootstrap prompt at
   [`notes/vps_bootstrap_prompt.md`](notes/vps_bootstrap_prompt.md)
   inside a Claude Code session — it installs Python, clones, builds
   the venv, registers the hooks + MCP server.
4. The TINM hooks transparently `git pull` at SessionStart and
   background-`git push` after each turn.

## Repo layout

```
.
├── mvp/                  TINM MVP for Claude Code (PCP v0 + MCP server + hooks)
│   ├── README.md             detailed install + usage
│   ├── pcp_v0_spec.md        PCP v0.1 protocol spec
│   ├── skill/                CLI scripts (init, load, update, artifact, paths)
│   ├── mcp_server/           FastMCP server exposed to Claude Code over stdio
│   ├── hooks/                SessionStart + UserPromptSubmit hooks
│   ├── scripts/              migrate_to_pcp_subdir.sh, setup_git_sync.sh
│   └── commands/             /tinm slash command (fallback / debug)
├── notes/                Project documents
│   ├── project_charter.md    vision, scope, strategic decisions
│   ├── conventions.md        binding agent-user working agreement
│   ├── experimental_roadmap.md  L1-L4 research roadmap
│   ├── phase2_vps_runbook.md    cross-machine setup runbook
│   └── vps_bootstrap_prompt.md  drop-in prompt for VPS-side Claude Code
├── tinm_substrate.md     mathematical foundation (EMA anchor, friction, etc.)
├── paper/                draft of the research paper
└── benchmark/            5 benchmarks, 4 agents, paper-ready result tables
```

## Status

| Component | Status |
|---|---|
| Research substrate + paper draft | ~95% complete, BibTeX verification pending; EMNLP 2026 target |
| 5 benchmarks (2 synthetic + MuSiQue 2/3-hop + 2WikiMultihopQA) | run, all comparisons p<0.05 |
| MVP Phase 1 (Claude Code MCP + hooks + auto-init) | shipped, dogfooded since 2026-05-12 |
| MVP Phase 2 (cross-machine via private git repo) | shipped 2026-05-13, see runbook |
| Other MCP clients (Claude Desktop, Cursor, OpenClaw, Cline) | server reusable — see [`notes/clients_roadmap.md`](notes/clients_roadmap.md) for per-client plan and the open auto-persist-without-hooks question |
| Claude.ai web + ChatGPT | gated on a remote MCP HTTPS endpoint (Claude.ai) or a Custom GPT actions wrapper (ChatGPT) — see roadmap |

## License

MIT — see [`LICENSE`](LICENSE).

## Citing

Paper currently in preparation. Once on arXiv, a BibTeX entry will land
here. In the meantime, see `paper/draft.md` for the working draft.
