from dataclasses import dataclass, field


@dataclass
class SessionMetrics:
    task_id: int
    agent_name: str
    # Task subquestions (the ones we score)
    subquestion_input_tokens: list[int] = field(default_factory=list)
    subquestion_output_tokens: list[int] = field(default_factory=list)
    subquestion_cache_read_tokens: list[int] = field(default_factory=list)
    subquestion_nodes_visited: list[list[int]] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)
    # Distractor turns (interruptions between task subqs)
    distractor_input_tokens: list[int] = field(default_factory=list)
    distractor_output_tokens: list[int] = field(default_factory=list)
    # Quality (computed on task subq responses only)
    quality_score: float = 0.0
    facts_found_symbolic: dict[str, bool] = field(default_factory=dict)
    facts_found_judge: dict[str, bool] = field(default_factory=dict)
    judge_quality: float = 0.0
    had_distractor: bool = False

    @property
    def task_input_tokens(self) -> int:
        return sum(self.subquestion_input_tokens)

    @property
    def task_output_tokens(self) -> int:
        return sum(self.subquestion_output_tokens)

    @property
    def total_input_tokens(self) -> int:
        return self.task_input_tokens + sum(self.distractor_input_tokens)

    @property
    def total_output_tokens(self) -> int:
        return self.task_output_tokens + sum(self.distractor_output_tokens)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def resumption_input_tokens(self) -> int:
        """Tokens spent on subqs >= 2 (the 'after interruption' turns)."""
        if len(self.subquestion_input_tokens) < 2:
            return 0
        return sum(self.subquestion_input_tokens[1:])

    def anchor_costs(self, gold_path: set[int]) -> list[int]:
        """Anchor cost per subquestion: number of non-gold nodes visited
        before the first gold-path hit. If no gold node was visited,
        returns the full visited count.
        """
        costs: list[int] = []
        for visited in self.subquestion_nodes_visited:
            cost = len(visited)
            for i, node_id in enumerate(visited):
                if node_id in gold_path:
                    cost = i
                    break
            costs.append(cost)
        return costs

    def gold_path_recall(self, gold_path: set[int]) -> float:
        if not gold_path:
            return 1.0
        all_visited: set[int] = set()
        for visited in self.subquestion_nodes_visited:
            all_visited.update(visited)
        hits = len(all_visited & gold_path)
        return hits / len(gold_path)
