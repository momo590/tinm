# TINM: Compressed Trajectory Memory for Multi-Turn LLM Agents

**Status**: draft skeleton. Abstract and Introduction are drafted in full prose;
Method is half-drafted; Related Work is a list of pointers; Results section
imports the consolidated experimental output (`benchmark/runs/paper_results/paper_section.md`).

---

## Abstract (draft v0 — ~210 words)

LLM agents that span multiple turns of interaction face a structural memory
trade-off. *Stateless RAG* re-issues retrieval at every turn, ignoring prior
context, and fails when later questions are anaphoric. *History-augmented RAG*,
the dominant production pattern, injects the full chat transcript into the LLM
prompt, but inflates token cost and — on longer reasoning chains — actively
impairs LLM performance by surfacing intermediate answers that confuse the
target hop.

We propose **TINM** (Turtle-Inspired Navigation Memory), a memory architecture
in which the agent maintains a single compressed *query anchor* updated via
slow exponential moving average across turns, blends it with the current query
for retrieval, and emits a *compact trajectory hint* (prior queries only, no
responses) to the language model. The mechanism is grounded in three primitives
from the substrate: an information ocean (the retrievable corpus), a latent
state (the anchor + visit history), and a multi-level resolution hierarchy
(here: query-level vs. node-level signals).

We evaluate TINM on four continuity benchmarks—two synthetic (controlled
distractors, controlled topic shifts) and two real-data (MuSiQue 2- and
3-hop)—against stateless RAG and history-augmented RAG with shared encoder,
seed, and retrieval depth. TINM achieves or exceeds the quality of
history-augmented RAG on every benchmark while using 5–23 % fewer tokens,
with statistically significant gains on three of the four (p < 0.05). On the
hardest benchmark (MuSiQue 3-hop), history-augmented RAG underperforms
stateless RAG by –0.035 quality, while TINM beats it by +0.057 (t = 2.57).
The friction-adaptive variant of TINM additionally provides a 2.9 σ advantage
over a fixed-anchor TINM on synthetic topic shifts, validating dynamic alpha
modulation when conversational topic changes. An ablation of a dual-anchor
variant (TINM-full) is reported as a negative result: a single-turn friction
signal cannot reliably distinguish transient distractors from persistent topic
shifts.

---

## 1. Introduction

The dominant pattern for memory in LLM agents today is *history-augmented
retrieval*: at each turn, the system retrieves relevant context from an
external corpus (Lewis et al., 2020; Gao et al., 2024) and stuffs the
multi-turn conversation history into the prompt window. This works as long as
turns are independent or near-stateless. It fails, in two distinct ways, when
conversations stretch over multiple turns and topics:

1. *Stateless RAG* discards prior turns entirely. Anaphoric questions
   ("And what about its capital?") lose their referent at the retrieval
   layer; the agent retrieves on the wrong signal and emits hedged or wrong
   answers.
2. *History-augmented RAG* injects the full chat transcript into every LLM
   call. Token cost grows linearly with the number of turns, and—more
   subtly—the verbose history can pollute the LLM's attention on multi-hop
   reasoning, surfacing intermediate-hop answers in a way that confuses the
   target hop.

The cleanest empirical demonstration of (2) in our experiments is on
MuSiQue 3-hop: history-augmented RAG scores 0.265, while *stateless* RAG
scores 0.300. Adding more context made the model worse.

We argue that the right level of memory is neither *none* nor *all*: it is a
*compressed latent state* that survives across turns and reaches the LLM
through (a) the retrieval-time embedding it shapes and (b) a minimal textual
hint listing prior queries. We instantiate this principle as **TINM**, a
small architecture with two variants—a fixed-EMA anchor (`tinm_a085`) and
a friction-adaptive anchor (`tinm_adapt`)—and validate it on four
benchmarks: synthetic continuity, synthetic topic shift, MuSiQue 2-hop, and
MuSiQue 3-hop. Across these:

- TINM ties or beats stateless RAG on every benchmark (significant on shift
  and MuSiQue 2-hop).
- TINM significantly beats history-augmented RAG on three of four
  benchmarks (p < 0.05–p < 0.01).
- TINM uses 5–23 % fewer tokens than history-augmented RAG on every
  benchmark.

The contribution is thus:

- **A practical memory design** (TINM-lite v2) that is a Pareto improvement
  over both stateless and history-augmented RAG on continuity tasks.
- **Four benchmarks** for multi-turn continuity, two synthetic
  (with controlled distractor and topic-shift variation) and two from
  MuSiQue, with reproducible seeds.
- **A negative ablation** of a dual-anchor design, identifying single-turn
  friction detection as the limiting factor for memory unification across
  distractor-shift vs. persistent-shift regimes.

---

## 2. Related Work

*[Skeleton — write prose later. See `related_work.md` for full bibliographic
pointers and notes.]*

- **Retrieval-augmented generation (RAG).** Lewis et al. 2020 (RAG-token,
  RAG-sequence); Borgeaud et al. 2022 (RETRO); Gao et al. 2024 (survey).
  Our work refines RAG's multi-turn memory regime rather than replacing
  the retrieval primitive.
- **Memory in LLM agents.** Park et al. 2023 (generative agents memory);
  Packer et al. 2024 (MemGPT/Letta); Anthropic 2024 (memory tool); MCP
  protocol (Anthropic 2024). Our compressed anchor differs from these
  in not being a stored document; it is a state vector.
- **Successor Representations.** Dayan 1993; Russek et al. 2017;
  Momennejad et al. 2017; Stachenfeld et al. 2017. The substrate doc
  draws on multi-scale SR for the long-term memory layer; the present
  paper realises a single-scale variant.
- **Cognitive maps and grid cells.** Whittington et al. 2022 (TEM);
  Eichenbaum 2017 (the hippocampus as a memory space). The anchor is
  loosely analogous to an entorhinal grid-cell prior on the information
  ocean's geometry.
- **World models / planning in latent space.** Hafner et al. 2023
  (DreamerV3). Their explicit recurrent latent state inspires the
  compressed state idea but with much heavier machinery than required
  here.
- **Multi-hop QA benchmarks.** Yang et al. 2018 (HotpotQA);
  Trivedi et al. 2022 (MuSiQue); Ho et al. 2020 (2WikiMultihopQA).
  We use MuSiQue for its explicit decomposition, which gives us
  ground-truth subquestion structure.
- **Animal navigation (biomimetic inspiration).** Lohmann et al. 2008
  (magnetic imprinting in sea turtles); Gallistel 1990 (path integration);
  Buzsáki & Moser 2013 (memory–space coupling). Our "magnetic anchor" is
  a direct metaphorical lift.
- **Friction / metacognition in agents.** Cox 2005 (metacognition in AI);
  active inference literature (Friston 2010 ff.). Friction-adaptive alpha
  is our minimal instantiation; multi-turn friction detection is open work.

---

## 3. Method

### 3.1 Setting

We consider an agent receiving a sequence of user queries $q_1, q_2, \dots,
q_T$ in a single task, retrieving from a static information ocean
$\mathcal{V} = \{v_1, \dots, v_N\}$ of textual nodes (paragraphs in real
data; templated factual snippets in synthetic data). At each turn $t$ the
agent emits a textual response $r_t$. The objective is to maximise factual
coverage over the union of expected facts for the task while minimising
total tokens injected into the LLM prompt across the task.

### 3.2 The TINM-lite agent

**State.** Per task, the agent maintains:

$$ \mathcal{S}_t = (\mathbf{a}_t, \mathbf{Q}_{<t}) $$

where $\mathbf{a}_t \in \mathbb{R}^d$ is the *query anchor* and
$\mathbf{Q}_{<t} = (q_1, \dots, q_{t-1})$ is the (text) trajectory of prior
queries. State is reset on every new task.

**Retrieval.** At turn $t$, with $\mathbf{q}_t = \mathrm{enc}(q_t)$,

$$ \mathbf{r}_t = w_q \mathbf{q}_t + w_a \mathbf{a}_{t-1} $$

and top-$K$ nodes are selected by cosine similarity to $\mathbf{r}_t$.
On turn 1, $\mathbf{a}_0$ is undefined and $\mathbf{r}_1 = \mathbf{q}_1$.

**Anchor update (TINM-a085).** Slow EMA:

$$ \mathbf{a}_t = \alpha \mathbf{a}_{t-1} + (1-\alpha) \mathbf{q}_t,
\qquad \alpha = 0.85 $$

**Anchor update (TINM-adapt).** Friction-adaptive $\alpha$ at each turn:

$$ c_t = \max(0, \cos(\mathbf{q}_t, \mathbf{a}_{t-1})) $$
$$ \alpha_t = \alpha_\min + (\alpha_\max - \alpha_\min) c_t,
\qquad \alpha_\min = 0.20, \alpha_\max = 0.95 $$
$$ \mathbf{a}_t = \alpha_t \mathbf{a}_{t-1} + (1-\alpha_t) \mathbf{q}_t $$

High consistency (chains, soft co-reference) keeps the anchor frozen.
Low consistency (genuine topic shift) drives $\alpha$ low and adapts the
anchor.

**LLM prompt.** A *trajectory hint* exposes prior queries without their
responses:

```
Prior questions in this conversation: (1) q_1 ; (2) q_2 ; …
Context: [Node id_1] … [Node id_K]
Now answer this follow-up question: q_t
```

On turn 1 (no prior queries), the hint is omitted, recovering a vanilla
RAG prompt.

### 3.3 Why the trajectory hint?

The retrieval-only anchor is sufficient to bring the right nodes into the
prompt, but the LLM still has to *resolve the anaphora in the current
query*. Our smoke tests confirmed that without the trajectory hint, the
LLM frequently refused to answer ("I cannot interpret 'its' in this
context"). Including prior queries as text—but not their answers—provides
the disambiguation context at a fraction of the cost of full
history-augmented RAG, which appends both prior queries *and*
prior responses. On MuSiQue 3-hop in particular, surfacing prior responses
hurts: it leads the LLM to confuse the target hop with the intermediate
hop.

### 3.4 Hyperparameter choices

All hyperparameters are fixed across all experiments:

| Parameter | Value | Notes |
|---|---|---|
| Top-K retrieval | 5 | Standard |
| $w_q$, $w_a$ | 0.5, 0.5 | Equal blend |
| $\alpha$ (TINM-a085) | 0.85 | Slow EMA, reverberant memory |
| $\alpha_\min$, $\alpha_\max$ (TINM-adapt) | 0.20, 0.95 | Wide range; lets friction fully drive update |
| Embedding | TF-IDF (1, 2)-gram, sublinear-TF | sklearn defaults |
| LLM | Claude Sonnet 4.6 | Same for all agents and the judge |
| Max tokens | 512 | Per response |
| Seed | 42 | Graph, tasks, distractor injection, all derive from this |

### 3.5 Baselines

- **rag_baseline**: stateless top-$K$ retrieval, no history in LLM prompt.
  Equivalent to standard RAG without conversation memory.
- **rag_with_history**: history-enriched retrieval (full prior-query string
  concatenated into the retrieval embedding) and full multi-turn chat
  passed to the LLM. The production-standard baseline.

---

## 4. Experimental Setup

### 4.1 Benchmarks

We construct four benchmarks (50 tasks each):

**B1. Synthetic continuity.** A 200-node graph with 5 topics and 5 facts
per topic. Each task is 2 sub-questions targeting facts in a single topic;
Q1 is topic-explicit, Q2 is co-referential (soft or hard). A
different-topic distractor turn is inserted with probability 0.5 between
Q1 and Q2.

**B2. Synthetic topic shift.** Same graph. 4 sub-questions per task:
Q1+Q2 about topic A, Q3 is an explicit pivot to topic B, Q4 is a
*hard* co-referential question for B (no fact keyword, no topic mention).
This is the decisive test for whether the memory mechanism follows a
topic change.

**B3. MuSiQue 2-hop.** 50 answerable 2-hop questions from MuSiQue,
real Wikipedia paragraphs. Each task gives Q1 (bridge entity) and Q2
(anaphoric, asks about bridge's property). Q2 is generated from the
"#1 >> relation" decomposition using a curated relation→template map
(see §4.2).

**B4. MuSiQue 3-hop.** Same construction with 3 chained hops.

### 4.2 Quality measurement

Combined symbolic + LLM-judge score, equally weighted:

- *Symbolic*: per-subquestion token-set check (all content tokens of the
  expected answer must appear in the response). The per-subquestion
  variant prevents over-matching across responses.
- *Judge*: a separate Claude Sonnet 4.6 call given the task's expected
  facts and the response, instructed to penalise hedged, confused, or
  multi-topic answers even when the correct value technically appears.

### 4.3 Reproducibility

All four benchmarks reproduce from `seed=42`. Scripts:
`runs/pilot_v2.py` (B1, B2), `runs/pilot_real.py --hops {2,3}` (B3, B4),
`runs/consolidate.py` for tables.

---

## 5. Results

*[Imported from `benchmark/runs/paper_results/paper_section.md` — sections
1 through 7 of the consolidated report.]*

See attached consolidated report. The five tables there constitute the
complete numerical evidence for the paper. The headline narrative:

- **Pareto improvement** on every benchmark vs. history-augmented RAG.
- **Significantly better** quality on B1 (p < 0.01), B2-adapt (p < 0.01),
  and B4 (p < 0.05).
- **20–67 % more hard tasks** (quality ≥ 0.5) than baselines on the
  hardest benchmark (B4).
- **History-augmented RAG underperforms stateless** on B4 by −0.035,
  illustrating the failure mode that motivates compressed memory.

---

## 6. Discussion

### 6.1 Why does history hurt on 3-hop chains?

The most striking finding is that on MuSiQue 3-hop,
`rag_with_history` scores *below* `rag_baseline`. The mechanism is
visible in qualitative inspection: on chain Q3, the LLM with full
history sees the answers to Q1 and Q2 in its prompt. Those intermediate
answers are themselves entities, and the LLM occasionally surfaces them
as the Q3 answer ("the answer to your question is Steve Hillage" when in
fact Steve Hillage is Q1's answer and Q3 is asking about Hillage's
spouse). The trajectory hint avoids this: by carrying only the prior
*questions*, not their answers, it disambiguates the anaphora without
seeding the LLM with hop-mixing material.

### 6.2 Why does adaptive only beat fixed on synthetic shift?

The friction-adaptive update is designed for a single, well-defined kind
of friction: the current query diverges from the anchor. On B2 this
divergence cleanly corresponds to the pivot turn Q3 ("Now let's switch
to Beta"), so $\alpha$ correctly drops and the anchor relocates.

On B1, the same divergence happens transiently at the distractor turn,
but the *next* task turn returns to the original topic. With fixed slow
$\alpha = 0.85$, the anchor barely moves at the distractor; with
adaptive $\alpha$, the anchor moves further toward the distractor and
needs another turn to recover. Hence adaptive shows a slightly larger
distractor drop on B1 (−0.048 vs −0.019) without any compensating
quality gain. On B4 (MuSiQue 3-hop), each hop *looks* like a topic
shift to a single-turn friction detector (the queries contain new
entities), so adaptive over-reacts and ties fixed.

The clean diagnosis: a single-turn friction signal cannot distinguish
*transient* from *persistent* divergence. This is the motivation for
the TINM-full v2 we propose in §6.4 and the dual-anchor ablation we
discuss next.

### 6.3 Negative result: dual-anchor (TINM-full)

A natural extension is to maintain two anchors at different time scales,
inspired by the substrate's multi-scale SR formulation: a slow
*magnetic* anchor (α = 0.92 on queries; the long-term topic prior) and
a faster *courant* anchor (α = 0.50 on retrieved-node centroids; the
trajectory tracker). A composite NPF score combines both with a
repulsion term to discourage revisits.

Retrieval-only dry runs (see §6 of the consolidated report) show this
design *degrades* retrieval on synthetic benchmarks: any positive
weight on the courant anchor pollutes Q2's retrieval with the
distractor's or pivot's content. The dual-anchor improves only on
MuSiQue chains, where both anchors agree on the coherent domain. A
five-configuration weight sweep confirmed: $w_\mathrm{courant}=0$
recovers TINM-lite, and any positive weight introduces synthetic
regression. We therefore did not run LLM evaluation on this variant.

The diagnosis is the same as §6.2: distinguishing a transient
distractor from a persistent topic shift requires multi-turn evidence
that a single-turn friction signal cannot supply. Properly gating
multi-anchor update is left to future work.

### 6.4 Limitations

- **TF-IDF substrate.** Our embeddings are TF-IDF over (1,2)-grams. Real
  deployments would use sentence-transformer embeddings. We expect the
  qualitative pattern to hold (the mechanism is embedding-agnostic), but
  absolute quality on MuSiQue should rise with better embeddings.
- **n = 50.** Statistical significance is marginal on some comparisons
  (t in [1.6, 1.8]). Larger task pools would tighten confidence
  intervals.
- **Single LLM (Claude Sonnet 4.6).** Results may not transfer
  identically to other models; reasoning-stronger or
  reasoning-weaker LLMs may shift the balance.
- **Domain is multi-hop QA.** Other multi-turn settings (tool-using
  agents, code assistants, dialog) may have different memory-vs-history
  trade-offs; we believe TINM applies but have not measured it.

### 6.5 Future work

- **Multi-turn friction detection.** Accumulated divergence over a
  sliding window; cycle detection in retrieval; LLM-side
  cross-reference of recent answers. Should resolve the
  distractor-vs-shift ambiguity that limits both adaptive and
  dual-anchor variants.
- **Larger benchmarks.** 200–500 tasks per benchmark; full HotpotQA
  for redundancy on real data; conversation-style benchmarks
  (CoQA, QuAC) that mix retrieval with extended dialogue.
- **Anchor sharing across agents.** The TINM substrate framing
  includes a *Personal Context Protocol* (PCP) for exposing the
  compressed state $z_t$ across systems. Empirically validating
  cross-agent handoffs is a separate experimental program.

---

## 7. Conclusion

TINM is a one-paragraph idea: keep one slow-EMA query anchor; blend it
with the current query for retrieval; tell the LLM what was asked
before but not what was answered. Across four benchmarks, this
mechanism Pareto-improves on both stateless RAG and the
history-augmented RAG that dominates production. The most striking
single finding is that *adding chat history to the LLM prompt actively
hurts performance on chained multi-hop reasoning*: a result with
direct implications for agent design more broadly.

The thesis of the underlying manifesto — that memory should be a
*navigation policy*, not a *store* — is supported empirically here in
its lightest form. Heavier variants (dual anchor, multi-scale SR,
explicit friction detection) await better multi-turn friction signals.

---

## Limitations (as a separate boxed paragraph for the camera-ready)

We test on TF-IDF retrieval, n = 50 tasks per benchmark, a single LLM
(Claude Sonnet 4.6), and the multi-hop QA domain only. Our negative
ablation suggests the natural next-step design (dual-anchor with
trajectory tracker) requires friction signals we have not yet built.

---

## References

*[Placeholder — populate from `related_work.md` once content is finalised.]*
