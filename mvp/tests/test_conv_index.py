"""Tests for tinm_conv_index.py"""
import json
import math
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

import tinm_conv_index  # noqa: E402


def make_fake_embed(text: str) -> list[float]:
    """Return a deterministic unit-normalised fake embedding for testing.

    Uses an MD5-seeded sine series so each input produces a distinct vector,
    avoiding degenerate cases (e.g., all-zero or identical embeddings) that
    would mask bugs in cosine-ranking logic.
    """
    h = int(hashlib.md5(text.encode()).hexdigest(), 16)
    vec = [math.sin(h + i) for i in range(384)]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


@pytest.fixture(autouse=True)
def tmp_buffer(tmp_path, monkeypatch):
    """Redirect all index I/O to a per-test temp directory."""
    monkeypatch.setattr(tinm_conv_index, "BUFFER_DIR", tmp_path)


# ---------------------------------------------------------------------------
# add_turn + find — basic round-trip
# ---------------------------------------------------------------------------


def test_add_and_find(monkeypatch):
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    tinm_conv_index.add_turn("sess1", 1, "user", "How do I fix the auth bug?")
    tinm_conv_index.add_turn("sess1", 2, "assistant", "The auth bug is in auth.ts line 47")
    results = tinm_conv_index.find("sess1", "authentication bug fix", k=2)

    assert len(results) == 2
    assert all(r["source"] == "conv_index" for r in results)
    assert all("turn_id" in r for r in results)
    assert all("role" in r for r in results)
    assert all("text_preview" in r for r in results)
    assert all("score" in r for r in results)


def test_find_empty_session(monkeypatch):
    """find() on a session with no index file returns empty list."""
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    results = tinm_conv_index.find("nonexistent-session", "anything", k=3)
    assert results == []


def test_results_sorted_by_score_descending(monkeypatch):
    """find() results must be ordered by score descending."""
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    for i in range(5):
        tinm_conv_index.add_turn("sess_sorted", i, "user", f"message number {i} about topic {i}")
    results = tinm_conv_index.find("sess_sorted", "specific topic", k=5)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True), "Results must be sorted by score descending"


# ---------------------------------------------------------------------------
# text_preview truncation
# ---------------------------------------------------------------------------


def test_text_preview_truncation(monkeypatch):
    """text_preview stored in the index is capped at 200 chars."""
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    long_text = "x" * 500
    tinm_conv_index.add_turn("sess2", 1, "user", long_text)
    path = tinm_conv_index._index_path("sess2")
    entry = json.loads(path.read_text().strip())
    assert len(entry["text_preview"]) == 200


def test_text_preview_short_text_preserved(monkeypatch):
    """text_preview for text shorter than 200 chars is stored verbatim."""
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    short_text = "short message"
    tinm_conv_index.add_turn("sess_short", 1, "user", short_text)
    path = tinm_conv_index._index_path("sess_short")
    entry = json.loads(path.read_text().strip())
    assert entry["text_preview"] == short_text


# ---------------------------------------------------------------------------
# MAX_ENTRIES cap
# ---------------------------------------------------------------------------


def test_max_cap_enforced(monkeypatch):
    """After 210 turns, only the most recent 200 are retained."""
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    for i in range(210):
        tinm_conv_index.add_turn("sess3", i, "user", f"turn {i} content unique phrase")

    entries = tinm_conv_index._load(tinm_conv_index._index_path("sess3"))
    assert len(entries) == 200
    # The oldest 10 entries (turn_id 0–9) must have been discarded
    assert entries[0]["turn_id"] == 10


def test_cap_exactly_at_limit(monkeypatch):
    """Exactly MAX_ENTRIES turns: no entries are discarded."""
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    for i in range(tinm_conv_index.MAX_ENTRIES):
        tinm_conv_index.add_turn("sess_exact", i, "user", f"turn {i}")

    entries = tinm_conv_index._load(tinm_conv_index._index_path("sess_exact"))
    assert len(entries) == tinm_conv_index.MAX_ENTRIES
    assert entries[0]["turn_id"] == 0  # no entries discarded


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear(monkeypatch):
    """clear() removes the session's index file."""
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    tinm_conv_index.add_turn("sess4", 1, "user", "test content")
    assert tinm_conv_index._index_path("sess4").exists()
    tinm_conv_index.clear("sess4")
    assert not tinm_conv_index._index_path("sess4").exists()


def test_clear_nonexistent_session_is_noop():
    """clear() on a session with no index is a no-op (no error)."""
    tinm_conv_index.clear("session-that-does-not-exist")  # must not raise


# ---------------------------------------------------------------------------
# empty / whitespace text is skipped
# ---------------------------------------------------------------------------


def test_empty_text_skipped(monkeypatch):
    """add_turn with empty or whitespace-only text must not create an index file."""
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    tinm_conv_index.add_turn("sess5", 1, "user", "")
    tinm_conv_index.add_turn("sess5", 2, "user", "   ")
    tinm_conv_index.add_turn("sess5", 3, "user", "\n\t")
    assert not tinm_conv_index._index_path("sess5").exists()


# ---------------------------------------------------------------------------
# graceful degradation when _encode is unavailable
# ---------------------------------------------------------------------------


def test_find_returns_empty_when_encode_unavailable(monkeypatch):
    """find() must return [] if _encode raises (e.g., sentence-transformers missing)."""
    # First add some turns with working embeddings
    monkeypatch.setattr(tinm_conv_index, "_encode", make_fake_embed)
    tinm_conv_index.add_turn("sess6", 1, "user", "some text with content")

    # Now simulate _encode raising ImportError during find()
    def broken_encode(text):
        raise ImportError("sentence-transformers not installed")

    monkeypatch.setattr(tinm_conv_index, "_encode", broken_encode)
    results = tinm_conv_index.find("sess6", "some text", k=3)
    assert results == []


def test_add_turn_with_broken_encode_stores_empty_embedding(tmp_path, monkeypatch):
    """add_turn must still write the entry even if _encode fails; embedding=[].

    Entries with empty embedding are silently skipped by find(), so this
    is a graceful degradation: the turn is recorded (for future use) but
    won't appear in semantic search results.
    """
    monkeypatch.setattr(tinm_conv_index, "BUFFER_DIR", tmp_path)

    def broken_encode(text):
        raise RuntimeError("GPU oom")

    monkeypatch.setattr(tinm_conv_index, "_encode", broken_encode)
    tinm_conv_index.add_turn("sess7", 1, "user", "some content here")

    path = tinm_conv_index._index_path("sess7")
    assert path.exists()
    entry = json.loads(path.read_text().strip())
    assert entry["embedding"] == []
    assert entry["text_preview"] == "some content here"


# ---------------------------------------------------------------------------
# _load resilience — malformed lines
# ---------------------------------------------------------------------------


def test_load_skips_malformed_lines(tmp_path, monkeypatch):
    """_load() must silently skip JSON-corrupt lines without raising."""
    monkeypatch.setattr(tinm_conv_index, "BUFFER_DIR", tmp_path)
    path = tinm_conv_index._index_path("sess_corrupt")
    path.write_text(
        '{"turn_id": 1, "role": "user", "text_preview": "good", "embedding": []}\n'
        'THIS IS NOT JSON\n'
        '{"turn_id": 2, "role": "user", "text_preview": "also good", "embedding": []}\n'
    )
    entries = tinm_conv_index._load(path)
    assert len(entries) == 2
    assert entries[0]["turn_id"] == 1
    assert entries[1]["turn_id"] == 2


# ---------------------------------------------------------------------------
# index_path naming
# ---------------------------------------------------------------------------


def test_index_path_naming():
    """Index path must embed the session_id so sessions are isolated."""
    p = tinm_conv_index._index_path("my-session-abc")
    assert "my-session-abc" in p.name
    assert p.suffix == ".jsonl"
