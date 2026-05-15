"""Tests for F3: L1 activation threshold in user_prompt.sh.

Tests the bash logic that controls _SKIP_FIND:
  - turn_count < 6 AND no anaphora → _SKIP_FIND=1 (skip hint emission)
  - anaphora detected in prompt → _HAS_ANAPHORA=1 → _SKIP_FIND=0 (emit hint)
  - turn_count >= 6 → _SKIP_FIND=0 (always emit hint)

Strategy: extract the exact bash logic into a small test harness script and
run it via subprocess.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Bash harness
# ---------------------------------------------------------------------------

_BASH_HARNESS = r"""#!/bin/bash
# Harness: accepts PROMPT_TEXT and TURN_COUNT as env vars.
# Outputs:
#   HAS_ANAPHORA=<0|1>
#   SKIP_FIND=<0|1>

PROMPT_TEXT="${PROMPT_TEXT:-}"
_TRANSCRIPT_LINES="${TURN_COUNT:-0}"

# F3 logic (verbatim copy from user_prompt.sh)
_TURN_COUNT="${_TRANSCRIPT_LINES:-0}"
_ANAPHORA_PATTERN='(avant|earlier|before|comme|précédemment|previously|turn|tour|step|étape|like we|what we|ce qu|qu'"'"'on)'
_HAS_ANAPHORA=0
if printf '%s' "$PROMPT_TEXT" | grep -iqE "$_ANAPHORA_PATTERN" 2>/dev/null; then
    _HAS_ANAPHORA=1
fi

_SKIP_FIND=0
if [ "$_TURN_COUNT" -lt 6 ] && [ "$_HAS_ANAPHORA" -eq 0 ]; then
    _SKIP_FIND=1
fi

echo "HAS_ANAPHORA=${_HAS_ANAPHORA}"
echo "SKIP_FIND=${_SKIP_FIND}"
"""


def _run_harness(prompt: str, turn_count: int, tmp_path: Path) -> dict[str, str]:
    """Run the harness and return {'HAS_ANAPHORA': '0'|'1', 'SKIP_FIND': '0'|'1'}."""
    harness = tmp_path / "harness.sh"
    harness.write_text(_BASH_HARNESS)
    harness.chmod(0o755)
    result = subprocess.run(
        ["bash", str(harness)],
        env={
            "PROMPT_TEXT": prompt,
            "TURN_COUNT": str(turn_count),
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    out = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------
# Test: _SKIP_FIND logic
# ---------------------------------------------------------------------------

class TestSkipFind:
    def test_short_session_no_anaphora_skips(self, tmp_path):
        """turn_count=2, plain prompt → _SKIP_FIND=1."""
        out = _run_harness("implement the new feature", turn_count=2, tmp_path=tmp_path)
        assert out["SKIP_FIND"] == "1", f"Expected SKIP_FIND=1, got: {out}"
        assert out["HAS_ANAPHORA"] == "0", f"Expected HAS_ANAPHORA=0, got: {out}"

    def test_turn_count_zero_no_anaphora_skips(self, tmp_path):
        """turn_count=0 (first turn) → _SKIP_FIND=1."""
        out = _run_harness("hello", turn_count=0, tmp_path=tmp_path)
        assert out["SKIP_FIND"] == "1", f"Expected SKIP_FIND=1, got: {out}"

    def test_turn_count_5_no_anaphora_skips(self, tmp_path):
        """turn_count=5 is still < 6 with no anaphora → _SKIP_FIND=1."""
        out = _run_harness("run the tests again", turn_count=5, tmp_path=tmp_path)
        assert out["SKIP_FIND"] == "1", f"Expected SKIP_FIND=1 at turn 5, got: {out}"

    def test_turn_count_6_always_emits(self, tmp_path):
        """turn_count=6 → _SKIP_FIND=0 regardless of anaphora."""
        out = _run_harness("run the tests", turn_count=6, tmp_path=tmp_path)
        assert out["SKIP_FIND"] == "0", f"Expected SKIP_FIND=0 at turn 6, got: {out}"

    def test_turn_count_10_always_emits(self, tmp_path):
        """turn_count=10 → _SKIP_FIND=0."""
        out = _run_harness("next step please", turn_count=10, tmp_path=tmp_path)
        assert out["SKIP_FIND"] == "0", f"Expected SKIP_FIND=0 at turn 10, got: {out}"


# ---------------------------------------------------------------------------
# Test: Anaphora detection overrides short-session skip
# ---------------------------------------------------------------------------

class TestAnaphoraOverride:
    def test_fr_anaphora_comme_avant_short_session(self, tmp_path):
        """'comme avant' in French → _HAS_ANAPHORA=1 → _SKIP_FIND=0 even at turn 1."""
        out = _run_harness("comme avant, fix the bug", turn_count=1, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1, got: {out}"
        assert out["SKIP_FIND"] == "0", f"Expected SKIP_FIND=0, got: {out}"

    def test_en_anaphora_earlier_short_session(self, tmp_path):
        """'earlier' in English → _HAS_ANAPHORA=1 → _SKIP_FIND=0 at turn 2."""
        out = _run_harness("do it like earlier", turn_count=2, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1, got: {out}"
        assert out["SKIP_FIND"] == "0", f"Expected SKIP_FIND=0, got: {out}"

    def test_en_anaphora_before_short_session(self, tmp_path):
        """'before' in English → _HAS_ANAPHORA=1 at turn 3."""
        out = _run_harness("what worked before?", turn_count=3, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1, got: {out}"
        assert out["SKIP_FIND"] == "0", f"Expected SKIP_FIND=0, got: {out}"

    def test_en_anaphora_previously(self, tmp_path):
        """'previously' triggers anaphora detection."""
        out = _run_harness("as we discussed previously", turn_count=1, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1, got: {out}"

    def test_en_anaphora_like_we(self, tmp_path):
        """'like we' triggers anaphora detection."""
        out = _run_harness("do it like we said", turn_count=2, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1, got: {out}"

    def test_fr_anaphora_precédemment(self, tmp_path):
        """'précédemment' (French) triggers anaphora detection."""
        out = _run_harness("comme précédemment, lance le script", turn_count=1, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1, got: {out}"

    def test_fr_anaphora_tour(self, tmp_path):
        """'tour' (French for turn) triggers anaphora detection."""
        out = _run_harness("au tour précédent tu avais dit", turn_count=1, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1, got: {out}"

    def test_en_anaphora_step(self, tmp_path):
        """'step' in the prompt triggers anaphora detection."""
        out = _run_harness("redo the last step", turn_count=1, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1, got: {out}"

    def test_anaphora_case_insensitive(self, tmp_path):
        """Anaphora pattern is case-insensitive (EARLIER should match)."""
        out = _run_harness("EARLIER you showed me", turn_count=2, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "1", f"Expected HAS_ANAPHORA=1 for UPPERCASE, got: {out}"


# ---------------------------------------------------------------------------
# Test: No false positives
# ---------------------------------------------------------------------------

class TestNoFalsePositives:
    def test_plain_technical_prompt_no_anaphora(self, tmp_path):
        """Plain technical prompt with no anaphoric terms → _HAS_ANAPHORA=0."""
        out = _run_harness("run pytest on the auth module", turn_count=1, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "0", f"Expected HAS_ANAPHORA=0, got: {out}"

    def test_french_neutral_prompt_no_anaphora(self, tmp_path):
        """Pure French noun phrase with no anaphora → _HAS_ANAPHORA=0."""
        out = _run_harness("génère un rapport PDF", turn_count=2, tmp_path=tmp_path)
        assert out["HAS_ANAPHORA"] == "0", f"Expected HAS_ANAPHORA=0, got: {out}"
