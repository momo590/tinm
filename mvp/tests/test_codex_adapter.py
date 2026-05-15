"""Tests for tinm_codex.py — OpenAI Codex CLI prompt normalization."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from tinm_codex import normalize


def test_normalize_input_field():
    payload = {"input": "Write a unit test for foo()", "session_id": "s1"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Write a unit test for foo()"
    assert result["session_id"] == "s1"
    assert result["hook_type"] == "openai_codex_cli"


def test_normalize_prompt_field():
    payload = {"prompt": "Explain this diff", "conversation_id": "conv-1"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Explain this diff"
    assert result["session_id"] == "conv-1"


def test_normalize_message_field():
    payload = {"message": "Generate docstrings", "thread_id": "thr-9"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Generate docstrings"
    assert result["session_id"] == "thr-9"


def test_normalize_user_message_field():
    payload = {"user_message": "make the tests pass", "session_id": "abc"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "make the tests pass"
    assert result["session_id"] == "abc"


def test_normalize_no_prompt_returns_none():
    payload = {"model": "gpt-5-codex", "tools": []}
    result = normalize(payload)
    assert result is None


def test_normalize_empty_prompt_returns_none():
    payload = {"input": ""}
    result = normalize(payload)
    assert result is None


def test_normalize_missing_session_generates_id():
    payload = {"input": "hello"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"].startswith("codex-")
    assert len(result["session_id"]) == len("codex-") + 8


def test_normalize_transcript_default_empty():
    """Codex has no transcript_fields configured — should be empty string."""
    payload = {"input": "no transcript here", "session_id": "s2"}
    result = normalize(payload)
    assert result is not None
    assert result["transcript_path"] == ""


def test_normalize_strips_whitespace():
    payload = {"input": "  trim me  ", "session_id": "ws"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "trim me"


@pytest.mark.parametrize(
    "payload,expected_prompt",
    [
        ({"input": "first"}, "first"),
        ({"prompt": "second"}, "second"),
        ({"message": "third"}, "third"),
        ({"user_message": "fourth"}, "fourth"),
    ],
)
def test_all_prompt_field_variants(payload, expected_prompt):
    payload["session_id"] = "test-session"
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == expected_prompt
    assert result["hook_type"] == "openai_codex_cli"


def test_normalize_hook_type_constant():
    result = normalize({"input": "anything"})
    assert result is not None
    assert result["hook_type"] == "openai_codex_cli"
