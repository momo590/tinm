"""Tests for TINM trajectory hint generation (--emit-hint functionality).

Tests the pure formatting functions directly — no filesystem, no model loading,
no thread files required. This validates the core hint logic that runs on every
user message.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add the skill directory to sys.path so tinm_update imports correctly.
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from tinm_update import _format_trajectory_hint, _top_query_terms  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _thread(turns: list[tuple[int, str]], *, engaged: bool = True,
            top_terms: list[str] | None = None) -> dict:
    """Build a minimal in-memory thread dict for testing."""
    return {
        "anchor": {
            "engaged_so_far": engaged,
            "top_terms": top_terms if top_terms is not None else ["auth", "middleware"],
        },
        "trajectory": [
            {"turn": n, "role": "user", "text": text, "ts": "2026-05-13T00:00:00Z"}
            for n, text in turns
        ],
    }


# ---------------------------------------------------------------------------
# Test 1: No hint before L1 threshold
# ---------------------------------------------------------------------------

class TestEmitHintBelowThreshold:
    def test_no_hint_when_not_engaged(self):
        """Returns empty string when L1 not engaged (early in session)."""
        thread = _thread([(1, "explain the auth middleware")], engaged=False)
        result = _format_trajectory_hint(thread, current_turn=1)
        assert result == "", f"Expected empty hint, got: {result!r}"

    def test_no_hint_when_only_current_turn(self):
        """Returns empty string when there are no prior queries (first turn)."""
        thread = _thread([(1, "first question")], engaged=True)
        # current_turn=1 is the only user turn — no prior queries to list
        result = _format_trajectory_hint(thread, current_turn=1)
        assert result == ""


# ---------------------------------------------------------------------------
# Test 2: Hint format when engaged
# ---------------------------------------------------------------------------

class TestHintFormat:
    def test_hint_lists_prior_queries(self):
        """Hint lists all prior user queries with (N) turn numbering."""
        thread = _thread(
            [(1, "explain the auth middleware"), (3, "why is token refresh failing?")],
            engaged=True,
        )
        result = _format_trajectory_hint(thread, current_turn=4)
        assert "(1)" in result
        assert "explain the auth middleware" in result
        assert "(3)" in result
        assert "why is token refresh failing?" in result

    def test_current_turn_not_in_prior_list(self):
        """Current turn is excluded from the prior queries list."""
        thread = _thread(
            [(1, "q1"), (2, "q2"), (3, "q3")],
            engaged=True,
        )
        result = _format_trajectory_hint(thread, current_turn=3)
        assert "(1)" in result
        assert "(2)" in result
        assert "(3)" not in result  # current turn excluded

    def test_hint_has_tinm_header(self):
        """Hint starts with [TINM — Turn N | Anchor: ...] header."""
        thread = _thread([(1, "q1"), (2, "q2")], engaged=True)
        result = _format_trajectory_hint(thread, current_turn=3)
        assert result.startswith("[TINM — Turn 3")
        assert "Anchor:" in result


# ---------------------------------------------------------------------------
# Test 3: Disambiguation instruction
# ---------------------------------------------------------------------------

class TestDisambiguationInstruction:
    def test_hint_contains_disambiguation_instruction(self):
        """Hint instructs Claude to use queries for disambiguation, not responses."""
        thread = _thread(
            [(1, "explain auth"), (3, "what is OAuth?"), (5, "add PKCE support")],
            engaged=True,
        )
        result = _format_trajectory_hint(thread, current_turn=6)
        assert "disambiguation" in result

    def test_hint_instructs_ignore_intermediate_responses(self):
        """Hint explicitly warns about intermediate answer contamination."""
        thread = _thread([(1, "q1"), (2, "q2")], engaged=True)
        result = _format_trajectory_hint(thread, current_turn=3)
        assert "intermediate answers" in result or "not the current target" in result

    def test_hint_contains_no_assistant_turns(self):
        """Assistant turns in trajectory are never included in the hint."""
        thread = _thread([(1, "user query 1"), (2, "user query 2")], engaged=True)
        # Add an assistant turn to the trajectory
        thread["trajectory"].append({
            "turn": 3,
            "role": "assistant",
            "text": "The middleware uses JWT tokens with a 15-minute expiry.",
            "ts": "2026-05-13T00:00:00Z",
        })
        result = _format_trajectory_hint(thread, current_turn=4)
        assert "JWT tokens" not in result
        assert "15-minute expiry" not in result


# ---------------------------------------------------------------------------
# Test: _top_query_terms helper
# ---------------------------------------------------------------------------

class TestAnaphoraDetection:
    def test_english_anaphora_detected(self):
        """English pronouns trigger anaphora detection."""
        from tinm_update import _ANAPHORIC_TOKEN_RE
        assert _ANAPHORIC_TOKEN_RE.search("it works now")
        assert _ANAPHORIC_TOKEN_RE.search("they are correct")
        assert _ANAPHORIC_TOKEN_RE.search("this is fine")

    def test_french_anaphora_detected(self):
        """French pronouns trigger anaphora detection."""
        from tinm_update import _ANAPHORIC_TOKEN_RE
        assert _ANAPHORIC_TOKEN_RE.search("ça marche vraiment")
        assert _ANAPHORIC_TOKEN_RE.search("cela est correct")
        assert _ANAPHORIC_TOKEN_RE.search("celui-ci fonctionne")
        assert _ANAPHORIC_TOKEN_RE.search("est-ce que ça marche ?")

    def test_no_false_positives_on_plain_content(self):
        """Non-anaphoric French/English queries don't match."""
        from tinm_update import _ANAPHORIC_TOKEN_RE
        # Pure noun phrase with no pronoun
        assert not _ANAPHORIC_TOKEN_RE.search("benchmark résultats TINM")
        assert not _ANAPHORIC_TOKEN_RE.search("install dependencies venv")


class TestTopQueryTerms:
    def test_extracts_top_terms_from_user_queries(self):
        """Extracts frequent non-stopword terms from user turns."""
        traj = [
            {"turn": 1, "role": "user", "text": "explain the OAuth middleware"},
            {"turn": 2, "role": "user", "text": "OAuth token refresh failing"},
            {"turn": 3, "role": "user", "text": "OAuth PKCE implementation"},
        ]
        terms = _top_query_terms(traj, n=3)
        assert "oauth" in terms  # most frequent

    def test_ignores_assistant_turns(self):
        """Assistant turns are not counted in term frequency."""
        traj = [
            {"turn": 1, "role": "user", "text": "explain middleware"},
            {"turn": 2, "role": "assistant", "text": "elephant elephant elephant"},
        ]
        terms = _top_query_terms(traj, n=5)
        assert "elephant" not in terms

    def test_filters_stopwords(self):
        """Common stopwords are excluded from top terms."""
        traj = [{"turn": 1, "role": "user", "text": "the and or but with in at"}]
        terms = _top_query_terms(traj, n=5)
        assert terms == []  # all stopwords filtered out
