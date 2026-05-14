"""Tests for tinm_approval.score().

Covers spec §5 unit tests T1-T12, T31 (code-switch), T38 (corpus eval).
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from tinm_approval import (
    WINDOW_CHARS,
    ApprovalSignal,
    is_capture,
    is_rejection,
    score,
)


class TestStrongApproval:
    def test_single_letter_c(self):
        assert score("C").w == 1.0
        assert score("C").label == "strong_approval"

    def test_lowercase_ok(self):
        assert score("ok").w == 1.0

    def test_go_with_bang(self):
        assert score("GO !").w == 1.0

    def test_oui(self):
        assert score("oui").w == 1.0

    def test_parfait(self):
        assert score("parfait").w == 1.0

    def test_merci(self):
        assert score("merci").w == 1.0


class TestExplicitApproval:
    def test_cest_top(self):
        assert score("C'est top!").w == 0.9
        assert score("C'est top!").label == "explicit_approval"

    def test_cest_nickel(self):
        assert score("c'est nickel").w == 0.9

    def test_looks_good(self):
        assert score("Looks good").w == 0.9


class TestApprovalThenMove:
    def test_ok_then_task(self):
        s = score("OK, maintenant aide-moi avec X")
        assert s.w == 0.7
        assert s.label == "approval_then_move"

    def test_cest_top_then_task(self):
        # "C'est top!" matches explicit_approval (0.9), specific wins
        assert score("C'est top! Maintenant…").w == 0.9


class TestCorrection:
    def test_non(self):
        assert score("Non").w == -0.8
        assert score("Non").label == "correction"

    def test_le_pain_nest_pas(self):
        s = score("Le pain n'est pas là")
        assert s.w == -0.8

    def test_stop(self):
        assert score("Stop").w == -0.8

    def test_attends(self):
        assert score("Attends, autre sujet").w == -0.8


class TestStrongCorrection:
    def test_cest_faux(self):
        assert score("C'est faux").w == -1.0

    def test_tu_nas_pas_compris(self):
        assert score("Tu n'as pas compris").w == -1.0


class TestRefinement:
    def test_oui_mais(self):
        s = score("oui mais regardes ça")
        assert s.w == 0.2
        assert s.label == "refinement"

    def test_actually(self):
        assert score("actually that's wrong").w == 0.2


class TestQuestion:
    def test_est_ce_que(self):
        s = score("Est-ce que tu peux refaire ?")
        assert s.w == 0.0
        assert s.label == "question"

    def test_how(self):
        assert score("How does X work?").w == 0.0

    def test_pourquoi(self):
        assert score("Pourquoi ?").w == 0.0


class TestImplicitMoveOn:
    def test_new_task(self):
        s = score("supprime la branche personal-assets")
        assert s.w == 0.3
        assert s.label == "implicit_move_on"

    def test_new_subject(self):
        assert score("J'ai déjà téléchargé.").w == 0.3


class TestEdgeCases:
    def test_empty_string(self):
        s = score("")
        assert s.w == 0.0
        assert s.label == "empty"

    def test_whitespace_only(self):
        assert score("   \n\t  ").label == "empty"

    def test_window_truncation(self):
        # text longer than WINDOW_CHARS is truncated for scoring
        long = "OK" + " padding" * 50
        assert len(long) > WINDOW_CHARS
        # still classifies as approval_then_move on first 60 chars
        assert score(long).w == 0.7


class TestCaseInsensitivity:
    def test_OK_upper(self):
        assert score("OK").w == 1.0

    def test_Oui_capitalised(self):
        assert score("Oui").w == 1.0

    def test_NON_upper(self):
        assert score("NON").w == -0.8


class TestRegexNonOverlap:
    def test_strong_approval_beats_approval_then_move(self):
        # "OK" alone matches strong_approval (whole), not approval_then_move
        assert score("OK").label == "strong_approval"

    def test_correction_beats_strong_approval(self):
        # "Stop" classifies as correction, not as missing strong_approval
        assert score("Stop").label == "correction"


class TestCodeSwitch:
    """T31 — Cross-language: prompt mixes FR + EN."""

    def test_yes_parfait_merci(self):
        # "yes" at position 0 matches strong_approval whole? No, "yes parfait merci"
        # has 3 tokens — strong_approval requires whole prompt to be one token.
        # Falls through to approval_then_move (yes + content).
        assert score("yes parfait merci").w == 0.7

    def test_ok_parfait(self):
        assert score("ok parfait").w == 0.7

    def test_perfect_in_french_sentence(self):
        # "Perfect" then content
        assert score("Perfect, maintenant fais X").w == 0.7


class TestCaptureRejectionPredicates:
    def test_is_capture_strong(self):
        assert is_capture(ApprovalSignal(1.0, "x", "x")) is True

    def test_is_capture_below_threshold(self):
        assert is_capture(ApprovalSignal(0.5, "x", "x")) is False

    def test_is_capture_at_exact_threshold(self):
        assert is_capture(ApprovalSignal(0.7, "x", "x")) is True

    def test_is_rejection_strong(self):
        assert is_rejection(ApprovalSignal(-1.0, "x", "x")) is True

    def test_is_rejection_above_threshold(self):
        assert is_rejection(ApprovalSignal(0.0, "x", "x")) is False

    def test_is_rejection_at_exact_threshold(self):
        assert is_rejection(ApprovalSignal(-0.3, "x", "x")) is True


# ---------------------------------------------------------------------------
# T38 — Labeled corpus eval. Replay hand-labeled turns from
# tests/corpus_approval_eval.jsonl and assert ≥80% match the expected label.
# ---------------------------------------------------------------------------


def _load_corpus():
    path = pathlib.Path(__file__).with_name("corpus_approval_eval.jsonl")
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def test_corpus_eval_accuracy():
    corpus = _load_corpus()
    if not corpus:
        pytest.skip("corpus_approval_eval.jsonl not present")
    hits = 0
    misses = []
    for entry in corpus:
        actual = score(entry["text"]).label
        if actual == entry["expected_label"]:
            hits += 1
        else:
            misses.append((entry["text"], entry["expected_label"], actual))
    accuracy = hits / len(corpus)
    assert accuracy >= 0.80, (
        f"corpus eval accuracy {accuracy:.0%} < 80% — "
        f"misses ({len(misses)}/{len(corpus)}): {misses[:5]}"
    )
