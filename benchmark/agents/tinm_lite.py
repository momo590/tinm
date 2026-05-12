"""TINM-lite: turtle-inspired navigation memory, minimum-viable version.

Three mechanisms only:
  1. Imprinting — Q1's query embedding is captured as the initial anchor.
  2. Slow EMA   — anchor ← 0.85·anchor + 0.15·new_query. The anchor resists
                  fast perturbation from distractors and remembers what the
                  user's task is actually about.
  3. Lean prompt — NO chat history in the LLM call. The memory is the agent's
                   internal state (a single fixed-size vector), not external
                   context bloat.

Retrieval uses a blended embedding: w_query·current_query + w_anchor·anchor.
The current query brings fresh fact-keywords; the anchor brings persistent
topic signal that survives distractors and anaphoric Q2s.

This is intentionally far simpler than the full substrate doc — no SR, no NPF
heads, no friction detection, no resolution hierarchy. Those come later if v1
shows promising signal.
"""
from __future__ import annotations

import anthropic
import numpy as np

from .base import AgentResponse


TINM_SYSTEM_PROMPT = (
    "You are a precise research assistant. "
    "Answer the user's question using only the provided context. "
    "If a specific fact is asked for, state it directly and concisely. "
    "If the answer is not in the context, say so explicitly."
)


class TINMLiteAgent:
    name = "tinm_lite"

    def __init__(
        self,
        nodes,
        top_k: int = 5,
        w_query: float = 0.5,
        w_anchor: float = 0.5,
        anchor_alpha: float = 0.85,
        adaptive_alpha: bool = True,
        alpha_min: float = 0.20,
        alpha_max: float = 0.95,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 512,
    ):
        """
        w_query, w_anchor : retrieval blend between current query and topic anchor.
        anchor_alpha      : fixed EMA carry-over rate (used if adaptive_alpha=False).
        adaptive_alpha    : if True, alpha is computed per turn from
                            sim(query, anchor). High consistency → high alpha
                            (preserve, resist distractors). Low consistency
                            → low alpha (adapt fast, handle topic shifts).
        alpha_min, alpha_max : bounds for the adaptive alpha schedule.
        """
        self.nodes = nodes
        self.top_k = top_k
        self.w_query = w_query
        self.w_anchor = w_anchor
        self.anchor_alpha = anchor_alpha
        self.adaptive_alpha = adaptive_alpha
        self.alpha_min = alpha_min
        self.alpha_max = alpha_max
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic()

        embeddings = np.stack([n.embedding for n in nodes])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        self.embeddings = embeddings
        self.embeddings_norm = embeddings / (norms + 1e-9)

        # Per-task state (cleared in reset())
        self.query_anchor: np.ndarray | None = None
        self.prior_queries: list[str] = []
        self.turn: int = 0

    def reset(self, task) -> None:
        self.query_anchor = None
        self.prior_queries = []
        self.turn = 0

    def _compute_alpha(self, query_embedding: np.ndarray) -> float:
        """Decide the EMA carry-over rate for this turn.

        Adaptive mode: high consistency with current anchor (same topic)
        → high alpha (slow update, preserve). Low consistency (topic shift)
        → low alpha (fast update, adapt).
        """
        if not self.adaptive_alpha or self.query_anchor is None:
            return self.anchor_alpha
        q_n = query_embedding / (np.linalg.norm(query_embedding) + 1e-9)
        a_n = self.query_anchor / (np.linalg.norm(self.query_anchor) + 1e-9)
        consistency = max(0.0, min(1.0, float(np.dot(q_n, a_n))))
        return self.alpha_min + (self.alpha_max - self.alpha_min) * consistency

    def respond(self, query: str, query_embedding: np.ndarray, history=None) -> AgentResponse:
        """`history` accepted for interface compatibility but IGNORED — TINM's
        memory is the internal `query_anchor`, not external chat history.
        """
        self.turn += 1

        # Blend the current query with the topic anchor for retrieval.
        # On Q1 there is no anchor yet, so we fall back to plain query retrieval.
        if self.query_anchor is not None:
            retrieval_emb = (
                self.w_query * query_embedding
                + self.w_anchor * self.query_anchor
            )
        else:
            retrieval_emb = query_embedding

        re_norm = retrieval_emb / (np.linalg.norm(retrieval_emb) + 1e-9)
        scores = self.embeddings_norm @ re_norm
        top_indices = np.argsort(scores)[::-1][: self.top_k]
        retrieved = [self.nodes[int(i)] for i in top_indices]

        # Update anchor: EMA on the raw query embedding.
        # In adaptive mode, alpha is high (preserve) when the query is
        # consistent with the anchor, and low (adapt fast) when the query
        # signals a topic shift.
        alpha_t = self._compute_alpha(query_embedding)
        if self.query_anchor is None:
            self.query_anchor = query_embedding.copy()
        else:
            self.query_anchor = (
                alpha_t * self.query_anchor
                + (1.0 - alpha_t) * query_embedding
            )

        # Lean LLM call — no chat history. We still include a compact
        # "trajectory hint" of prior queries (without responses) so the LLM
        # can resolve anaphora in the current query. This is the textual form
        # of the latent state z_t shared with the LLM.
        context_str = "\n\n".join(f"[Node {n.id}] {n.content}" for n in retrieved)
        if self.prior_queries:
            trace = " ; ".join(f"({i + 1}) {q}" for i, q in enumerate(self.prior_queries))
            user_msg = (
                f"Prior questions in this conversation: {trace}\n\n"
                f"Context:\n{context_str}\n\n"
                f"Now answer this follow-up question: {query}"
            )
        else:
            user_msg = f"Context:\n{context_str}\n\nQuestion: {query}"

        # Remember this query for the next turn's trajectory hint
        self.prior_queries.append(query)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=TINM_SYSTEM_PROMPT,
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
