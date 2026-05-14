"""Tests for tinm_scoring — the extracted artifact scoring module.

These tests exercise the pure helpers (`_substring_score`, `_cosine`) and
the two-stage `rank_artifacts` pipeline directly, with no filesystem,
thread file, or sentence-transformers model loading required.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add the skill directory to sys.path so tinm_scoring imports correctly.
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

import tinm_scoring  # noqa: E402
from tinm_scoring import (  # noqa: E402
    _cosine,
    _substring_score,
    rank_artifacts,
    score_artifact,
)


# ---------------------------------------------------------------------------
# _substring_score
# ---------------------------------------------------------------------------


class TestSubstringScore:
    def test_query_is_substring_of_candidate(self):
        """Query 'auth' inside candidate 'authentication' scores 1.0."""
        assert _substring_score("auth", ["authentication"]) == 1.0

    def test_candidate_is_substring_of_query(self):
        """Candidate 'JWT' inside query 'how does JWT refresh work' scores 1.0."""
        assert _substring_score("how does JWT refresh work", ["JWT"]) == 1.0

    def test_disjoint_strings(self):
        """No overlap → 0.0."""
        assert _substring_score("oauth", ["middleware"]) == 0.0

    def test_empty_inputs(self):
        """Empty query or empty/whitespace candidates → 0.0."""
        assert _substring_score("", ["foo"]) == 0.0
        assert _substring_score("foo", []) == 0.0
        assert _substring_score("foo", [""]) == 0.0
        assert _substring_score("foo", ["   "]) == 0.0

    def test_case_insensitive_and_whitespace_normalised(self):
        """Mixed case + extra whitespace still matches."""
        assert _substring_score("  AUTH  Middleware  ", ["auth middleware"]) == 1.0


# ---------------------------------------------------------------------------
# _cosine
# ---------------------------------------------------------------------------


class TestCosine:
    def test_orthogonal_vectors(self):
        """Perpendicular vectors → ~0.0."""
        assert abs(_cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])) < 1e-6

    def test_identical_vectors(self):
        """Identical vectors → ~1.0."""
        assert abs(_cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-6

    def test_anti_parallel_vectors(self):
        """Anti-parallel vectors → ~-1.0."""
        assert abs(_cosine([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]) + 1.0) < 1e-6


# ---------------------------------------------------------------------------
# rank_artifacts — substring stage short-circuits embedding stage
# ---------------------------------------------------------------------------


class TestRankArtifactsSubstring:
    def test_substring_hits_short_circuit_embedding(self, monkeypatch):
        """When substring matches exist, _encode must NOT be called."""
        encode_calls: list[str] = []

        def fake_encode(text: str):
            encode_calls.append(text)
            return [0.0] * 384

        monkeypatch.setattr(tinm_scoring, "_encode", fake_encode)

        arts = [
            {"name": "auth middleware", "aliases": [], "embedding": [1.0, 0.0, 0.0]},
            {"name": "billing service", "aliases": [], "embedding": [0.0, 1.0, 0.0]},
        ]
        result = rank_artifacts(arts, "auth", k=3)

        assert encode_calls == [], "embedding stage should not run when substring hits exist"
        assert len(result) == 1
        assert result[0]["name"] == "auth middleware"
        assert result[0]["match_kind"] == "substring"
        assert result[0]["score"] == 1.0


# ---------------------------------------------------------------------------
# rank_artifacts — embedding fallback when no substring hits
# ---------------------------------------------------------------------------


class TestRankArtifactsEmbedding:
    def test_embedding_fallback_when_no_substring(self, monkeypatch):
        """No substring hit → embedding stage runs and yields match_kind='embedding'."""
        # Fake _encode returns a fixed unit vector aligned with the first artifact.
        monkeypatch.setattr(tinm_scoring, "_encode", lambda text: [1.0, 0.0, 0.0])

        arts = [
            {"name": "alpha", "aliases": [], "embedding": [1.0, 0.0, 0.0]},  # cosine ~1
            {"name": "beta", "aliases": [], "embedding": [0.0, 1.0, 0.0]},   # cosine ~0
        ]
        result = rank_artifacts(arts, "completely-unrelated-query", k=2)

        assert len(result) == 2
        assert all(r["match_kind"] == "embedding" for r in result)
        # alpha should rank above beta because its embedding aligns with the query vec.
        assert result[0]["name"] == "alpha"
        assert result[0]["score"] > result[1]["score"]

    def test_embedding_fallback_skips_artifacts_without_embedding(self, monkeypatch):
        """Artifacts with no embedding are excluded from the embedding stage."""
        monkeypatch.setattr(tinm_scoring, "_encode", lambda text: [1.0, 0.0, 0.0])

        arts = [
            {"name": "no-emb", "aliases": [], "embedding": None},
            {"name": "with-emb", "aliases": [], "embedding": [1.0, 0.0, 0.0]},
        ]
        result = rank_artifacts(arts, "unrelated", k=5)

        assert [r["name"] for r in result] == ["with-emb"]

    def test_embedding_stage_returns_empty_when_no_embeddings(self):
        """No substring hit + no artifact has an embedding → empty list."""
        arts = [
            {"name": "foo", "aliases": [], "embedding": None},
            {"name": "bar", "aliases": [], "embedding": None},
        ]
        result = rank_artifacts(arts, "unrelated", k=3)
        assert result == []


# ---------------------------------------------------------------------------
# rank_artifacts — k slicing
# ---------------------------------------------------------------------------


class TestRankArtifactsKSlicing:
    def test_k_truncates_results(self):
        """When more matches than k, result is sliced to k items."""
        arts = [
            {"name": "auth-a", "aliases": [], "embedding": None},
            {"name": "auth-b", "aliases": [], "embedding": None},
            {"name": "auth-c", "aliases": [], "embedding": None},
        ]
        result = rank_artifacts(arts, "auth", k=2)
        assert len(result) == 2

    def test_k_larger_than_matches(self):
        """k larger than the number of matches → all matches returned."""
        arts = [
            {"name": "auth-a", "aliases": [], "embedding": None},
            {"name": "auth-b", "aliases": [], "embedding": None},
        ]
        result = rank_artifacts(arts, "auth", k=10)
        assert len(result) == 2

    def test_empty_artifacts_list(self):
        """Empty artifact list → empty result."""
        assert rank_artifacts([], "anything", k=3) == []


# ---------------------------------------------------------------------------
# score_artifact (single-artifact helper for digest_generator)
# ---------------------------------------------------------------------------


class TestScoreArtifact:
    def test_substring_hit_returns_substring_kind(self):
        """A substring match returns (1.0, 'substring')."""
        art = {"name": "auth middleware", "aliases": ["JWT"], "embedding": [1.0, 0.0]}
        score, kind = score_artifact(art, "auth")
        assert score == 1.0
        assert kind == "substring"

    def test_alias_substring_hit(self):
        """A match against an alias counts as a substring hit."""
        art = {"name": "Service X", "aliases": ["billing"], "embedding": None}
        score, kind = score_artifact(art, "billing")
        assert score == 1.0
        assert kind == "substring"

    def test_no_substring_no_embedding(self):
        """No substring + no embedding → (0.0, 'embedding')."""
        art = {"name": "foo", "aliases": [], "embedding": None}
        score, kind = score_artifact(art, "unrelated")
        assert score == 0.0
        assert kind == "embedding"

    def test_embedding_fallback(self, monkeypatch):
        """No substring + embedding present → cosine score with kind='embedding'."""
        monkeypatch.setattr(tinm_scoring, "_encode", lambda text: [1.0, 0.0, 0.0])
        art = {"name": "foo", "aliases": [], "embedding": [1.0, 0.0, 0.0]}
        score, kind = score_artifact(art, "unrelated")
        assert kind == "embedding"
        assert abs(score - 1.0) < 1e-6
