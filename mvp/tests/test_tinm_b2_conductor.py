"""Tests for TINMConductorAgent — tinm_full variant with injected digest."""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# Add the repo root so `benchmark.agents.*` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Stub `anthropic` if not installed (tests mock the client anyway).
if "anthropic" not in sys.modules:
    stub = types.ModuleType("anthropic")
    stub.Anthropic = MagicMock()
    sys.modules["anthropic"] = stub


@dataclass
class FakeNode:
    id: int
    content: str
    embedding: np.ndarray


def _make_nodes(n: int = 6, dim: int = 8) -> list[FakeNode]:
    rng = np.random.default_rng(42)
    return [
        FakeNode(id=i, content=f"node-{i} content", embedding=rng.standard_normal(dim))
        for i in range(n)
    ]


def _fake_response(text: str = "ok"):
    resp = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp.content = [block]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    resp.usage.cache_read_input_tokens = 0
    return resp


def _capture_user_msg(agent, query: str) -> str:
    """Run one respond() turn and return the user_msg passed to the LLM."""
    emb = agent.nodes[0].embedding.copy()
    agent.client = MagicMock()
    agent.client.messages.create.return_value = _fake_response()
    agent.respond(query, emb)
    call = agent.client.messages.create.call_args
    return call.kwargs["messages"][0]["content"]


def test_inherits_from_tinm_full():
    from benchmark.agents.tinm_b2_conductor import TINMConductorAgent
    from benchmark.agents.tinm_full import TINMFullAgent

    assert issubclass(TINMConductorAgent, TINMFullAgent)


def test_no_digest_behaves_like_tinm_full():
    from benchmark.agents.tinm_b2_conductor import TINMConductorAgent
    from benchmark.agents.tinm_full import TINMFullAgent

    nodes = _make_nodes()
    full = TINMFullAgent(nodes=nodes)
    cond = TINMConductorAgent(nodes=nodes, digest_text="")

    full_msg = _capture_user_msg(full, "What is X?")
    cond_msg = _capture_user_msg(cond, "What is X?")
    assert full_msg == cond_msg


def test_digest_prepended_to_context():
    from benchmark.agents.tinm_b2_conductor import TINMConductorAgent

    digest = "Prior session: user asked about X, answer was Y."
    nodes = _make_nodes()
    cond = TINMConductorAgent(nodes=nodes, digest_text=digest)

    msg = _capture_user_msg(cond, "What is X?")
    assert digest in msg
    assert "Context:" in msg
    assert msg.index(digest) < msg.index("Context:")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
