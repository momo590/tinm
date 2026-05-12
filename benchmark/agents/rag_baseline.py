import anthropic
import numpy as np

from .base import AgentResponse


RAG_SYSTEM_PROMPT = (
    "You are a precise research assistant. "
    "Answer the user's question using only the provided context. "
    "If a specific fact is asked for, state it directly and concisely. "
    "If the answer is not in the context, say so explicitly."
)


class RAGBaselineAgent:
    """Naive RAG: cosine similarity top-k retrieval, full context injection.

    No memory across queries — each subquestion is answered independently.
    This is the lower bound: any benefit of continuity must come from
    something beyond this baseline.
    """

    name = "rag_baseline"

    def __init__(self, nodes, top_k: int = 5, model: str = "claude-sonnet-4-6", max_tokens: int = 512):
        self.nodes = nodes
        self.top_k = top_k
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(max_retries=8)

        embeddings = np.stack([n.embedding for n in nodes])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        self.embeddings_norm = embeddings / (norms + 1e-9)

    def reset(self, task) -> None:
        # Naive RAG: stateless across subquestions.
        return

    def respond(
        self,
        query: str,
        query_embedding: np.ndarray,
        history=None,  # ignored — this baseline is intentionally stateless
    ) -> AgentResponse:
        q_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-9)
        scores = self.embeddings_norm @ q_norm
        top_indices = np.argsort(scores)[::-1][: self.top_k]

        retrieved = [self.nodes[int(i)] for i in top_indices]
        context_str = "\n\n".join(f"[Node {n.id}] {n.content}" for n in retrieved)
        user_msg = f"Context:\n{context_str}\n\nQuestion: {query}"

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=RAG_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        text = "".join(b.text for b in response.content if b.type == "text")
        return AgentResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            nodes_visited=[int(i) for i in top_indices],
        )
