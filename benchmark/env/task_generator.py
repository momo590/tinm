from dataclasses import dataclass, field

import numpy as np

from .graph_generator import FACTS, TOPICS, Graph


@dataclass
class Task:
    id: int
    topic_id: int
    goal_text: str
    goal_embedding: np.ndarray
    gold_path: list[int]
    facts_to_find: dict[str, str]
    subquestions: list[str]
    subquestion_fact_keys: list[list[str]]


# Each entry has THREE phrasings:
#   0 = "explicit":    mentions the topic         ("Project Alpha deadline")
#   1 = "soft coref":  drops the topic, keeps the fact keyword   ("its deadline")
#   2 = "hard coref":  drops both topic AND fact keyword         ("And when?")
# The first subq uses (0). Subsequent subqs use (1) or (2) based on
# `hard_coref_probability` — (2) is the true memory test.
SUBQUESTION_TEMPLATES: dict[int, dict[str, tuple[str, str, str]]] = {
    0: {
        "deadline":   ("When is the Project Alpha deadline?",          "When is the deadline?",           "And when?"),
        "lead":       ("Who is leading Project Alpha?",                 "Who is leading it?",              "And who is in charge?"),
        "budget":     ("What is the budget for Project Alpha?",         "What is the budget?",             "And how much?"),
        "status":     ("What is the current status of Project Alpha?",  "What is the current status?",     "And how is it going?"),
        "tech_stack": ("Which technologies are used in Project Alpha?", "Which technologies are used?",    "And how is it built?"),
    },
    1: {
        "deadline":  ("When is the Project Beta deadline?",                     "When is the deadline?",                       "And when?"),
        "client":    ("Who is the client for Project Beta?",                    "Who is the client?",                          "And for whom?"),
        "team_size": ("How many engineers are on Project Beta?",                "How many engineers are on the team?",         "And how many people?"),
        "status":    ("What is the current status of Project Beta?",            "What is the current status?",                 "And how is it going?"),
        "language":  ("Which programming language is Project Beta written in?", "Which programming language is it written in?", "And how is it built?"),
    },
    2: {
        "bank":            ("Which bank handles my personal finances?", "Which bank am I using?",  "And where?"),
        "advisor":         ("Who is my financial advisor?",             "Who is the advisor?",     "And who is it?"),
        "monthly_savings": ("How much do I save each month?",            "How much each month?",    "And how much?"),
        "retirement_age":  ("What is my planned retirement age?",        "At what age?",            "And when?"),
        "broker":          ("Which broker do I use for investments?",    "Which broker?",           "And through whom?"),
    },
    3: {
        "paper_title":  ("What is the title of the ML research paper?",     "What is its title?",         "And the name?"),
        "first_author": ("Who is the first author of the ML paper?",        "Who is the first author?",   "And who?"),
        "conference":   ("At which conference was the ML paper presented?", "At which conference?",       "And where was it shown?"),
        "year":         ("In which year was the ML paper published?",       "In which year?",             "And when?"),
        "key_insight":  ("What is the key insight of the ML paper?",        "What is the key insight?",   "And the main finding?"),
    },
    4: {
        "destination":      ("Which cities are we visiting in Japan?", "Which cities are we visiting?", "And where?"),
        "hotel":            ("Where are we staying in Japan?",         "Where are we staying?",         "And the accommodation?"),
        "departure_date":   ("When do we leave for Japan?",            "When do we leave?",             "And when?"),
        "return_date":      ("When do we return from Japan?",          "When do we return?",            "And then back?"),
        "travel_companion": ("Who is travelling with me to Japan?",    "Who is travelling with me?",    "And with whom?"),
    },
}


GOAL_TEMPLATES: dict[int, str] = {
    0: "Compile a status report for Project Alpha covering the key facts.",
    1: "Gather information for the Project Beta planning meeting.",
    2: "Prepare an overview of the personal finance situation.",
    3: "Summarize the key research findings on navigation in latent spaces.",
    4: "Build an itinerary summary for the Japan vacation.",
}


def generate_tasks(
    graph: Graph,
    n_tasks: int,
    facts_min: int,
    facts_max: int,
    n_subq: int,
    encoder,
    seed: int,
    hard_coref_probability: float = 0.4,
) -> list[Task]:
    rng = np.random.default_rng(seed + 1)

    goal_texts = [GOAL_TEMPLATES[t] for t in range(len(TOPICS))]
    goal_embeddings = encoder.encode(goal_texts, convert_to_numpy=True, show_progress_bar=False)

    tasks: list[Task] = []
    for task_id in range(n_tasks):
        topic_id = task_id % len(TOPICS)
        topic_facts = FACTS[topic_id]
        fact_keys = list(topic_facts.keys())

        n_facts = int(rng.integers(facts_min, facts_max + 1))
        n_facts = min(n_facts, len(fact_keys))
        sampled_keys = [str(k) for k in rng.choice(fact_keys, size=n_facts, replace=False)]

        gold_path = [
            n.id
            for n in graph.nodes
            if n.topic_id == topic_id and any(k in n.facts for k in sampled_keys)
        ]

        chunks: list[list[str]] = [[] for _ in range(n_subq)]
        for i, k in enumerate(sampled_keys):
            chunks[i % n_subq].append(k)
        chunks = [c for c in chunks if c]

        subquestions = []
        for chunk_idx, chunk in enumerate(chunks):
            if chunk_idx == 0:
                phrasing = 0  # explicit topic
            else:
                # Soft coref keeps the fact keyword; hard coref is purely anaphoric.
                phrasing = 2 if rng.random() < hard_coref_probability else 1
            parts = [SUBQUESTION_TEMPLATES[topic_id][k][phrasing] for k in chunk]
            subquestions.append(" ".join(parts))

        facts_to_find = {k: topic_facts[k] for k in sampled_keys}

        tasks.append(
            Task(
                id=task_id,
                topic_id=topic_id,
                goal_text=GOAL_TEMPLATES[topic_id],
                goal_embedding=goal_embeddings[topic_id],
                gold_path=gold_path,
                facts_to_find=facts_to_find,
                subquestions=subquestions,
                subquestion_fact_keys=chunks,
            )
        )

    return tasks


def generate_shift_tasks(
    graph: Graph,
    n_tasks: int,
    encoder,
    seed: int,
    facts_per_half: int = 2,
) -> list[Task]:
    """Generate topic-shift tasks.

    Structure (4 subqs per task):
      Q1 — explicit topic A (one A-fact)
      Q2 — soft coref A    (another A-fact, "the X is...")
      Q3 — explicit topic B (one B-fact)   ← the pivot turn
      Q4 — hard coref B    (another B-fact, "And when?" with no topic word)

    The key test is Q4: an agent that's frozen on Q1 (topic A) will misroute
    retrieval. An agent with a slow-EMA anchor that has updated through Q3
    should now point toward B and retrieve correctly.
    """
    rng = np.random.default_rng(seed + 11)
    tasks: list[Task] = []
    n_topics_total = len(TOPICS)

    for task_id in range(n_tasks):
        topic_a = task_id % n_topics_total
        topic_b = (topic_a + 2) % n_topics_total
        if topic_b == topic_a:
            topic_b = (topic_a + 1) % n_topics_total

        facts_a_dict = FACTS[topic_a]
        facts_b_dict = FACTS[topic_b]
        keys_a = [
            str(k)
            for k in rng.choice(list(facts_a_dict.keys()), size=facts_per_half, replace=False)
        ]
        keys_b = [
            str(k)
            for k in rng.choice(list(facts_b_dict.keys()), size=facts_per_half, replace=False)
        ]

        # Q1: explicit A; Q2: soft coref A; Q3: explicit B (pivot); Q4: hard coref B
        q1 = SUBQUESTION_TEMPLATES[topic_a][keys_a[0]][0]
        q2 = SUBQUESTION_TEMPLATES[topic_a][keys_a[1]][1]
        q3 = SUBQUESTION_TEMPLATES[topic_b][keys_b[0]][0]
        q4 = SUBQUESTION_TEMPLATES[topic_b][keys_b[1]][2]
        subquestions = [q1, q2, q3, q4]

        # Gold path: all fact nodes referenced (both topics)
        gold_a = [
            n.id
            for n in graph.nodes
            if n.topic_id == topic_a and any(k in n.facts for k in keys_a)
        ]
        gold_b = [
            n.id
            for n in graph.nodes
            if n.topic_id == topic_b and any(k in n.facts for k in keys_b)
        ]
        gold_path = gold_a + gold_b

        # Disambiguated fact keys so the judge sees topic context
        facts_to_find: dict[str, str] = {}
        for k in keys_a:
            facts_to_find[f"{TOPICS[topic_a]} — {k}"] = facts_a_dict[k]
        for k in keys_b:
            facts_to_find[f"{TOPICS[topic_b]} — {k}"] = facts_b_dict[k]

        goal_text = (
            f"Gather information about {TOPICS[topic_a]}, then about {TOPICS[topic_b]}."
        )
        goal_embedding = encoder.encode(
            goal_text, convert_to_numpy=True, show_progress_bar=False
        )

        sq_fact_keys = [
            [f"{TOPICS[topic_a]} — {keys_a[0]}"],
            [f"{TOPICS[topic_a]} — {keys_a[1]}"],
            [f"{TOPICS[topic_b]} — {keys_b[0]}"],
            [f"{TOPICS[topic_b]} — {keys_b[1]}"],
        ]

        tasks.append(
            Task(
                id=task_id,
                topic_id=topic_a,  # primary topic; topic_b implicit in gold/facts
                goal_text=goal_text,
                goal_embedding=goal_embedding,
                gold_path=gold_path,
                facts_to_find=facts_to_find,
                subquestions=subquestions,
                subquestion_fact_keys=sq_fact_keys,
            )
        )

    return tasks
