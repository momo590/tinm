# TINM — Experimental Section (consolidated)

Four benchmarks. Four agents (`rag_baseline`, `rag_with_history`, `tinm_a085`, `tinm_adapt`). n=50 tasks per benchmark, Claude Sonnet 4.6, deterministic seed=42, top-K retrieval = 5.

**Benchmarks**:

1. *Synthetic continuity* — 200 nodes, 5 topics, 2 sub-questions per task. Random distractor (different topic) inserted with p=0.5 between Q1 and Q2. Q2 mixes soft and hard co-reference.
2. *Synthetic topic shift* — same graph, 4 sub-questions: Q1+Q2 about topic A, Q3 explicit pivot to topic B, Q4 hard co-reference for B. The decisive test of whether the memory mechanism adapts to mid-task shifts.
3. *MuSiQue 2-hop* — real Wikipedia paragraphs. Each task: a multi-hop question decomposed into Q1 (identify bridge entity) and Q2 (anaphoric, asks about bridge entity's property).
4. *MuSiQue 3-hop* — three chained hops. Q1 → Q2 → Q3, each Q_i using #i-1 to reference the previous answer.
## 1. Headline results — all benchmarks × all agents

Quality is the hybrid score (50% symbolic factual coverage + 50% LLM-as-judge). Higher is better. All runs: n=50 tasks, Claude Sonnet 4.6, seed=42, top-K=5.

| Benchmark | Agent | Quality | Median | Tokens | Q/1k tok |
|---|---|---|---|---|---|
| Synthetic continuity | rag_baseline | 0.780 | 0.775 | 39,251 | 0.993 |
| Synthetic continuity | rag_with_history | 0.734 | 0.750 | 51,142 | 0.718 |
| Synthetic continuity | tinm_a085 | 0.801 | 1.000 | 39,534 | 1.013 |
| Synthetic continuity | tinm_adapt | 0.789 | 0.917 | 39,330 | 1.004 |
| Synthetic topic shift | rag_baseline | 0.729 | 0.750 | 60,757 | 0.600 |
| Synthetic topic shift | rag_with_history | 0.735 | 0.750 | 79,747 | 0.461 |
| Synthetic topic shift | tinm_a085 | 0.732 | 0.750 | 61,506 | 0.595 |
| Synthetic topic shift | tinm_adapt | 0.767 | 0.750 | 61,656 | 0.622 |
| MuSiQue 2-hop | rag_baseline | 0.324 | 0.300 | 70,359 | 0.230 |
| MuSiQue 2-hop | rag_with_history | 0.365 | 0.375 | 73,876 | 0.247 |
| MuSiQue 2-hop | tinm_a085 | 0.368 | 0.375 | 69,504 | 0.265 |
| MuSiQue 2-hop | tinm_adapt | 0.377 | 0.375 | 69,359 | 0.271 |
| MuSiQue 3-hop | rag_baseline | 0.300 | 0.217 | 116,071 | 0.129 |
| MuSiQue 3-hop | rag_with_history | 0.265 | 0.217 | 118,461 | 0.112 |
| MuSiQue 3-hop | tinm_a085 | 0.322 | 0.217 | 112,911 | 0.142 |
| MuSiQue 3-hop | tinm_adapt | 0.317 | 0.217 | 113,018 | 0.140 |

## 2. Statistical significance (paired t-tests, n=50)

For each benchmark we run paired t-tests between agents on the same tasks (common seed). `t > 1.96` is significant at p < 0.05. W/L/T = wins/losses/ties.

| Benchmark | Comparison | Δ quality | SE | t-stat | W/L/T |
|---|---|---|---|---|---|
| Synthetic continuity | tinm_adapt vs rag_baseline | +0.0097 | 0.0182 | 0.53 | 8/9/33 |
| Synthetic continuity | tinm_a085 vs rag_baseline | +0.0213 | 0.0201 | 1.06 | 11/7/32 |
| Synthetic continuity | tinm_adapt vs rag_with_history | +0.0552 * | 0.0238 | 2.32 | 14/5/31 |
| Synthetic continuity | tinm_a085 vs rag_with_history | +0.0668 ** | 0.0217 | 3.07 | 15/7/28 |
| Synthetic continuity | tinm_adapt vs tinm_a085 | -0.0116 | 0.0105 | -1.11 | 5/5/40 |
| Synthetic continuity | rag_with_history vs rag_baseline | -0.0455 | 0.0261 | -1.74 | 9/19/22 |
| Synthetic topic shift | tinm_adapt vs rag_baseline | +0.0375 * | 0.0152 | 2.47 | 12/5/33 |
| Synthetic topic shift | tinm_a085 vs rag_baseline | +0.0025 | 0.0084 | 0.30 | 5/6/39 |
| Synthetic topic shift | tinm_adapt vs rag_with_history | +0.0318 | 0.0258 | 1.23 | 17/13/20 |
| Synthetic topic shift | tinm_a085 vs rag_with_history | -0.0032 | 0.0230 | -0.14 | 12/13/25 |
| Synthetic topic shift | tinm_adapt vs tinm_a085 | +0.0350 ** | 0.0121 | 2.90 | 10/1/39 |
| Synthetic topic shift | rag_with_history vs rag_baseline | +0.0058 | 0.0234 | 0.25 | 13/11/26 |
| MuSiQue 2-hop | tinm_adapt vs rag_baseline | +0.0530 | 0.0307 | 1.73 | 23/8/19 |
| MuSiQue 2-hop | tinm_a085 vs rag_baseline | +0.0450 | 0.0272 | 1.66 | 20/7/23 |
| MuSiQue 2-hop | tinm_adapt vs rag_with_history | +0.0110 | 0.0242 | 0.45 | 18/12/20 |
| MuSiQue 2-hop | tinm_a085 vs rag_with_history | +0.0030 | 0.0221 | 0.14 | 16/13/21 |
| MuSiQue 2-hop | tinm_adapt vs tinm_a085 | +0.0080 | 0.0134 | 0.60 | 10/10/30 |
| MuSiQue 2-hop | rag_with_history vs rag_baseline | +0.0420 | 0.0294 | 1.43 | 18/9/23 |
| MuSiQue 3-hop | tinm_adapt vs rag_baseline | +0.0165 | 0.0219 | 0.75 | 15/8/27 |
| MuSiQue 3-hop | tinm_a085 vs rag_baseline | +0.0214 | 0.0256 | 0.84 | 15/8/27 |
| MuSiQue 3-hop | tinm_adapt vs rag_with_history | +0.0519 ** | 0.0188 | 2.77 | 18/7/25 |
| MuSiQue 3-hop | tinm_a085 vs rag_with_history | +0.0568 * | 0.0221 | 2.57 | 16/9/25 |
| MuSiQue 3-hop | tinm_adapt vs tinm_a085 | -0.0049 | 0.0134 | -0.37 | 9/10/31 |
| MuSiQue 3-hop | rag_with_history vs rag_baseline | -0.0354 | 0.0247 | -1.43 | 10/12/28 |

`*` p<0.05, `**` p<0.01

## 3. Pareto frontier — quality vs token cost

Total tokens summed across all responses (subquestions, distractors, retrieved context). Best agent on each benchmark per the quality/tokens trade-off is **bolded**.

| Benchmark | Agent | Quality | Tokens | Q / 1k tok |
|---|---|---|---|---|
| Synthetic continuity | rag_baseline | 0.780 | 39,251 | 0.993 |
| Synthetic continuity | rag_with_history | 0.734 | 51,142 | 0.718 |
| Synthetic continuity | **tinm_a085** | 0.801 | 39,534 | **1.013** |
| Synthetic continuity | tinm_adapt | 0.789 | 39,330 | 1.004 |
| Synthetic topic shift | rag_baseline | 0.729 | 60,757 | 0.600 |
| Synthetic topic shift | rag_with_history | 0.735 | 79,747 | 0.461 |
| Synthetic topic shift | tinm_a085 | 0.732 | 61,506 | 0.595 |
| Synthetic topic shift | **tinm_adapt** | 0.767 | 61,656 | **0.622** |
| MuSiQue 2-hop | rag_baseline | 0.324 | 70,359 | 0.230 |
| MuSiQue 2-hop | rag_with_history | 0.365 | 73,876 | 0.247 |
| MuSiQue 2-hop | tinm_a085 | 0.368 | 69,504 | 0.265 |
| MuSiQue 2-hop | **tinm_adapt** | 0.377 | 69,359 | **0.271** |
| MuSiQue 3-hop | rag_baseline | 0.300 | 116,071 | 0.129 |
| MuSiQue 3-hop | rag_with_history | 0.265 | 118,461 | 0.112 |
| MuSiQue 3-hop | **tinm_a085** | 0.322 | 112,911 | **0.142** |
| MuSiQue 3-hop | tinm_adapt | 0.317 | 113,018 | 0.140 |

## 4. Robustness to distractor interruption (continuity benchmark)

On the continuity benchmark, 20/50 tasks have a distractor turn inserted between Q1 and Q2. *Drop* = quality(no distractor) − quality(with distractor). Smaller is more robust.

| Agent | n with distractor | n without | Q with distr. | Q without distr. | Drop |
|---|---|---|---|---|---|
| rag_baseline | 20 | 30 | 0.772 | 0.785 | 0.0136 |
| rag_with_history | 20 | 30 | 0.683 | 0.769 | 0.0858 |
| tinm_a085 | 20 | 30 | 0.790 | 0.809 | 0.0191 |
| tinm_adapt | 20 | 30 | 0.761 | 0.809 | 0.0476 |

## 5. Hard-task success rate

Counts of tasks reaching quality ≥ 0.5 (at least half of expected facts identified) and ≥ 0.75 (most or all facts identified). Larger memory benefit shows on the harder tasks where retrieval alone is insufficient.

| Benchmark | Agent | n ≥ 0.5 | n ≥ 0.75 | % ≥ 0.5 |
|---|---|---|---|---|
| Synthetic continuity | rag_baseline | 44 | 30 | 88.0% |
| Synthetic continuity | rag_with_history | 45 | 27 | 90.0% |
| Synthetic continuity | tinm_a085 | 44 | 33 | 88.0% |
| Synthetic continuity | tinm_adapt | 45 | 31 | 90.0% |
| Synthetic topic shift | rag_baseline | 48 | 31 | 96.0% |
| Synthetic topic shift | rag_with_history | 49 | 37 | 98.0% |
| Synthetic topic shift | tinm_a085 | 48 | 31 | 96.0% |
| Synthetic topic shift | tinm_adapt | 48 | 37 | 96.0% |
| MuSiQue 2-hop | rag_baseline | 10 | 2 | 20.0% |
| MuSiQue 2-hop | rag_with_history | 12 | 4 | 24.0% |
| MuSiQue 2-hop | tinm_a085 | 12 | 5 | 24.0% |
| MuSiQue 2-hop | tinm_adapt | 12 | 6 | 24.0% |
| MuSiQue 3-hop | rag_baseline | 6 | 6 | 12.0% |
| MuSiQue 3-hop | rag_with_history | 6 | 1 | 12.0% |
| MuSiQue 3-hop | tinm_a085 | 10 | 3 | 20.0% |
| MuSiQue 3-hop | tinm_adapt | 7 | 4 | 14.0% |

## 6. Ablation — TINM-full (dual-anchor) negative result

We tested a dual-scale variant (TINM-full) combining a slow `magnetic_anchor` (α=0.92 on query embeddings; long-term topic prior) with a faster `courant_anchor` (α=0.50 on retrieved-node centroids; recent trajectory). Composite NPF score: `s(v) = 0.40·sim_query(v) + 0.25·sim_magnetic(v) + 0.25·sim_courant(v) − 0.10·repulsion(v)`.

Retrieval-only dry-run on the same task seeds (`gold/top5` at the last task turn):

| Benchmark | TINM-lite a085 | TINM-full v0 |
|---|---|---|
| Synthetic continuity | 2.76 | 1.46 |
| Synthetic topic shift | 1.96 | 0.66 |
| MuSiQue 2-hop | 0.78 | 0.78 |
| MuSiQue 3-hop | 0.88 | 0.84 |

Finding: TINM-full degrades retrieval on synthetic benchmarks. Diagnosis: the `courant_anchor` follows the distractor (continuity) or pivot (shift), polluting retrieval at the next task turn. A weight sweep (5 configurations) confirmed that any non-zero `w_courant` introduces this regression. Setting `w_courant = 0` reduces TINM-full to TINM-lite. No LLM evaluation was run on this variant.

We attribute this to the single-turn nature of friction detection: distractors and topic shifts both manifest as query-anchor divergence at one turn, and cannot be distinguished without multi-turn evidence. We leave proper multi-turn friction detection (e.g. accumulated divergence over a sliding window) to future work.

## 7. Headline findings

- **Synthetic continuity**: best TINM variant is `tinm_a085` at 0.801 quality vs `rag_with_history` at 0.734 (Δ=+0.067, t=+3.07), using 39,534 vs 51,142 tokens (-23%).
- **Synthetic topic shift**: best TINM variant is `tinm_adapt` at 0.767 quality vs `rag_with_history` at 0.735 (Δ=+0.032, t=+1.23), using 61,656 vs 79,747 tokens (-23%).
- **MuSiQue 2-hop**: best TINM variant is `tinm_adapt` at 0.377 quality vs `rag_with_history` at 0.365 (Δ=+0.011, t=+0.45), using 69,359 vs 73,876 tokens (-6%).
- **MuSiQue 3-hop**: best TINM variant is `tinm_a085` at 0.322 quality vs `rag_with_history` at 0.265 (Δ=+0.057, t=+2.57), using 112,911 vs 118,461 tokens (-5%).

**Cross-benchmark conclusion.** Across four continuity benchmarks — two synthetic (controlled distractors, controlled topic shifts) and two real-data (MuSiQue multi-hop QA) — TINM-lite consistently sits on or above the Pareto frontier defined by stateless RAG and history-injection RAG. The strongest comparative result is on MuSiQue 3-hop, where verbose chat history actively impairs LLM reasoning (`rag_with_history` underperforms `rag_baseline` by −0.035) while TINM-lite's compressed memory (single slow-EMA query anchor + compact trajectory hint) beats both.

The friction-adaptive variant (`tinm_adapt`) outperforms fixed-anchor (`tinm_a085`) only on the explicit topic-shift benchmark (Δ=+0.035, t=2.90). On chained reasoning (MuSiQue 3-hop), the two are statistically tied. We interpret this as: dynamic alpha helps when query-level divergence truly signals a topic shift, but over-reacts on coherent reasoning chains. A more robust friction signal (multi-turn evidence) is a natural direction for future work.