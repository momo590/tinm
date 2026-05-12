from dataclasses import dataclass, field


@dataclass
class AgentResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    nodes_visited: list[int] = field(default_factory=list)


@dataclass
class Turn:
    """One past turn in the conversation, available to agents as `history`."""
    query: str
    response: str
    is_distractor: bool = False
