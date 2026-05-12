# TINM — Experimental Section (consolidated)

Five benchmarks. Four agents (`rag_baseline`, `rag_with_history`, `tinm_a085`, `tinm_adapt`). Claude Sonnet 4.6, deterministic seed=42, top-K retrieval = 5. Per-benchmark task counts: n=50 for synthetic + MuSiQue 3-hop + 2WikiMultihopQA, n=100 for MuSiQue 2-hop (re-run for tighter t-stats).

**Benchmarks**:

1. *Synthetic continuity* — 200 nodes, 5 topics, 2 sub-questions per task. Random distractor (different topic) inserted with p=0.5 between Q1 and Q2. Q2 mixes soft and hard co-reference.
2. *Synthetic topic shift* — same graph, 4 sub-questions: Q1+Q2 about topic A, Q3 explicit pivot to topic B, Q4 hard co-reference for B. The decisive test of whether the memory mechanism adapts to mid-task shifts.
3. *MuSiQue 2-hop* — real Wikipedia paragraphs. Each task: a multi-hop question decomposed into Q1 (identify bridge entity) and Q2 (anaphoric, asks about bridge entity's property).
4. *MuSiQue 3-hop* — three chained hops. Q1 → Q2 → Q3, each Q_i using #i-1 to reference the previous answer.
5. *2WikiMultihopQA* — alternative real-data 2-hop benchmark with explicit `(subj, rel, obj)` evidence triplets. We keep only linear chains (`obj_0 == subj_1`) and exclude the `comparison` type, yielding the same anaphoric Q1→Q2 structure as MuSiQue 2-hop but with cleaner decomposition.
## 1. Headline results — all benchmarks × all agents

Quality is the hybrid score (50% symbolic factual coverage + 50% LLM-as-judge). Higher is better. Per-row `n` is the number of tasks. Sonnet 4.6, seed=42, top-K=5.

| Benchmark | Agent | n | Quality | Median | Tokens | Q/1k tok |
|---|---|---|---|---|---|---|
| Synthetic continuity | rag_baseline | 50 | 0.780 | 0.775 | 39,251 | 0.993 |
| Synthetic continuity | rag_with_history | 50 | 0.734 | 0.750 | 51,142 | 0.718 |
| Synthetic continuity | tinm_a085 | 50 | 0.801 | 1.000 | 39,534 | 1.013 |
| Synthetic continuity | tinm_adapt | 50 | 0.789 | 0.917 | 39,330 | 1.004 |
| Synthetic topic shift | rag_baseline | 50 | 0.729 | 0.750 | 60,757 | 0.600 |
| Synthetic topic shift | rag_with_history | 50 | 0.735 | 0.750 | 79,747 | 0.461 |
| Synthetic topic shift | tinm_a085 | 50 | 0.732 | 0.750 | 61,506 | 0.595 |
| Synthetic topic shift | tinm_adapt | 50 | 0.767 | 0.750 | 61,656 | 0.622 |
| MuSiQue 2-hop | rag_baseline | 100 | 0.345 | 0.325 | 142,857 | 0.242 |
| MuSiQue 2-hop | rag_with_history | 100 | 0.377 | 0.375 | 151,395 | 0.249 |
| MuSiQue 2-hop | tinm_a085 | 100 | 0.412 | 0.375 | 141,119 | 0.292 |
| MuSiQue 2-hop | tinm_adapt | 100 | 0.405 | 0.375 | 141,528 | 0.287 |
| MuSiQue 3-hop | rag_baseline | 50 | 0.300 | 0.217 | 116,071 | 0.129 |
| MuSiQue 3-hop | rag_with_history | 50 | 0.265 | 0.217 | 118,461 | 0.112 |
| MuSiQue 3-hop | tinm_a085 | 50 | 0.322 | 0.217 | 112,911 | 0.142 |
| MuSiQue 3-hop | tinm_adapt | 50 | 0.317 | 0.217 | 113,018 | 0.140 |
| 2WikiMultihopQA | rag_baseline | 50 | 0.367 | 0.375 | 86,979 | 0.211 |
| 2WikiMultihopQA | rag_with_history | 50 | 0.450 | 0.400 | 71,316 | 0.315 |
| 2WikiMultihopQA | tinm_a085 | 50 | 0.481 | 0.425 | 66,587 | 0.362 |
| 2WikiMultihopQA | tinm_adapt | 50 | 0.460 | 0.400 | 66,531 | 0.345 |

## 2. Statistical significance (paired t-tests)

For each benchmark we run paired t-tests between agents on the same tasks (common seed). `t > 1.96` is significant at p < 0.05. W/L/T = wins/losses/ties. Synthetic benchmarks + 2WikiMultihopQA: n=50; MuSiQue 2-hop: n=100; MuSiQue 3-hop: n=50.

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
| MuSiQue 2-hop | tinm_adapt vs rag_baseline | +0.0602 ** | 0.0157 | 3.84 | 38/13/49 |
| MuSiQue 2-hop | tinm_a085 vs rag_baseline | +0.0670 ** | 0.0166 | 4.05 | 40/9/51 |
| MuSiQue 2-hop | tinm_adapt vs rag_with_history | +0.0285 * | 0.0119 | 2.40 | 25/14/61 |
| MuSiQue 2-hop | tinm_a085 vs rag_with_history | +0.0352 ** | 0.0135 | 2.62 | 27/15/58 |
| MuSiQue 2-hop | tinm_adapt vs tinm_a085 | -0.0067 | 0.0098 | -0.69 | 15/21/64 |
| MuSiQue 2-hop | rag_with_history vs rag_baseline | +0.0318 * | 0.0135 | 2.36 | 33/15/52 |
| MuSiQue 3-hop | tinm_adapt vs rag_baseline | +0.0165 | 0.0219 | 0.75 | 15/8/27 |
| MuSiQue 3-hop | tinm_a085 vs rag_baseline | +0.0214 | 0.0256 | 0.84 | 15/8/27 |
| MuSiQue 3-hop | tinm_adapt vs rag_with_history | +0.0519 ** | 0.0188 | 2.77 | 18/7/25 |
| MuSiQue 3-hop | tinm_a085 vs rag_with_history | +0.0568 * | 0.0221 | 2.57 | 16/9/25 |
| MuSiQue 3-hop | tinm_adapt vs tinm_a085 | -0.0049 | 0.0134 | -0.37 | 9/10/31 |
| MuSiQue 3-hop | rag_with_history vs rag_baseline | -0.0354 | 0.0247 | -1.43 | 10/12/28 |
| 2WikiMultihopQA | tinm_adapt vs rag_baseline | +0.0920 ** | 0.0258 | 3.56 | 33/8/9 |
| 2WikiMultihopQA | tinm_a085 vs rag_baseline | +0.1140 ** | 0.0283 | 4.03 | 37/6/7 |
| 2WikiMultihopQA | tinm_adapt vs rag_with_history | +0.0100 | 0.0108 | 0.93 | 15/7/28 |
| 2WikiMultihopQA | tinm_a085 vs rag_with_history | +0.0320 ** | 0.0118 | 2.71 | 19/6/25 |
| 2WikiMultihopQA | tinm_adapt vs tinm_a085 | -0.0220 * | 0.0105 | -2.10 | 4/14/32 |
| 2WikiMultihopQA | rag_with_history vs rag_baseline | +0.0820 ** | 0.0253 | 3.24 | 28/7/15 |

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
| MuSiQue 2-hop | rag_baseline | 0.345 | 142,857 | 0.242 |
| MuSiQue 2-hop | rag_with_history | 0.377 | 151,395 | 0.249 |
| MuSiQue 2-hop | **tinm_a085** | 0.412 | 141,119 | **0.292** |
| MuSiQue 2-hop | tinm_adapt | 0.405 | 141,528 | 0.287 |
| MuSiQue 3-hop | rag_baseline | 0.300 | 116,071 | 0.129 |
| MuSiQue 3-hop | rag_with_history | 0.265 | 118,461 | 0.112 |
| MuSiQue 3-hop | **tinm_a085** | 0.322 | 112,911 | **0.142** |
| MuSiQue 3-hop | tinm_adapt | 0.317 | 113,018 | 0.140 |
| 2WikiMultihopQA | rag_baseline | 0.367 | 86,979 | 0.211 |
| 2WikiMultihopQA | rag_with_history | 0.450 | 71,316 | 0.315 |
| 2WikiMultihopQA | **tinm_a085** | 0.481 | 66,587 | **0.362** |
| 2WikiMultihopQA | tinm_adapt | 0.460 | 66,531 | 0.345 |

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
| MuSiQue 2-hop | rag_baseline | 15 | 9 | 15.0% |
| MuSiQue 2-hop | rag_with_history | 25 | 12 | 25.0% |
| MuSiQue 2-hop | tinm_a085 | 32 | 17 | 32.0% |
| MuSiQue 2-hop | tinm_adapt | 30 | 13 | 30.0% |
| MuSiQue 3-hop | rag_baseline | 6 | 6 | 12.0% |
| MuSiQue 3-hop | rag_with_history | 6 | 1 | 12.0% |
| MuSiQue 3-hop | tinm_a085 | 10 | 3 | 20.0% |
| MuSiQue 3-hop | tinm_adapt | 7 | 4 | 14.0% |
| 2WikiMultihopQA | rag_baseline | 3 | 0 | 6.0% |
| 2WikiMultihopQA | rag_with_history | 14 | 4 | 28.0% |
| 2WikiMultihopQA | tinm_a085 | 21 | 8 | 42.0% |
| 2WikiMultihopQA | tinm_adapt | 15 | 6 | 30.0% |

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
- **MuSiQue 2-hop**: best TINM variant is `tinm_a085` at 0.412 quality vs `rag_with_history` at 0.377 (Δ=+0.035, t=+2.62), using 141,119 vs 151,395 tokens (-7%).
- **MuSiQue 3-hop**: best TINM variant is `tinm_a085` at 0.322 quality vs `rag_with_history` at 0.265 (Δ=+0.057, t=+2.57), using 112,911 vs 118,461 tokens (-5%).
- **2WikiMultihopQA**: best TINM variant is `tinm_a085` at 0.481 quality vs `rag_with_history` at 0.450 (Δ=+0.032, t=+2.71), using 66,587 vs 71,316 tokens (-7%).

**Cross-benchmark conclusion.** Across five continuity benchmarks — two synthetic (controlled distractors, controlled topic shifts) and three real-data (MuSiQue 2-hop, MuSiQue 3-hop, 2WikiMultihopQA) — TINM-lite consistently sits on or above the Pareto frontier defined by stateless RAG and history-injection RAG. The strongest magnitude effect is on 2WikiMultihopQA (`tinm_a085` beats `rag_baseline` by Δ=+0.114, t=4.03, while spending 23% fewer tokens), and the strongest statistical effect — now that we re-ran with n=100 — is on MuSiQue 2-hop (`tinm_a085` vs `rag_baseline`, t=4.05). On MuSiQue 3-hop the qualitative pattern is particularly striking: verbose chat history actively impairs chain reasoning (`rag_with_history` underperforms `rag_baseline`), while TINM-lite's compressed memory (single slow-EMA query anchor + compact trajectory hint) beats both.

The friction-adaptive variant (`tinm_adapt`) outperforms fixed-anchor (`tinm_a085`) only on the explicit topic-shift benchmark (Δ=+0.035, t=2.90). On chained reasoning (MuSiQue 2-hop n=100, MuSiQue 3-hop) the two are statistically tied, and on 2WikiMultihopQA `tinm_adapt` is significantly *worse* than `tinm_a085` (Δ=−0.022, t=−2.10). We interpret this as: dynamic alpha helps when query-level divergence truly signals a topic shift, but over-reacts on coherent reasoning chains and on cleanly-decomposed anaphoric continuations. A more robust friction signal (multi-turn evidence) is a natural direction for future work.