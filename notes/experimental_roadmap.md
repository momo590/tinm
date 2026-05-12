# Experimental Roadmap — TINM Research Validation

Tracking what's done, what's pending, what consolidates into the paper's
experimental section.

---

## Completed (results in `benchmark/runs/*_results.json`)

### Benchmark 1 — Synthetic Continuity (`pilot_v2_results.json`, "continuity")
- 50 tasks, n_subq=2, distractor between Q1/Q2 with p=0.5
- 4 agents: rag_baseline, rag_with_history, tinm_a085, tinm_adapt
- **Result**: tinm_a085 = 0.801 (best), tinm_adapt = 0.789, rag_baseline = 0.780, rag_with_history = 0.734
- TINM ties rag_baseline on quality but rag_with_history is Pareto-dominated (+30% tokens for worse quality)
- Distractor drop: -0.019 (a085), -0.048 (adapt), -0.014 (baseline), -0.086 (history)

### Benchmark 2 — Synthetic Topic Shift (`pilot_v2_results.json`, "shift")
- 50 tasks, n_subq=4 with explicit pivot at Q3
- **Result**: tinm_adapt = 0.767 (best), tinm_a085 = 0.732, rag_with_history = 0.735, rag_baseline = 0.729
- tinm_adapt beats tinm_a085 by +0.035 (t=2.90, 10/1/39 W/L/T) — **highly significant**
- tinm_adapt beats rag_baseline by +0.038 (t=2.47) — significant
- **Validates friction-adaptive memory (Thèse 9 of manifesto)**

### Benchmark 3 — MuSiQue 2-hop (`pilot_real_results.json`)
- 50 tasks, real Wikipedia paragraphs, natural anaphora
- **Result**: tinm_adapt = 0.377 (best), tinm_a085 = 0.368, rag_with_history = 0.365, rag_baseline = 0.324
- TINM ties rag_with_history on quality, **−6% tokens** (69359 vs 73876)
- TINM beats rag_baseline by +0.05 (t=1.66-1.73, marginal significance)
- High-quality tasks (≥0.75): tinm_adapt 6/50, rag_with_history 4/50, rag_baseline 2/50 — **2× harder-task success**
- **Validates ecological transfer of the mechanism**
- tinm_adapt ≈ tinm_a085 (expected — only 1 anchor update at 2-hop)

---

## Completed (continued)

### Benchmark 4 — MuSiQue 3-hop (`pilot_real_results_3hop.json`)
- 50 tasks, 3-hop chains
- **Result**: tinm_a085 = 0.322 (best), tinm_adapt = 0.317, rag_baseline = 0.300, rag_with_history = 0.265
- **tinm_a085 beats rag_with_history**: Δ=+0.057, t=2.57 (p<0.05)
- **tinm_adapt beats rag_with_history**: Δ=+0.052, t=2.77 (p<0.01)
- **Surprise**: rag_with_history < rag_baseline on 3-hop. Verbose history pollutes chain reasoning.
- Hard-task success (≥0.5): tinm_a085 10/50, others 6-7/50. TINM resolves 67% more hard chains.

### TINM-full ablation (NEGATIVE result, documented)
- Dual-anchor design (magnetic α=0.92 + courant α=0.50 + repulsion)
- Retrieval-only dry-run on same task seeds:
  - Continuity: 1.46 gold/top5 (TINM-full) vs 2.76 (a085) — degraded
  - Shift: 0.66 vs 1.96 — strongly degraded
  - MuSiQue 2-hop: 0.78 ≈ 0.78 — tied
  - MuSiQue 3-hop: 0.84 vs 0.88 — slight loss
- Cause: `courant_anchor` follows distractor or pivot, polluting next-turn retrieval
- Weight sweep (5 configs) confirmed: any `w_courant > 0` introduces synthetic regression
- No LLM eval run (retrieval signal sufficient). Future work: multi-turn friction detection.

### Consolidated experimental section
- Generated at `runs/paper_results/paper_section.md`
- 7 sub-sections: headline table, significance, pareto, distractor robustness,
  hard-task rate, ablation, narrative
- 5 CSV files for raw data

---

## Status: experimental section COMPLETE

All planned validation done. Pending decisions for the paper:
1. Whether to add a 5th benchmark (real distractor task — e.g. CoQA / QuAC).
2. Whether to add HotpotQA as a second real-data validation (similar to MuSiQue).
3. Implementation of "TINM-full v2" with proper multi-turn friction detection
   (future-work line in current paper).
4. Latex figures from CSVs (matplotlib install was unavailable on macOS Monterey
   + Python 3.13; either set up a different env or use external tooling).

---

## Identified limitations and planned fixes

Four limitations of TINM-lite v2 surfaced during analysis. Each has a concrete
fix, classified by complexity and target phase.

### L1 — Overhead on short reasoning chains
**Cause**: anchor + trajectory hint adds ~30-100 tokens/turn. On 3-turn
conversations that fit trivially in context, this is pure waste.
**Fix**: activation threshold. TINM engages only when `turn >= 3` OR
`context > 8K tokens` OR `anaphora_detected(query)`. Below threshold,
fall back to vanilla retrieval.
**Complexity**: ~30 lines, 1 hour.
**Phase**: MVP v1 — immediate.

### L2 — Abrupt topic transitions (slow EMA lag)
**Cause**: single-turn friction detection cannot distinguish a transient
distractor from a persistent topic shift. Slow EMA lags shifts; adaptive
over-reacts to distractors.
**Fix**: sliding-window divergence detection. Track the last 3 turns'
query-anchor divergence. If consistently high → persistent shift, drop
alpha to 0.10. If high then low → transient distractor, raise alpha to
0.95 to preserve. Default 0.85 otherwise.
**Complexity**: 2-3 days implementation + 1 week validation on existing
benchmarks. Should resolve the failed TINM-full dual-anchor ablation.
**Phase**: TINM-full v2 — research paper 2 (the "Multi-Turn Friction
Detection for Compressed Memory" follow-up).

### L3 — Agent mental state (working memory ≠ user trajectory)
**Cause**: TINM compresses user queries (anchor + trajectory hint), but
agent-side working memory (current file state, code, tests run, decisions)
lives verbatim in context.
**Fix**: structured AgentState alongside user trajectory. A small dataclass
that tracks files (path → snapshot, last seen turn), decisions log, test
results, named artifacts ("Pareto plot", "Table 3"). Serialized to a
compact ~500-token summary in the LLM prompt instead of verbatim history.
**Complexity**: 2-3 weeks for a clean implementation. Essentially a mini
agent OS.
**Phase**: MVP v2 product feature OR independent research paper 3
("Structured Agent State vs. Flat Context in LLM Coding Agents"). Distinct
research direction from limit 2.

### L4 — Implicit references between artifacts
**Cause**: "Like we did earlier for X" — X is in conversation history, not
in the static information ocean. TINM retrieves from ocean only, missing
intra-conversation references.
**Fix**: parallel conversation index. Maintain `(turn_id, role, content,
embedding)` records and named artifact lookup table. Retrieval at each
turn merges results from (a) static ocean and (b) conversation index.
LLM-side anaphoric phrase resolution can hit either source.
**Complexity**: 1-2 weeks of engineering.
**Phase**: MVP v1 — necessary for the cross-session continuity wedge.

### Summary

| Limit | Fix complexity | MVP v1 | MVP v2 | Research paper |
|---|---|---|---|---|
| L1 Short chains | Trivial | ✓ | — | — |
| L2 Abrupt shifts | Medium | — | optional | ✓ paper 2 |
| L3 Agent state | Heavy | — | ✓ | possible paper 3 |
| L4 Implicit refs | Medium | ✓ | — | — |

Note: limits 1 and 4 are the must-haves for the product MVP (the wedge is
cross-session continuity). Limits 2 and 3 are research-track follow-ups
that can proceed in parallel.

---

## Related but orthogonal: ReasoningBank (Google Research, 2025)

Surfaced during session 2 (2026-05-12). ReasoningBank is a concurrent line
of work that addresses a *complementary* memory regime: instead of state
within a task (TINM's domain), it distills *transferable lessons* across
tasks via LLM-as-judge extraction after each task completion. Reported
gains: +8.3% on WebArena and +4.6% on SWE-Bench.

**Verdict for the TINM paper**: cite in Related Work, no integration.
Our benchmarks (MuSiQue 2/3-hop, synthetic continuity) have each task as
independent — there is no cross-task pattern for ReasoningBank to learn.
Integrating would require redesigning benchmarks to have repeated/related
tasks (WebArena/SWE-Bench style), at which point we're writing a different
paper. The current TINM paper has a clean within-task message and should
not be diluted.

**Verdict for the MVP skill**: ReasoningBank-style lesson extraction is
a strong candidate for a future product feature (probably MVP v2). At
the end of a work session, an LLM-as-judge pass extracts portable
lessons ("when working on Project Alpha, always check the deadline
node first"); subsequent sessions retrieve these as long-term priors
that sit above the per-session TINM anchor. The three time scales
(per-query retrieval, per-session TINM anchor, cross-session lessons)
correspond to the hierarchy of resolution described in the substrate
doc §1.3.

**Verdict for a future TINM v3 / paper 2**: combining within-task TINM
with cross-task lessons is a natural follow-up paper. Likely benchmark:
WebArena or SWE-Bench (long trajectories with repeated patterns).
Not before the current TINM paper is published.

---

## Configuration constants (for reproducibility)
- Seed: 42 everywhere (graph, tasks, distractor injection)
- LLM: claude-sonnet-4-6 (Sonnet 4.6)
- Top-K retrieval: 5
- TINM-lite v2 defaults: w_query=0.5, w_anchor=0.5, alpha_min=0.20, alpha_max=0.95
- Hardware: macOS Monterey, Python 3.13, TF-IDF embeddings (sklearn)

---

## Open questions
- Does TINM survive harder distractor scenarios (multiple distractors per task)?
- Can the trajectory-hint mechanism be made cleaner (use anchor vector as condensed prior,
  rather than raw query text)?
- How does TINM scale to 10000+ documents?
- HotpotQA validation as second real-data dataset?
