"""Load 2WikiMultihopQA and convert to TNIM benchmark format.

Like the MuSiQue loader, this provides a natural continuity benchmark on
real Wikipedia text, but with a cleaner decomposition signal:
  - Each example exposes explicit (subj, rel, obj) evidence triplets.
  - We keep only 2-hop examples whose chain is strictly linear
    (obj_0 == subj_1) — i.e. the second hop's subject is the answer of the
    first. This is exactly the anaphoric case our memory mechanism targets.
  - We exclude "comparison" type (parallel rather than chained reasoning).
  - Q1 is rendered with explicit subject; Q2 uses the anaphoric template.

We reuse `_natural_subquestion` + `_RELATION_TEMPLATES` from
`musique_loader` since 2Wiki relations are similar Wikidata phrasings.
"""
from __future__ import annotations

import json

import numpy as np
from datasets import load_dataset

from .graph_generator import Graph, Node
from .musique_loader import _natural_subquestion
from .task_generator import Task


_DEFAULT_DATASET = "voidful/2WikiMultihopQA"


def _unquote(s: str) -> str:
    """Strip the surrounding double-quotes 2Wiki adds to titles."""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _parse_context(ctx: list) -> list[tuple[str, str]]:
    """Parse 2Wiki context entries into (title, joined_paragraph_text)."""
    parsed: list[tuple[str, str]] = []
    for entry in ctx:
        title = _unquote(entry[0])
        raw_sentences = entry[1]
        if isinstance(raw_sentences, str):
            try:
                sentences = json.loads(raw_sentences)
            except (json.JSONDecodeError, TypeError):
                sentences = [raw_sentences]
        else:
            sentences = list(raw_sentences)
        text = " ".join(s.strip() for s in sentences if isinstance(s, str) and s.strip())
        parsed.append((title, text))
    return parsed


def _evidences_to_subquestions(evidences: list) -> list[str] | None:
    """Render evidence triplets as natural sub-questions.

    Returns None if the chain is not strictly linear (obj_{i-1} != subj_i),
    so the caller can skip the example.
    """
    if not evidences:
        return None
    subqs: list[str] = []
    for i, ev in enumerate(evidences):
        subj, rel, _obj = ev[0], ev[1], ev[2]
        if i > 0 and subj != evidences[i - 1][2]:
            return None
        anchor = f"#{i}" if i > 0 else subj
        subqs.append(_natural_subquestion(f"{anchor} >> {rel}"))
    return subqs


def load_wiki2hop_benchmark(
    encoder,
    n_tasks: int = 50,
    seed: int = 42,
    dataset_name: str = _DEFAULT_DATASET,
) -> tuple[Graph, list[Task]]:
    rng = np.random.default_rng(seed)

    ds = load_dataset(dataset_name, split="validation", streaming=True)
    pool: list[dict] = []
    cap = n_tasks * 8
    for ex in ds:
        if ex.get("type") == "comparison":
            continue
        if len(ex["evidences"]) != 2:
            continue
        subqs = _evidences_to_subquestions(ex["evidences"])
        if subqs is None:
            continue
        ex_kept = dict(ex)
        ex_kept["_subquestions"] = subqs
        pool.append(ex_kept)
        if len(pool) >= cap:
            break

    if len(pool) < n_tasks:
        raise RuntimeError(
            f"Found only {len(pool)} qualifying 2-hop linear examples; "
            f"requested {n_tasks}"
        )

    indices = rng.choice(len(pool), size=n_tasks, replace=False)
    examples = [pool[int(i)] for i in indices]

    paragraph_map: dict[str, int] = {}
    paragraph_meta: list[tuple[str, str]] = []
    for ex in examples:
        for title, text in _parse_context(ex["context"]):
            if text and text not in paragraph_map:
                paragraph_map[text] = len(paragraph_meta)
                paragraph_meta.append((text, title))

    contents = [t for t, _ in paragraph_meta]
    if hasattr(encoder, "fit"):
        encoder.fit(contents)
    embeddings = encoder.encode(contents, convert_to_numpy=True, show_progress_bar=False)

    nodes: list[Node] = []
    for i, (text, title) in enumerate(paragraph_meta):
        full_content = f"[{title}] {text}"
        nodes.append(
            Node(
                id=i,
                topic_id=0,
                content=full_content,
                facts={},
                embedding=embeddings[i],
                timestamp=0,
                is_filler=False,
            )
        )
    graph = Graph(nodes=nodes, edges=[])

    tasks: list[Task] = []
    for task_id, ex in enumerate(examples):
        subquestions = ex["_subquestions"]

        facts_to_find: dict[str, str] = {}
        for i, ev in enumerate(ex["evidences"]):
            label = f"Q{i + 1}: {subquestions[i]}"
            facts_to_find[label] = ev[2]

        supporting_titles = {_unquote(sf[0]) for sf in ex["supporting_facts"]}
        gold_path: list[int] = []
        for title, text in _parse_context(ex["context"]):
            if title in supporting_titles and text in paragraph_map:
                gold_path.append(paragraph_map[text])

        goal_text = ex["question"]
        goal_embedding = encoder.encode(
            goal_text, convert_to_numpy=True, show_progress_bar=False
        )

        tasks.append(
            Task(
                id=task_id,
                topic_id=0,
                goal_text=goal_text,
                goal_embedding=goal_embedding,
                gold_path=gold_path,
                facts_to_find=facts_to_find,
                subquestions=subquestions,
                subquestion_fact_keys=[
                    [f"Q{i + 1}: {subquestions[i]}"] for i in range(len(subquestions))
                ],
            )
        )

    return graph, tasks
