# TINM — context memory for Claude Code

**TINM (TNIM)** is the only layer that makes Claude Code remember **across your sessions** AND keeps **long-running sessions coherent** — without you doing anything.

```bash
curl -fsSL https://raw.githubusercontent.com/momo590/tinm/main/mvp/scripts/install.sh | bash
```

macOS / Linux, ~2 minutes, fully reversible. See [`docs/QUICKSTART.md`](docs/QUICKSTART.md) for the DM-friendly walkthrough.

> *"~7K tokens saved on a single question that Claude pulled from a thread I'd ended 3 days earlier — without me re-pasting anything."* (founder N=1)

## What it does

1. **Cross-session memory.** Every Claude Code session writes a *trajectory* and a *top-terms anchor* to `~/.tinm/pcp/`. When you reopen Claude Code (same project or `/tinm load <slug>`), the trajectory + relevant named artifacts (`/tinm save "..."`) get re-injected automatically. No re-pasting.
2. **Intra-session digest** *(v0.2, shipping soon)*. When a session crosses ~40k tokens, TINM swaps old turns for an LLM-compressed digest — Claude stays sharp, the original turns stay in the PCP store.
3. **Self-instrumented telemetry** *(opt-in, local-only by default)*. TINM measures the value it actually generates: cross-session hits, tokens saved, hook latency, your `/tinm` usage. Aggregates only, never content. Opt out anytime: `python ~/.claude/skills/tinm/tinm_telemetry.py off`.

## How it works (5 lines)

- **Hooks.** Three Claude Code hooks (`SessionStart`, `UserPromptSubmit`, `PreCompact`+`PostCompact`) call short Python scripts that read/write `~/.tinm/pcp/`.
- **PCP v0 format.** Vendor-neutral JSON ([`mvp/pcp_v0_spec.md`](mvp/pcp_v0_spec.md)) so a second client (Claude Desktop, openClaw, …) can speak the same threads later.
- **Anchor.** An EMA of past user-query embeddings — `all-MiniLM-L6-v2` from sentence-transformers. The anchor decides what's relevant; ranking via two-stage substring + cosine fallback.
- **Native compaction-aware.** If Claude Code triggers its own compaction, TINM detects it via `PreCompact` markers and cedes — no double-compression.
- **No remote required for v0.1.** PCP is a local git repo; cross-host sync (Mac↔VPS via `tinm-pcp`) is a separate opt-in setup script.

## Privacy

Everything is local in `~/.tinm/` by default. Nothing leaves your machine unless you explicitly enable telemetry sharing (`tinm_telemetry.py on --share`) — and even then only **aggregate counts**, never prompts or file contents. The privacy contract is enforced and tested in [`mvp/tests/test_telemetry.py`](mvp/tests/test_telemetry.py).

## Status

- **v0.1** — cross-session memory shipped and stable. **First external beta tester onboarding now.**
- **v0.2** — intra-session digest (4-week ETA). See the design doc in `paper/` for the full mechanism.
- **v1.0** — openClaw / Cursor / Claude Desktop client parity.

## Try it

After install:

```bash
# In any Claude Code session
/tinm init my-project          # Create your first thread

# Later, mark something for cross-session recall
/tinm save "the architecture decision"

# Inspect what TINM is tracking
python ~/.claude/skills/tinm/tinm_status.py
```

Full DM-able walkthrough: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Uninstall (fully reversible)

```bash
bash ~/.tinm/source/mvp/scripts/uninstall.sh
```

Leaves a `~/.claude/settings.json.pre-uninstall` backup so you can roll back manually if needed. `TINM_KEEP_PCP=1 bash ...` preserves your thread history at `~/tinm-pcp-keep-<ts>/`.

## Repo layout

- [`mvp/`](mvp/) — the Claude Code MVP (skills, hooks, scripts, tests). The thing you install.
- [`benchmark/`](benchmark/) — research substrate. 5 long-context benchmarks comparing TINM-lite vs RAG baselines.
- [`paper/`](paper/) — paper draft + figures ([Fig 1 Pareto plot](paper/figures/fig1_pareto.pdf)).
- [`notes/`](notes/) — internal design notes and ADRs (not user-facing, but kept public for transparency).

## Research

TINM-lite (paper substrate) reaches **+0.114 F1** over a strong RAG baseline on 2WikiMultihopQA n=50 by carrying an EMA-anchor + trajectory across turns. The MVP applies the same mechanism to Claude Code sessions. Full evaluation in `paper/`.

## License

MIT — see [LICENSE](LICENSE).

## Contributing / feedback

Beta v0.1 collects: *would you be upset if TINM disappeared tomorrow?* (Sean Ellis test). If you try it, please DM me what you noticed — magical, weird, or boring. That's the data this stage needs more than any commit.
