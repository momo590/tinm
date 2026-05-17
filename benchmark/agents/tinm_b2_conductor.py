"""Variant of tinm_full with a pre-computed digest injected into the prompt.

Measures the marginal contribution of the digest layer on correctness and
hallucination. The digest is passed via the constructor (production-time:
read from a cache pre-computed by `_digest_worker`).
"""
from __future__ import annotations

import numpy as np

from .base import AgentResponse
from .tinm_full import TINM_SYSTEM_PROMPT, TINMFullAgent


class TINMConductorAgent(TINMFullAgent):
    name = "tinm_b2_conductor"

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
        digest_text: str = "",
    ):
        super().__init__(
            nodes=nodes,
            top_k=top_k,
            w_query=w_query,
            w_magnetic=w_magnetic,
            w_courant=w_courant,
            w_repulsion=w_repulsion,
            alpha_magnetic=alpha_magnetic,
            alpha_courant=alpha_courant,
            repulsion_decay=repulsion_decay,
            model=model,
            max_tokens=max_tokens,
        )
        self.digest_text = digest_text

    def respond(self, query: str, query_embedding: np.ndarray, history=None) -> AgentResponse:
        self.turn += 1

        q_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-9)
        sim_query = self.embeddings_norm @ q_norm
        sim_magnetic = self._sim_to_anchor(self.magnetic_anchor)
        sim_courant = self._sim_to_anchor(self.courant_anchor)

        repulsion = np.zeros(len(self.nodes))
        for node_id, last_turn in self.visited.items():
            age = self.turn - last_turn
            if age <= self.repulsion_decay:
                repulsion[node_id] = float(np.exp(-age))

        score = (
            self.w_query * sim_query
            + self.w_magnetic * sim_magnetic
            + self.w_courant * sim_courant
            - self.w_repulsion * repulsion
        )

        top_indices = np.argsort(score)[::-1][: self.top_k]
        retrieved = [self.nodes[int(i)] for i in top_indices]

        if self.magnetic_anchor is None:
            self.magnetic_anchor = query_embedding.copy()
        else:
            self.magnetic_anchor = (
                self.alpha_magnetic * self.magnetic_anchor
                + (1.0 - self.alpha_magnetic) * query_embedding
            )

        retrieved_centroid = np.mean(np.stack([n.embedding for n in retrieved]), axis=0)
        if self.courant_anchor is None:
            self.courant_anchor = retrieved_centroid.copy()
        else:
            self.courant_anchor = (
                self.alpha_courant * self.courant_anchor
                + (1.0 - self.alpha_courant) * retrieved_centroid
            )

        for nid in top_indices:
            self.visited[int(nid)] = self.turn

        context_str = "\n\n".join(f"[Node {n.id}] {n.content}" for n in retrieved)
        digest_prefix = (
            f"[Digest from prior session: {self.digest_text}]\n\n"
            if self.digest_text
            else ""
        )
        if self.prior_queries:
            trace = " ; ".join(f"({i + 1}) {q}" for i, q in enumerate(self.prior_queries))
            user_msg = (
                f"{digest_prefix}"
                f"Prior questions in this conversation: {trace}\n\n"
                f"Context:\n{context_str}\n\n"
                f"Now answer this follow-up question: {query}"
            )
        else:
            user_msg = (
                f"{digest_prefix}"
                f"Context:\n{context_str}\n\nQuestion: {query}"
            )

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
