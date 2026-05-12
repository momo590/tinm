# Related Work — Pointers and Notes

Bibliographic suggestions for the paper, grouped by sub-area. Each entry has:
**reference** | *one-line takeaway* | (how to use in our Related Work).

Some references are well-known and citable as standard; others need
verification against the published versions. The substrate document and the
manifesto already cite many of these — they can be reused.

---

## A. Retrieval-Augmented Generation (RAG)

- **Lewis et al. 2020** | *RAG-token / RAG-sequence: dense retrieval + seq2seq*
  | Foundational RAG paper. Cite as the baseline paradigm we extend.
- **Borgeaud et al. 2022** | *RETRO: chunked retrieval into the LM*
  | Shows retrieval as a primitive operation; we operate at a higher level.
- **Gao et al. 2024 (survey)** | *RAG taxonomy, recent advances*
  | Use to position TINM in the survey's "advanced RAG" category.
- **Asai et al. 2023 (Self-RAG)** | *Reflection tokens to control retrieval*
  | Adjacent: also dynamic retrieval, but with explicit retrieve/no-retrieve
    decisions. We don't add reflection — we shape the retrieval embedding.
- **Trivedi et al. 2022 (IRCoT)** | *Interleaved retrieval and CoT for multi-hop*
  | Directly compares to multi-hop RAG strategies. They retrieve between
    reasoning steps; we retrieve at every user turn but persist memory across.

## B. Memory in LLM Agents

- **Park et al. 2023 (Generative Agents)** | *Memory stream + reflection*
  | Closest production-system reference. Their memory is a stored log; ours
    is a state vector. We can cite as motivating context.
- **Packer et al. 2024 (MemGPT / Letta)** | *Hierarchical context paging*
  | Memory is a managed window. We avoid windowing — the anchor is a single
    fixed-dimensional vector.
- **Anthropic 2024 (Memory tool, MCP)** | *Filesystem-mounted persistent memory*
  | Production-style external memory. Our anchor lives inside the agent,
    not as an external resource.
- **Wang et al. 2023 (Voyager)** | *Skill library for LLM agents in Minecraft*
  | Long-term memory as accumulated skills. Different problem (skill reuse
    vs context retrieval), useful as a contrast.

## C. Successor Representations and Predictive Memory

- **Dayan 1993** | *Successor Representation in RL*
  | The original SR. Substrate doc draws on this directly; cite when
    introducing the multi-scale memory idea (even though TINM-lite uses a
    single scale).
- **Russek et al. 2017** | *SR-based learning in humans*
  | Empirical / behavioural validation in cognitive science.
- **Momennejad et al. 2017** | *Successor representation in working memory*
  | Bridge to working-memory literature. Useful for framing the anchor as
    a working-memory-style compressed state.
- **Stachenfeld et al. 2017** | *Hippocampus as a predictive map*
  | Neural side: place cells and SR. Cite when motivating the
    "compressed-state-as-cognitive-map" framing.

## D. Cognitive Maps and Multi-Scale Memory

- **Whittington et al. 2022 (Tolman-Eichenbaum Machine, TEM)** | *NN model
    of hippocampal-entorhinal memory*
  | Strong theoretical grounding. TEM uses grid + place cells for
    multi-scale map; we use a single anchor, but the inspiration is
    explicit. Cite when justifying multi-scale SR (future-work).
- **Buzsáki & Moser 2013** | *Memory and navigation share neural machinery*
  | Useful one-line citation supporting the substrate framing.
- **Eichenbaum 2017** | *Hippocampal memory beyond space*
  | Generalises the navigation framing to non-spatial memory.

## E. World Models and Latent Planning

- **Hafner et al. 2023 (DreamerV3)** | *World model with recurrent latent state*
  | Closest "compressed state" in RL. Use to compare with: TINM's anchor is
    a deliberately much simpler primitive aimed at LLM-agent context
    management, not full-blown imagination-based planning.

## F. Multi-Hop QA Benchmarks

- **Yang et al. 2018 (HotpotQA)** | *Multi-hop QA over Wikipedia*
  | Standard benchmark. We use MuSiQue instead because of explicit
    decomposition (HotpotQA has supporting-sentence annotations but not
    decomposed subquestions).
- **Trivedi et al. 2022 (MuSiQue)** | *2/3/4-hop questions with explicit
    decomposition*
  | The dataset we use for B3 and B4. Cite as primary real-data source.
- **Ho et al. 2020 (2WikiMultiHopQA)** | *Alternative multi-hop dataset*
  | Mention as a potential additional benchmark.
- **HotpotQA leaderboard / SoTA in 2024** | Verify current state-of-the-art
  numbers for context, but our work is about a *different* metric (Pareto
  on quality-vs-tokens, not raw quality).

## G. Animal Navigation (Biomimetic Anchor)

- **Lohmann et al. 2008** | *Magnetic imprinting in sea turtles*
  | Directly cited as the metaphor for the "magnetic anchor" in the
    substrate. Use sparingly in the paper itself — the magnetic-turtle
    framing is in the manifesto, but the published paper should
    present it as a useful metaphor, not a justification.
- **Gallistel 1990** | *The Organization of Learning*
  | Path integration framework; useful as general background.
- **Buzsáki & Moser 2013 (already cited)**

## H. Friction and Metacognition

- **Cox 2005** | *Metacognition in computation: a survey*
  | Standard reference for self-monitoring in AI.
- **Russek et al. 2022** | *Cognitive control as resource allocation*
  | Loosely related; friction as a resource-allocation signal.
- **Friston 2010** | *The free-energy principle*
  | Active-inference framing. Substrate references this; for a paper,
    only cite if the friction-adaptive discussion goes deeper than
    "alpha varies with similarity".

## I. Conversational and Continuity Benchmarks

- **Choi et al. 2018 (QuAC)** | *Conversational QA*
  | Real-world conversational benchmark; mentioned as future work in §6.5.
- **Reddy et al. 2019 (CoQA)** | *Conversational QA dataset*
  | Similar, useful complementary dataset.
- **Dziri et al. 2022 (Faithfulness in conversations)** | *Hallucination in
    chat*
  | Useful framing for "why history can hurt": models can confabulate by
    over-conditioning on prior turns.

---

## Quick citation table (verify before submission)

| Tag | First author | Year | Venue |
|---|---|---|---|
| @lewis_rag | Lewis | 2020 | NeurIPS |
| @borgeaud_retro | Borgeaud | 2022 | ICML |
| @gao_rag_survey | Gao | 2024 | preprint |
| @asai_selfrag | Asai | 2023 | ICLR |
| @trivedi_ircot | Trivedi | 2022 | ACL |
| @park_generative_agents | Park | 2023 | UIST |
| @packer_memgpt | Packer | 2024 | preprint |
| @wang_voyager | Wang | 2023 | preprint |
| @dayan_sr | Dayan | 1993 | Neural Computation |
| @russek_sr | Russek | 2017 | PLoS Comp Bio |
| @momennejad_sr_wm | Momennejad | 2017 | Nat Hum Behav |
| @stachenfeld_predictive_map | Stachenfeld | 2017 | Nat Neuro |
| @whittington_tem | Whittington | 2022 | Cell |
| @buzsaki_moser | Buzsáki & Moser | 2013 | Nat Neuro |
| @eichenbaum_beyond_space | Eichenbaum | 2017 | Nat Rev Neuro |
| @hafner_dreamer | Hafner | 2023 | preprint |
| @yang_hotpot | Yang | 2018 | EMNLP |
| @trivedi_musique | Trivedi | 2022 | TACL |
| @ho_2wiki | Ho | 2020 | COLING |
| @lohmann_turtles | Lohmann | 2008 | PNAS |
| @choi_quac | Choi | 2018 | EMNLP |
| @reddy_coqa | Reddy | 2019 | TACL |

---

## Suggested citation density

Target: ~30 citations total in the final paper (modest for a focused
empirical paper, appropriate for the scope). The bulk of the
citation budget should go to:

1. RAG (sections A) — ~5 cites
2. Memory in LLM agents (section B) — ~5 cites
3. SR + cognitive maps (sections C, D) — ~6 cites
4. Multi-hop QA (section F) — ~3 cites
5. Animal navigation, friction, conversational (G, H, I) — ~4 cites combined
6. Specific architectural references (Sonnet, MuSiQue dataset, etc.) — ~4 cites

The substrate doc already lists most of these. Cross-reference there
before tracking down individual papers.
