"""RAG + conversation history — the realistic baseline.

This is the standard "ChatGPT + retrieval" pattern in production:
- past (query, response) turns are passed as multi-turn messages to the LLM
- the current query is enriched with prior query text for retrieval
  (so a co-referential query like "What is the deadline?" can still anchor
  on the topic mentioned in earlier turns)
"""
from __future__ import annotations

import anthropic
import numpy as np

from .base import AgentResponse, Turn


RAG_HISTORY_SYSTEM_PROMPT = (
    "You are a precise research assistant. "
    "Answer the user's question using only the provided context. "
    "If a question refers to something implicitly (e.g. 'it', 'the project'), "
    "use the prior turns of the conversation to resolve the reference. "
    "If the answer is not in the context, say so explicitly."
)


class RAGWithHistoryAgent:
    name = "rag_with_history"

    def __init__(
        self,
        nodes,
        encoder,
        top_k: int = 5,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 512,
    ):
        self.nodes = nodes
        self.encoder = encoder
        self.top_k = top_k
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(max_retries=8)

        embeddings = np.stack([n.embedding for n in nodes])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        self.embeddings_norm = embeddings / (norms + 1e-9)

    def reset(self, task) -> None:
        # History is managed by the orchestrator and passed to respond().
        return

    def _retrieve(self, query: str, query_embedding: np.ndarray, history: list[Turn] | None):
        """Enrich the retrieval embedding with prior query text to handle co-reference."""
        if history:
            # Concatenate prior queries (excluding distractor — see note below)
            # We deliberately INCLUDE distractor queries here too: a realistic
            # production system doesn't know which past turn was a distractor.
            prior_text = " ".join(turn.query for turn in history)
            enriched = f"{prior_text} {query}"
            q = self.encoder.encode(enriched)
        else:
            q = query_embedding
        q_norm = q / (np.linalg.norm(q) + 1e-9)
        scores = self.embeddings_norm @ q_norm
        return np.argsort(scores)[::-1][: self.top_k]

    def respond(
        self,
        query: str,
        query_embedding: np.ndarray,
        history: list[Turn] | None = None,
    ) -> AgentResponse:
        top_indices = self._retrieve(query, query_embedding, history)
        retrieved = [self.nodes[int(i)] for i in top_indices]
        context_str = "\n\n".join(f"[Node {n.id}] {n.content}" for n in retrieved)

        messages = []
        if history:
            for turn in history:
                messages.append({"role": "user", "content": turn.query})
                messages.append({"role": "assistant", "content": turn.response})
        messages.append({"role": "user", "content": f"Context:\n{context_str}\n\nQuestion: {query}"})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=RAG_HISTORY_SYSTEM_PROMPT,
            messages=messages,
        )

        text = "".join(b.text for b in response.content if b.type == "text")
        return AgentResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            nodes_visited=[int(i) for i in top_indices],
        )
