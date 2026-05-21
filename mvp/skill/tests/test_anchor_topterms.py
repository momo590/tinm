"""Tests for _top_query_terms — recency, length normalization, meta-turn boost.

Regression coverage for project_tinm_anchor_recency_bug (observed 2026-05-18):
long autopilot briefs mid-session dominated the bag-of-words and overwrote
the actually-current topic. Fix lives in tinm_update._top_query_terms.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tinm_update import _is_meta_turn, _top_query_terms


def _u(turn: int, text: str) -> dict:
    return {"turn": turn, "role": "user", "text": text}


def _a(turn: int, text: str) -> dict:
    return {"turn": turn, "role": "assistant", "text": text}


# ---------------------------------------------------------------------------
# Recency — two short recent turns about X beat one long old turn about Y.
# ---------------------------------------------------------------------------
def test_recency_wins_over_old_volume():
    # Realistic long brief: many words, "yulu" mentioned a handful of times
    # alongside other content. Without length normalization, "yulu" would
    # accumulate raw count and beat the short xena turns.
    filler = " ".join([f"word{i}" for i in range(40)])
    long_y = f"{filler} yulu {filler} yulu pipeline yulu metric yulu eval yulu"
    trajectory = [
        _u(1, long_y),
        _a(2, "ok"),
        _u(3, "xena protocol details"),
        _a(4, "..."),
        _u(5, "xena edge cases"),
    ]
    terms = _top_query_terms(trajectory, n=3)
    assert "xena" in terms
    assert terms.index("xena") < (
        terms.index("yulu") if "yulu" in terms else len(terms)
    )


# ---------------------------------------------------------------------------
# Length normalization — one 500-word mono-topic post does NOT beat
# three short posts of a different topic.
# ---------------------------------------------------------------------------
def test_length_normalization_caps_long_pastes():
    long_yulu = " ".join(["yulu"] * 500)
    trajectory = [
        _u(1, "xena hot path"),
        _u(2, long_yulu),
        _u(3, "xena dispatch"),
        _u(4, "xena bench"),
    ]
    terms = _top_query_terms(trajectory, n=3)
    assert "xena" in terms
    assert terms.index("xena") < (
        terms.index("yulu") if "yulu" in terms else len(terms)
    )


# ---------------------------------------------------------------------------
# Meta-turns — "reprends" must NOT appear in top terms.
# ---------------------------------------------------------------------------
def test_meta_turn_tokens_excluded():
    trajectory = [
        _u(1, "xena hot path"),
        _u(2, "xena edge cases"),
        _u(3, "reprends en autopilot"),
    ]
    terms = _top_query_terms(trajectory, n=5)
    assert "xena" in terms
    assert "reprends" not in terms
    assert "autopilot" not in terms


# ---------------------------------------------------------------------------
# Meta-turn boost — "reprends" after talking about X but with Y elsewhere
# in the session: X (the latest non-meta topic) gets x2 and beats Y.
# ---------------------------------------------------------------------------
def test_meta_turn_boosts_prior_topic_over_volume():
    yulu_block = " ".join(["yulu", "yulu", "yulu"] * 5)
    trajectory = [
        _u(1, yulu_block),
        _u(2, yulu_block),
        _u(3, yulu_block),
        _u(4, "xena protocol"),
        _u(5, "on reprend"),
    ]
    terms = _top_query_terms(trajectory, n=3)
    assert "xena" in terms
    assert terms.index("xena") < (
        terms.index("yulu") if "yulu" in terms else len(terms)
    )


# ---------------------------------------------------------------------------
# Regression — a thread with one user turn returns its frequent terms,
# same as the old behavior (no decay applies, no meta).
# ---------------------------------------------------------------------------
def test_single_turn_unchanged():
    trajectory = [_u(1, "alpha beta alpha gamma alpha beta")]
    terms = _top_query_terms(trajectory, n=3)
    assert terms[0] == "alpha"
    assert "beta" in terms
    assert "gamma" in terms


# ---------------------------------------------------------------------------
# Empty / no-user trajectory.
# ---------------------------------------------------------------------------
def test_empty_trajectory_returns_empty():
    assert _top_query_terms([], n=5) == []


def test_assistant_only_returns_empty():
    assert _top_query_terms([_a(1, "hi there friend")], n=5) == []


# ---------------------------------------------------------------------------
# Meta-turn detection — sanity for FR + EN phrases.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "reprends",
        "reprends en autopilot",
        "on reprend",
        "on continue",
        "continue",
        "continuons",
        "recommence stp",
        "where were we",
        "let's continue from here",
        "pick up where you left off",
        "résume",
        "résumons",
    ],
)
def test_is_meta_turn_positive(text):
    assert _is_meta_turn(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "xena protocol details",
        "fix this bug please",
        "ship the v0.3.0 tag",
        "qu'est-ce que tu en penses",
        "",
    ],
)
def test_is_meta_turn_negative(text):
    assert _is_meta_turn(text) is False


# ---------------------------------------------------------------------------
# Consecutive meta-turns — the boost should land on the most recent
# non-meta predecessor, not pile onto another meta-turn.
# ---------------------------------------------------------------------------
def test_consecutive_meta_turns_boost_real_topic():
    yulu_block = " ".join(["yulu"] * 30)
    trajectory = [
        _u(1, yulu_block),
        _u(2, yulu_block),
        _u(3, "xena hot path"),
        _u(4, "reprends"),
        _u(5, "on continue"),
    ]
    terms = _top_query_terms(trajectory, n=3)
    assert "xena" in terms
    assert "reprends" not in terms
    assert "continue" not in terms
