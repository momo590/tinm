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

Reproducible after `bash install.sh` + `tinm_demo.py` (see [Try it](#try-it)). The cast source is at [`docs/assets/replay.cast`](docs/assets/replay.cast).

macOS / Linux, ~3 minutes (most of it is `pip install`), fully reversible. The walkthrough is in [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## What it removes

Every new Claude Code session today starts with re-pasting yesterday's context: scrolling Slack, your last terminal, your own notes, then copy-pasting key decisions before any work begins. TINM does that step for you. It also avoids the alternative of growing `CLAUDE.md` until it becomes noise.

## How it works

- **Hooks.** Three Claude Code hooks read and write a local store under `~/.tinm/`. Nothing else in your setup changes.
- **PCP v0 storage.** Each thread is two JSON files (trajectory + named artifacts). The format is vendor-neutral so other clients can read the same threads. Spec: [`mvp/pcp_v0_spec.md`](mvp/pcp_v0_spec.md).
- **Relevance.** When you send a new prompt, TINM ranks past artifacts against it and surfaces the matches into Claude's context — no manual `/load` required.

An MCP server is also included so clients beyond Claude Code can read the same threads ([`mvp/clients/`](mvp/clients/)). Cross-host sync (Mac ↔ VPS) ships as a separate, optional setup script.

## Status

- **v0.1** — cross-session memory shipped and stable. First external beta tester onboarding now.
- **v0.2** — intra-session digest (in flight). See the design doc in `paper/` for the mechanism.
- **v1.0** — openClaw / Cursor / Claude Desktop client parity.

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

Claude will answer with a specific number from the demo's stored artifacts — one it could not have produced without TINM pulling that artifact into its context. That's the mechanism: prior-session content surfaced into a fresh session, no manual load, no file read.

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
