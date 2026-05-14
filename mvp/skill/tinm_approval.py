"""TINM approval signal scorer.

Classifies the next user prompt as approval / refinement / question / correction
of the prior assistant turn. Used by the assistant-capture pipeline to decide
whether to promote the buffered assistant turn to an artifact, log it as
rejected, or treat as neutral.

Design rationale: regex-based, first 60 chars only. Cheap (<1ms), language-aware
(FR + EN), and explicit. Calibration plan: log every classification via
telemetry, tune regex weights from real distribution in v0.2.2.

Spec ref: design/root-tinm-design-20260514-approval-weighted-capture.md §2.2.
"""
from __future__ import annotations

import re
from typing import NamedTuple

WINDOW_CHARS = 60


class ApprovalSignal(NamedTuple):
    w: float
    label: str
    pattern_id: str


# Patterns ordered by specificity: most specific first wins on overlap.
_PATTERNS: list[tuple[str, re.Pattern, float, str]] = [
    (
        "strong_correction",
        re.compile(
            r"^\s*(tu n[''’]?as pas compris|t[''’]?es à côté|"
            r"c[''’]?est faux|erreur|"
            r"you got it wrong|that[''’]?s wrong|no that[''’]?s wrong)",
            re.IGNORECASE,
        ),
        -1.0,
        "strong_correction",
    ),
    (
        "correction",
        re.compile(
            r"^\s*(non|nope|pas (comme ça|du tout)|stop|wait|attends|"
            r"le pain n[''’]?est pas|that[''’]?s not the pain)",
            re.IGNORECASE,
        ),
        -0.8,
        "correction",
    ),
    (
        "explicit_approval",
        re.compile(
            r"^\s*(c[''’]?est (top|bon|ok|génial|cool|parfait|nickel)|"
            r"sounds (good|great|perfect)|looks good)",
            re.IGNORECASE,
        ),
        0.9,
        "explicit_approval",
    ),
    (
        "strong_approval_whole",
        re.compile(
            r"^\s*(c|ok|go|yes|y|oui|parfait|exact(ement)?|top|génial|"
            r"nickel|super|merci|thanks|cool|great|perfect|done)"
            r"[\s.!?]*$",
            re.IGNORECASE,
        ),
        1.0,
        "strong_approval",
    ),
    (
        "refinement",
        re.compile(
            r"^\s*(oui mais|sauf que|mais|but|except|"
            r"actually|en fait[,\s])",
            re.IGNORECASE,
        ),
        0.2,
        "refinement",
    ),
    (
        "question",
        re.compile(
            r"^\s*(est-ce que|comment|pourquoi|que (peux|fait|vais)|"
            r"how|why|what|when|where|which|can you|could you|"
            r"qu'est-ce que)",
            re.IGNORECASE,
        ),
        0.0,
        "question",
    ),
    (
        "approval_then_move",
        re.compile(
            r"^\s*(ok|c|go|yes|oui|parfait|cool|good|great|merci|thanks|"
            r"top|perfect|super|génial|nickel)"
            r"[\s,.!]+\S",
            re.IGNORECASE,
        ),
        0.7,
        "approval_then_move",
    ),
]


def score(text: str) -> ApprovalSignal:
    """Classify the user prompt's first window.

    Returns a (w, label, pattern_id) tuple. w in [-1.0, 1.0].
    Empty / whitespace → (0.0, "empty", "empty").
    No pattern match → (0.3, "implicit_move_on", "default").
    """
    if not text or not text.strip():
        return ApprovalSignal(0.0, "empty", "empty")

    window = text.strip()[:WINDOW_CHARS]

    for _, pattern, w, label in _PATTERNS:
        if pattern.search(window):
            return ApprovalSignal(w, label, label)

    return ApprovalSignal(0.3, "implicit_move_on", "default")


def is_capture(signal: ApprovalSignal, threshold: float = 0.7) -> bool:
    """True if signal warrants promotion to an approved artifact."""
    return signal.w >= threshold


def is_rejection(signal: ApprovalSignal, threshold: float = -0.3) -> bool:
    """True if signal warrants writing to rejected_log."""
    return signal.w <= threshold


__all__ = ["ApprovalSignal", "score", "is_capture", "is_rejection", "WINDOW_CHARS"]
