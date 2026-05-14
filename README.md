# TINM — cross-session memory for Claude Code

> Stop re-pasting yesterday's context at the start of every new session.

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

This is a real recording (`docs/assets/replay.cast`), not a mockup. Reproducible after `bash install.sh` + `tinm_demo.py` (see [Try it](#try-it)).

macOS / Linux, ~3 minutes (most of it is `pip install`), fully reversible. The DM-friendly walkthrough is in [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## What just happened

I shipped Phase 1 of the TINM paper yesterday — `+0.114 F1` on 2WikiMultihopQA. Today, new terminal, new Claude Code session, no `/tinm load`, no file open. I asked what we had measured. TINM surfaced the artifact and Claude quoted the number without reading anything. ~7000 tokens I didn't have to re-paste, on a single question. That is the whole pitch.

## The pain TINM removes

Re-coller le contexte au début de chaque session. You open Claude Code, you already know you're about to spend ten minutes searching Slack, scrolling yesterday's terminal, copy-pasting decisions back into the chat before any actual work starts. Power-users juggling three projects feel that several times a day. The current workarounds — letting CLAUDE.md grow into 800-line garbage, expanding `mem0` / `Letta` / `MemGPT` (none Claude-Code-native), or just typing the same paragraph again — all lose information and never compose.

## How it works (5 lines)

- **Hooks.** Three Claude Code hooks (`SessionStart`, `UserPromptSubmit`, `PreCompact`+`PostCompact`) call short Python scripts that read/write `~/.tinm/pcp/`.
- **PCP v0 — vendor-neutral storage format.** Two JSON files per thread (trajectory + artifacts). Spec at [`mvp/pcp_v0_spec.md`](mvp/pcp_v0_spec.md). A Claude Code thread today is the same file a Claude Desktop / openClaw / Cursor client can read tomorrow.
- **Anchor.** An EMA (α=0.85) of past user-query embeddings — `all-MiniLM-L6-v2` from sentence-transformers. The anchor decides what's relevant; ranking is two-stage substring then cosine fallback.
- **Native compaction-aware.** If Claude Code triggers its own compaction, TINM detects it via `PreCompact` markers and cedes — no double-compression.
- **MCP server included.** TINM also exposes its anchor / artifact lookup as MCP tools (`current_thread`, `artifact_find`, `record_turn`) so non-Claude-Code clients can read the same threads. See [`mvp/clients/`](mvp/clients/).

No remote required for v0.1 — PCP is a local git repo. Cross-host sync (Mac ↔ VPS via `tinm-pcp`) is a separate, optional setup script.

## But won't Anthropic ship native semantic compaction?

Probably. And when they do, TINM consumes it. The moat is not the mechanism, it's [PCP v0](mvp/pcp_v0_spec.md) — an open file format for cross-session, cross-client, cross-machine threads. TINM is one app on top of that standard. The standard survives anything Anthropic ships inside one client; the app gets thinner and more useful, not obsolete. If Claude Desktop, openClaw, Cursor, and ChatGPT all speak PCP, that's the layer worth building on.

## Status

- **v0.1** — cross-session memory shipped and stable. First external beta tester onboarding now.
- **v0.2** — intra-session digest (in flight). See the design doc in `paper/` for the mechanism.
- **v1.0** — openClaw / Cursor / Claude Desktop client parity.

## Try it

The whoa moment in the GIF up top can fire for you in minute 2 — no need to wait 24h for cross-session recall on your own data. After install:

```bash
# 1. Verify the hooks loaded
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_status.py

# 2. Install the bundled demo thread
~/.tinm/.venv/bin/python ~/.claude/skills/tinm/tinm_demo.py

# 3. Open a fresh Claude Code session and paste:
#    "What was the biggest absolute effect we measured on the 2WikiMultihopQA pilot?"
```

When you're ready to use TINM on your own work:

```
/tinm init my-project          # create your first real thread
/tinm save "the architecture decision"   # mark a named artifact for cross-session recall
```

Next session, ask anything that touches that decision — TINM pulls the artifact back without you re-pasting.

## Privacy

Everything is local in `~/.tinm/` by default. Nothing leaves your machine unless you explicitly enable telemetry sharing (`python ~/.claude/skills/tinm/tinm_telemetry.py on --share`) — and even then only **aggregate counts**, never prompts or file contents. The privacy contract is enforced and tested in [`mvp/tests/test_telemetry.py`](mvp/tests/test_telemetry.py). Opt out anytime:

```bash
python ~/.claude/skills/tinm/tinm_telemetry.py off
python ~/.claude/skills/tinm/tinm_telemetry.py status   # check current state
```

## Uninstall (fully reversible)

```bash
bash ~/.tinm/source/mvp/scripts/uninstall.sh
```

Leaves a `~/.claude/settings.json.pre-uninstall` backup so you can roll back manually if needed. `TINM_KEEP_PCP=1 bash ...` preserves your thread history at `~/tinm-pcp-keep-<ts>/` for future reinstalls.

## Repo layout

- [`mvp/`](mvp/) — the Claude Code MVP (skills, hooks, scripts, tests). The thing you install.
  - [`mvp/pcp_v0_spec.md`](mvp/pcp_v0_spec.md) — the open standard. Start here if you only read one file.
  - [`mvp/seeds/`](mvp/seeds/) — bundled demo threads, including `tinm-tour`.
  - [`mvp/clients/`](mvp/clients/) — MCP server and non-Claude-Code adapters.
- [`paper/`](paper/) — paper draft + figures ([Fig 1 Pareto plot](paper/figures/fig1_pareto.pdf)).
- [`benchmark/`](benchmark/) — 5 long-context benchmarks comparing TINM-lite vs RAG baselines.
- [`notes/`](notes/) — internal ADRs and design docs (kept public for transparency).

## Research

TINM-lite (the paper substrate) reaches **+0.114 F1** over a strong RAG baseline on 2WikiMultihopQA n=50 by carrying an EMA-anchor + trajectory across turns (paired t=4.03). Pareto-best on 4 of 5 long-context benchmarks. Full evaluation in `paper/`; the MVP applies the same mechanism to Claude Code sessions.

## License

MIT — see [LICENSE](LICENSE).

## Contributing / feedback

Beta v0.1 collects: *would you be upset if TINM disappeared tomorrow?* (Sean Ellis test). If you try it, **DM [@MmakhtarDiop](https://x.com/MmakhtarDiop) on X** what you noticed — magical, weird, boring, or broken. That is the data this stage needs more than any commit.
