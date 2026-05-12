"""Load MuSiQue (multi-hop QA) and convert to TNIM benchmark format.

MuSiQue gives us a natural continuity benchmark on real data:
  - Each "task" is a 2-hop question (e.g. "Who is the spouse of the Green performer?")
  - Decomposition gives two subqs, the second referencing the first via "#1"
  - We map "#1 >> relation" → "And what is its <relation>?" (natural anaphora)
  - Supporting paragraphs are explicitly labeled in the dataset (our gold path)

The information ocean is built by pooling all paragraphs across the sampled
examples (20 paragraphs each, 2 supporting + 18 hard distractors). After
deduplication this typically gives ~1000 nodes.
"""
from __future__ import annotations

import numpy as np
from datasets import load_dataset

from .graph_generator import Graph, Node
from .task_generator import Task


_DEFAULT_DATASET = "dgslibisey/MuSiQue"


# Common Wikidata relations → (explicit_template_with_{subj}, anaphoric_template).
# Covers most MuSiQue 2-hop questions naturally; fallback handles the rest.
_RELATION_TEMPLATES: dict[str, tuple[str, str]] = {
    "performer": ("Who performed {subj}?", "Who performed it?"),
    "creator": ("Who created {subj}?", "Who created it?"),
    "author": ("Who is the author of {subj}?", "Who is its author?"),
    "director": ("Who directed {subj}?", "Who directed it?"),
    "producer": ("Who produced {subj}?", "Who produced it?"),
    "screenwriter": ("Who wrote {subj}?", "Who wrote it?"),
    "founded by": ("Who founded {subj}?", "Who founded it?"),
    "owned by": ("Who owns {subj}?", "Who owns it?"),
    "discoverer or inventor": ("Who discovered or invented {subj}?", "Who discovered or invented it?"),
    "spouse": ("Who is the spouse of {subj}?", "Who is their spouse?"),
    "child": ("Who is the child of {subj}?", "Who is their child?"),
    "father": ("Who is the father of {subj}?", "Who is their father?"),
    "mother": ("Who is the mother of {subj}?", "Who is their mother?"),
    "place of birth": ("Where was {subj} born?", "Where were they born?"),
    "place of death": ("Where did {subj} die?", "Where did they die?"),
    "date of birth": ("When was {subj} born?", "When were they born?"),
    "country of citizenship": ("What is the country of citizenship of {subj}?", "What is their country of citizenship?"),
    "occupation": ("What is the occupation of {subj}?", "What is their occupation?"),
    "country": ("In what country is {subj}?", "In what country is it?"),
    "capital": ("What is the capital of {subj}?", "What is its capital?"),
    "located in the administrative territorial entity": ("In which administrative entity is {subj} located?", "In which administrative entity is it located?"),
    "shares border with": ("What does {subj} share a border with?", "What does it share a border with?"),
    "headquarters location": ("Where is the headquarters of {subj}?", "Where is its headquarters?"),
    "member of": ("What is {subj} a member of?", "What is it a member of?"),
    "part of": ("What is {subj} part of?", "What is it part of?"),
    "instance of": ("What kind of thing is {subj}?", "What kind of thing is it?"),
    "subclass of": ("What is {subj} a subclass of?", "What is it a subclass of?"),
    "industry": ("In what industry is {subj}?", "In what industry is it?"),
    "genre": ("What genre is {subj}?", "What genre is it?"),
    "language of work or name": ("What is the language of {subj}?", "What is its language?"),
    "publisher": ("Who published {subj}?", "Who published it?"),
    "record label": ("What is the record label of {subj}?", "What is its record label?"),
    "cast member": ("Who is in the cast of {subj}?", "Who is in its cast?"),
    "operator": ("Who operates {subj}?", "Who operates it?"),
    "educated at": ("Where was {subj} educated?", "Where were they educated?"),
    "award received": ("What award did {subj} receive?", "What award did they receive?"),
    "employer": ("Who employs {subj}?", "Who employs them?"),
    "manufacturer": ("Who manufactures {subj}?", "Who manufactures it?"),
    "publication date": ("When was {subj} published?", "When was it published?"),
    "release date": ("When was {subj} released?", "When was it released?"),
    "publication year": ("In what year was {subj} published?", "In what year was it published?"),
    "main subject": ("What is the main subject of {subj}?", "What is its main subject?"),
    "based on": ("What is {subj} based on?", "What is it based on?"),
    "language of work": ("What is the language of {subj}?", "What is its language?"),
    "inception": ("When was {subj} founded?", "When was it founded?"),
    "official language": ("What is the official language of {subj}?", "What is its official language?"),
    "continent": ("On what continent is {subj}?", "On what continent is it?"),
    "follows": ("What does {subj} follow?", "What does it follow?"),
    "followed by": ("What follows {subj}?", "What follows it?"),
}


def _natural_subquestion(decomp_q: str) -> str:
    """Convert MuSiQue's 'subject >> relation' shorthand to natural English.

    For #N placeholders (references to a prior hop's answer), produce an
    anaphoric form ("its <relation>"), which is exactly the co-reference
    case our memory mechanism is designed for.
    """
    if ">>" not in decomp_q:
        return decomp_q
    subj, rel = [s.strip() for s in decomp_q.split(">>", 1)]
    rel = rel.lower().rstrip("?.")
    is_anaphoric = subj.startswith("#")

    if rel in _RELATION_TEMPLATES:
        explicit, anaphoric = _RELATION_TEMPLATES[rel]
        return anaphoric if is_anaphoric else explicit.format(subj=subj)

    # Fallback: avoid awkward double-prepositions ("the member of of X")
    if rel.endswith(" of"):
        base = rel[:-3].strip()
        return f"What is it the {base} of?" if is_anaphoric else f"What is {subj} the {base} of?"
    if rel.endswith(" by"):
        return f"Who was it {rel}?" if is_anaphoric else f"Who was {subj} {rel}?"
    if is_anaphoric:
        return f"And what is its {rel}?"
    return f"What is the {rel} of {subj}?"


def load_musique_benchmark(
    encoder,
    n_tasks: int = 50,
    n_hops: int = 2,
    seed: int = 42,
    dataset_name: str = _DEFAULT_DATASET,
) -> tuple[Graph, list[Task]]:
    rng = np.random.default_rng(seed)

    ds = load_dataset(dataset_name, split="validation", streaming=True)
    pool: list[dict] = []
    for ex in ds:
        if not ex.get("answerable"):
            continue
        if len(ex["question_decomposition"]) != n_hops:
            continue
        pool.append(ex)
        if len(pool) >= n_tasks * 4:
            break

    if len(pool) < n_tasks:
        raise RuntimeError(
            f"Found only {len(pool)} qualifying {n_hops}-hop answerable examples; "
            f"requested {n_tasks}"
        )

    indices = rng.choice(len(pool), size=n_tasks, replace=False)
    examples = [pool[int(i)] for i in indices]

    # Pool unique paragraphs across examples (distractors are often shared)
    paragraph_map: dict[str, int] = {}
    paragraph_meta: list[tuple[str, str]] = []
    for ex in examples:
        for p in ex["paragraphs"]:
            text = p["paragraph_text"]
            if text not in paragraph_map:
                paragraph_map[text] = len(paragraph_meta)
                paragraph_meta.append((text, p["title"]))

    contents = [t for t, _ in paragraph_meta]
    if hasattr(encoder, "fit"):
        encoder.fit(contents)
    embeddings = encoder.encode(contents, convert_to_numpy=True, show_progress_bar=False)

    nodes: list[Node] = []
    for i, (text, title) in enumerate(paragraph_meta):
        # Prepend title to content for retrieval signal — MuSiQue paragraphs
        # often need title context to be discriminable.
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
        decomp = ex["question_decomposition"]
        subquestions = [_natural_subquestion(d["question"]) for d in decomp]

        facts_to_find: dict[str, str] = {}
        for i, d in enumerate(decomp):
            label = f"Q{i + 1}: {subquestions[i]}"
            facts_to_find[label] = d["answer"]

        gold_path = [
            paragraph_map[p["paragraph_text"]]
            for p in ex["paragraphs"]
            if p.get("is_supporting")
        ]

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
                    [f"Q{i + 1}: {subquestions[i]}"] for i in range(len(decomp))
                ],
            )
        )

    return graph, tasks
