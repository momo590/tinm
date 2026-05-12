from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Graph
    n_nodes: int = 200
    n_topics: int = 5
    embedding_model: str = "all-MiniLM-L6-v2"
    intra_topic_edge_prob: float = 0.10
    cross_topic_edge_prob: float = 0.005

    # Tasks
    n_tasks: int = 50
    facts_per_task_min: int = 3
    facts_per_task_max: int = 4
    n_subquestions: int = 2
    distractor_probability: float = 0.5  # P(inject distractor between Q1 and Q2)
    hard_coref_probability: float = 0.4  # P(Q2 uses purely anaphoric phrasing)

    # Pipeline
    rag_top_k: int = 5
    seed: int = 42

    # LLM
    llm_model: str = "claude-sonnet-4-6"
    judge_model: str = "claude-sonnet-4-6"
    max_tokens: int = 512
