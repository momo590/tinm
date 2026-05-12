# TINM — Project Charter

One-page brief for any future agent (Claude or otherwise) picking up this
project. Read this *after* `conventions.md` and `experimental_roadmap.md`.

---

## ⮕ NEXT FOCUS (maintained at the end of every major session)

**Last updated**: 2026-05-12 (end of session 3 — paper-1 polish first
pass: BibTeX entries in `paper/draft.md`, Figure 1 rendered via
`paper/figures/make_fig1.py` (matplotlib turned out to install fine on
this env, contrary to the prior session's belief), §5.3 + §2 + status
header tightened. L1 activation threshold shipped in `tinm_lite.py`
behind a default-off flag and mentioned in §6.5).

The natural next steps, in suggested priority order. The agent should
propose this as the default starting point at session start, but the user
owns the decision.

1. **Final verification of paper-1 before submission.** The draft is
   substantively complete and self-consistent. Remaining lower-effort
   work before camera-ready:
   - Verify the BibTeX block at the end of `paper/draft.md` against
     DBLP / Semantic Scholar (author lists beyond first author, venue
     abbreviations, the 3 entries flagged with `note = {verify ...}`).
   - Convert the draft to LaTeX with a journal/venue template once the
     target venue is locked (EMNLP 2026 main or Findings track per §2.1).
   - Optional final prose pass for transitions/redundancy if a co-author
     reads it through.
   - Upload arXiv preprint when the BibTeX is verified.

2. **Start MVP skill prototype** (separate from paper polish, weekend
   side-track). Cross-session continuity Claude skill, file-based PCP v0,
   no cloud. Targeted at the user's own daily workflow first. The
   activation-threshold L1 fix is already in the repo
   (`tinm_lite.py::activation_threshold=True`) — the MVP should enable
   it. L4 (parallel conversation index) is the next MVP-must-have to
   build.

3. **Future research follow-ups** — not for this session, but listed in
   the experimental roadmap (§L1–L4): multi-turn friction detection (now
   with stronger evidence from the 2WikiMultihopQA `tinm_adapt < tinm_a085`
   significant negative result; paper-2 candidate), structured agent state,
   conversation-level retrieval index.

---

## 1. What this project is

**TINM** (Turtle-Inspired Navigation Memory; also written TNIM in earlier
documents — same thing) is a research-and-product project on **memory for
LLM agents** in multi-turn settings.

The core thesis: for agents that span many turns of interaction, memory
should not be a stored corpus of documents (the RAG-with-history pattern)
but a *compressed latent state* that biases retrieval and reaches the LLM
via a minimal textual hint. This compressed memory is empirically
Pareto-improved over both stateless RAG and history-augmented RAG on five
benchmarks tested (two synthetic, three real-data multi-hop QA: MuSiQue
2-hop, MuSiQue 3-hop, 2WikiMultihopQA).

The companion documents:
- `tinm_substrate.md`: the computational substrate (math, algorithms).
- Original manifesto at `~/Downloads/tnim_manifesto_scientific.md` (the
  user's vision document; not in the repo, conceptual input only).

## 2. Dual track: research + product

### Research track (priority now)

- **Target venue**: EMNLP 2026 (main or Findings track; deadline ~Jun 2026).
- **Backup**: ACL Findings, or TMLR (rolling submission, no deadline).
- **Status**: paper draft at `paper/draft.md` is ~85% complete. Remaining
  work: BibTeX conversion of references, matplotlib-rendered Figure 1
  (we couldn't install torch/matplotlib on this macOS Monterey + Py 3.13
  combo; defer to a different env).
- **arXiv strategy**: upload preprint as soon as the draft is polish-ready,
  before venue submission. Standard for NLP/ML.

### Product track (phase 2, after paper polish)

- **Target wedge**: cross-session continuity for knowledge workers who
  juggle Claude Code, Claude.ai, ChatGPT, and other LLM clients on the
  same project over days/weeks. Pain point: context is lost at every
  switch.
- **MVP form**: a Claude skill (or fork of Clicky / Karpathy's
  LLM-wiki / Gary Tan's Gbrain) that captures user queries silently,
  maintains a TINM anchor per "work thread", and exposes the state via
  a simple local file (PCP v0 format).
- **Not in scope for MVP**: cloud sync, multi-user, mobile, marketplace.
- **PCP** (Personal Context Protocol) is a deliberately separate concept
  from TINM. TINM is the memory mechanism; PCP is the protocol for
  exposing that memory across vendors/sessions. **PCP is not in the
  research paper** — it's a product layer. The user has documents
  (Stipple manifesto etc.) describing the broader Stipple/PCP/TINM
  triple but only TINM is research-validated.

### Open-source strategy

- **Open**: PCP spec, format conventions, basic SDK, research-grade TINM
  Python implementation, basic connectors, the paper itself.
- **Closed**: production-grade TINM tuning, watcher pipelines (silent
  capture), polished app UX, cloud sync, enterprise/compliance features.

## 3. Current state (as of last session)

| Component | Status |
|---|---|
| 5 benchmarks (2 synthetic + MuSiQue 2-hop n=100 + MuSiQue 3-hop n=50 + 2WikiMultihopQA n=50) | ✓ run |
| 4 agents (rag_baseline, rag_with_history, tinm_a085, tinm_adapt) | ✓ implemented (SDK `max_retries=8` for 529 resilience) |
| TINM-full ablation | ✓ documented as negative result |
| Paper draft | ✓ end-to-end (~95%), all numbers integrated, BibTeX inlined, Figure 1 rendered |
| Consolidated experimental tables | ✓ in `benchmark/runs/paper_results/` |
| BibTeX references | ✓ inlined at end of `paper/draft.md` — needs verification pass |
| Figure 1 (matplotlib) | ✓ `paper/figures/fig1_pareto.{pdf,png}` (regen via `make_fig1.py`) |
| L1 activation threshold (MVP fix) | ✓ shipped in `tinm_lite.py` behind default-off flag |
| MVP skill | not started |
| PCP v0 spec | not started |

## 4. Open strategic decisions (await user input)

1. ~~**HotpotQA as 5th benchmark?**~~ — resolved in session 2: we picked
   2WikiMultihopQA instead (cleaner decomposition via explicit
   `(subj, rel, obj)` triplets, same anaphoric Q1→Q2 structure).
2. ~~**n = 100 or 200 rerun for tighter t-stats?**~~ — resolved in
   session 2: MuSiQue 2-hop re-run at n=100. All previously marginal
   comparisons (TINM vs rag_baseline, TINM vs rag_with_history) now
   clear p < 0.05 (t-stats 2.40 to 4.05).
3. **Multi-turn friction (TINM-lite v3)?** Would address L2 in the
   roadmap. Possible separate paper. The 2Wiki result (tinm_adapt
   significantly *worse* than tinm_a085, t=-2.10) strengthens the
   motivation for this line of work.
4. **Start MVP skill in parallel with paper polish?** Currently paper
   has priority but the user has expressed wanting to do both.

The agent should not act on any of these without explicit user direction.

## 5. The four limits documented in the roadmap

(See `experimental_roadmap.md` for details. Brief recap:)

- **L1**: TINM overhead on short chains. Fix: activation threshold. MVP v1.
- **L2**: Abrupt topic transitions. Fix: multi-turn friction. Research paper 2.
- **L3**: Agent mental state. Fix: structured agent state. MVP v2 / paper 3.
- **L4**: Implicit refs between artifacts. Fix: conversation-level retrieval index. MVP v1.

L1 and L4 are MVP-must-haves. L2 and L3 are research follow-ups.

## 6. What an incoming agent should do first

1. Read `notes/conventions.md` (this is binding).
2. Read this charter to understand the project shape.
3. Read `notes/experimental_roadmap.md` for the experimental status.
4. Acknowledge readiness in 1-3 sentences. Surface the "Next focus"
   item at the top of this charter as the proposed default, and offer
   1-2 alternative directions if the user prefers something else.
5. Wait for the user's call. **Do not act on a major piece of work
   without explicit user direction.** The project has multiple
   plausible next steps and the user owns the strategic call.

## 7. What an incoming agent should *not* do

- Implement PCP, build the MVP skill, write a new TINM variant, or run
  expensive pilots without explicit user request.
- Modify `notes/conventions.md` silently.
- Commit anything without explicit user request.
- Push to a remote (no remote configured anyway).
- Promise the user that "we tested X" if the test isn't in the result
  JSONs. Be honest about what's actually been validated.
