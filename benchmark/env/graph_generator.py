from dataclasses import dataclass

import numpy as np


TOPICS = {
    0: "Project Alpha",
    1: "Project Beta",
    2: "Personal Finance",
    3: "ML Research",
    4: "Japan Vacation",
}

FACTS: dict[int, dict[str, str]] = {
    0: {
        "deadline": "2026-06-15",
        "lead": "Marie Dupont",
        "budget": "120000 EUR",
        "status": "in progress, on track",
        "tech_stack": "Python and FastAPI",
    },
    1: {
        "deadline": "2026-08-30",
        "client": "Acme Corp",
        "team_size": "7 engineers",
        "status": "design phase, awaiting approval",
        "language": "TypeScript",
    },
    2: {
        "bank": "Credit Mutuel",
        "advisor": "Jean Martin",
        "monthly_savings": "1200 EUR",
        "retirement_age": "62",
        "broker": "DeGiro",
    },
    3: {
        "paper_title": "Navigation in Latent Spaces",
        "first_author": "Tolman",
        "conference": "NeurIPS 2024",
        "year": "2024",
        "key_insight": "successor representations enable transfer",
    },
    4: {
        "destination": "Kyoto and Tokyo",
        "hotel": "Ryokan Sakura",
        "departure_date": "2026-04-12",
        "return_date": "2026-04-26",
        "travel_companion": "Sophie",
    },
}


# Multiple phrasings per fact, so morphological variants ("lead"/"leading",
# "save"/"savings", "tech stack"/"technologies") are all present in the
# content — eliminates the TF-IDF mismatch artifact.
FACT_VERBALIZATIONS: dict[int, dict[str, list[str]]] = {
    0: {
        "deadline":   ["The deadline is {value}.", "The project must be delivered by {value}.", "Due date: {value}."],
        "lead":       ["The lead is {value}.", "{value} is leading the project.", "{value} heads the team."],
        "budget":     ["The budget is {value}.", "We have {value} allocated.", "Total funding: {value}."],
        "status":     ["The status is {value}.", "Currently: {value}.", "Project state: {value}."],
        "tech_stack": ["The tech stack is {value}.", "Built with {value}.", "The technologies used are {value}."],
    },
    1: {
        "deadline":  ["The deadline is {value}.", "Must be delivered by {value}.", "Due date: {value}."],
        "client":    ["The client is {value}.", "We are working for {value}.", "Our customer is {value}."],
        "team_size": ["The team size is {value}.", "The team has {value}.", "There are {value} working on this."],
        "status":    ["The status is {value}.", "Currently: {value}.", "Project state: {value}."],
        "language":  ["The language is {value}.", "Written in {value}.", "The codebase uses {value}."],
    },
    2: {
        "bank":            ["The bank is {value}.", "I bank with {value}.", "My account is at {value}."],
        "advisor":         ["The advisor is {value}.", "My advisor is {value}.", "{value} advises me on finances."],
        "monthly_savings": ["Monthly savings: {value}.", "I save {value} each month.", "I put aside {value} monthly."],
        "retirement_age":  ["Retirement age: {value}.", "I plan to retire at {value}.", "Retiring at age {value}."],
        "broker":          ["The broker is {value}.", "I use {value} for trading.", "My investments go through {value}."],
    },
    3: {
        "paper_title":  ["The paper title is {value}.", "The paper is called {value}.", "Titled: {value}."],
        "first_author": ["The first author is {value}.", "{value} is the first author.", "By {value} et al."],
        "conference":   ["Presented at {value}.", "The conference is {value}.", "Appeared in {value}."],
        "year":         ["Published in {value}.", "The year is {value}.", "From {value}."],
        "key_insight":  ["Key insight: {value}.", "The main finding is {value}.", "{value} is the central claim."],
    },
    4: {
        "destination":      ["The destination is {value}.", "We are visiting {value}.", "Going to {value}."],
        "hotel":            ["Staying at {value}.", "The hotel is {value}.", "Booked {value} for accommodation."],
        "departure_date":   ["Departure date: {value}.", "We leave on {value}.", "Flying out {value}."],
        "return_date":      ["Return date: {value}.", "We come back on {value}.", "Flying home {value}."],
        "travel_companion": ["Travelling with {value}.", "{value} is coming along.", "My travel companion is {value}."],
    },
}


@dataclass
class Node:
    id: int
    topic_id: int
    content: str
    facts: dict[str, str]
    embedding: np.ndarray
    timestamp: int
    is_filler: bool


@dataclass
class Graph:
    nodes: list[Node]
    edges: list[tuple[int, int]]


def _fact_sentence(topic_id: int, fact_key: str, fact_value: str) -> str:
    topic_name = TOPICS[topic_id]
    variants = FACT_VERBALIZATIONS[topic_id][fact_key]
    sentences = [v.format(value=fact_value) for v in variants]
    return f"Note about {topic_name}. " + " ".join(sentences) + " Confirmed in the latest review."


def _filler_sentence(topic_name: str, filler_idx: int) -> str:
    return (
        f"Routine update on {topic_name}, item {filler_idx}. "
        f"General discussion, follow-ups, scheduling, "
        f"no concrete decisions or specific figures reported here."
    )


def generate_graph(
    n_nodes: int,
    n_topics: int,
    encoder,
    seed: int,
    intra_topic_edge_prob: float = 0.10,
    cross_topic_edge_prob: float = 0.005,
) -> Graph:
    """Generate a synthetic information graph.

    Each topic has a fixed set of facts. For each fact, one fact-bearing node
    is created; the remaining nodes per topic are filler.
    """
    rng = np.random.default_rng(seed)
    assert n_topics <= len(TOPICS), f"Only {len(TOPICS)} topics defined"
    nodes_per_topic = n_nodes // n_topics

    contents: list[str] = []
    node_meta: list[tuple[int, dict[str, str], bool]] = []

    for topic_id in range(n_topics):
        topic_name = TOPICS[topic_id]
        topic_facts = FACTS[topic_id]
        fact_keys = list(topic_facts.keys())

        for fact_key in fact_keys:
            content = _fact_sentence(topic_id, fact_key, topic_facts[fact_key])
            contents.append(content)
            node_meta.append((topic_id, {fact_key: topic_facts[fact_key]}, False))

        n_filler = nodes_per_topic - len(fact_keys)
        for filler_idx in range(n_filler):
            content = _filler_sentence(topic_name, filler_idx)
            contents.append(content)
            node_meta.append((topic_id, {}, True))

    if hasattr(encoder, "fit"):
        encoder.fit(contents)
    embeddings = encoder.encode(contents, convert_to_numpy=True, show_progress_bar=False)

    nodes: list[Node] = []
    for i, (content, (topic_id, facts, is_filler)) in enumerate(zip(contents, node_meta)):
        nodes.append(
            Node(
                id=i,
                topic_id=topic_id,
                content=content,
                facts=facts,
                embedding=embeddings[i],
                timestamp=int(rng.integers(0, 1000)),
                is_filler=is_filler,
            )
        )

    edges: list[tuple[int, int]] = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            same_topic = nodes[i].topic_id == nodes[j].topic_id
            p = intra_topic_edge_prob if same_topic else cross_topic_edge_prob
            if rng.random() < p:
                edges.append((nodes[i].id, nodes[j].id))

    return Graph(nodes=nodes, edges=edges)
