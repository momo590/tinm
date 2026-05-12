"""TINM-full: dual-scale memory with NPF-style composite scoring.

Extends TINM-lite with:
  - DUAL anchors at different update rates:
      * magnetic_anchor (alpha=0.92, very slow) — long-term topic prior,
        equivalent to "imprinting" from the substrate doc. Wins on chained
        reasoning where coherent domain spans surface query divergence.
      * courant_anchor (alpha=0.50, medium EMA on retrieved centroids) — recent
        trajectory tracker. Follows the agent's path through the information
        space; useful when topic genuinely drifts.
  - NPF composite score:
        score(v) = w_q · sim(query, v)
                 + w_m · sim(magnetic, v)
                 + w_c · sim(courant, v)
                 - w_r · repulsion(v)
    where repulsion penalizes recently visited nodes (encourages exploration).
  - Trajectory hint in LLM prompt (same as lite v2).

The key empirical claim: a single scalar anchor with adaptive alpha (lite v2)
cannot simultaneously handle (a) chained reasoning where Q1's anchor should
stay, and (b) topic shifts where the anchor must adapt. Two anchors at fixed
but distinct rates resolve this without dynamic alpha gymnastics.
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


class TINMFullAgent:
    name = "tinm_full"

    def __init__(
        self,
        nodes,
        top_k: int = 5,
        w_query: float = 0.40,
        w_magnetic: float = 0.25,
        w_courant: float = 0.25,
        w_repulsion: float = 0.10,
        alpha_magnetic: float = 0.92,
        alpha_courant: float = 0.50,
        repulsion_decay: int = 2,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 512,
    ):
        self.nodes = nodes
        self.top_k = top_k
        self.w_query = w_query
        self.w_magnetic = w_magnetic
        self.w_courant = w_courant
        self.w_repulsion = w_repulsion
        self.alpha_magnetic = alpha_magnetic
        self.alpha_courant = alpha_courant
        self.repulsion_decay = repulsion_decay
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic()

        embeddings = np.stack([n.embedding for n in nodes])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        self.embeddings = embeddings
        self.embeddings_norm = embeddings / (norms + 1e-9)

        # Per-task state
        self.magnetic_anchor: np.ndarray | None = None
        self.courant_anchor: np.ndarray | None = None
        self.visited: dict[int, int] = {}  # node_id -> turn_visited
        self.prior_queries: list[str] = []
        self.turn: int = 0

    def reset(self, task) -> None:
        self.magnetic_anchor = None
        self.courant_anchor = None
        self.visited = {}
        self.prior_queries = []
        self.turn = 0

    def _sim_to_anchor(self, anchor: np.ndarray | None) -> np.ndarray:
        if anchor is None:
            return np.zeros(len(self.nodes))
        an = anchor / (np.linalg.norm(anchor) + 1e-9)
        return self.embeddings_norm @ an

    def respond(self, query: str, query_embedding: np.ndarray, history=None) -> AgentResponse:
        self.turn += 1

        q_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-9)
        sim_query = self.embeddings_norm @ q_norm
        sim_magnetic = self._sim_to_anchor(self.magnetic_anchor)
        sim_courant = self._sim_to_anchor(self.courant_anchor)

        # Repulsion: exponential decay penalty for recently visited nodes
        repulsion = np.zeros(len(self.nodes))
        for node_id, last_turn in self.visited.items():
            age = self.turn - last_turn
            if age <= self.repulsion_decay:
                repulsion[node_id] = float(np.exp(-age))

        # NPF composite score
        score = (
            self.w_query * sim_query
            + self.w_magnetic * sim_magnetic
            + self.w_courant * sim_courant
            - self.w_repulsion * repulsion
        )

        top_indices = np.argsort(score)[::-1][: self.top_k]
        retrieved = [self.nodes[int(i)] for i in top_indices]

        # Update magnetic anchor (slow EMA on query embedding)
        if self.magnetic_anchor is None:
            self.magnetic_anchor = query_embedding.copy()
        else:
            self.magnetic_anchor = (
                self.alpha_magnetic * self.magnetic_anchor
                + (1.0 - self.alpha_magnetic) * query_embedding
            )

        # Update courant anchor (medium EMA on retrieved centroid)
        retrieved_centroid = np.mean(np.stack([n.embedding for n in retrieved]), axis=0)
        if self.courant_anchor is None:
            self.courant_anchor = retrieved_centroid.copy()
        else:
            self.courant_anchor = (
                self.alpha_courant * self.courant_anchor
                + (1.0 - self.alpha_courant) * retrieved_centroid
            )

        # Mark visited
        for nid in top_indices:
            self.visited[int(nid)] = self.turn

        # LLM prompt with trajectory hint
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
