"""Tests for tinm_aider.py — Aider CLI prompt normalization."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from tinm_aider import normalize


def test_normalize_input_field():
    payload = {"input": "Fix the bug in auth.py", "session_id": "sess-abc"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Fix the bug in auth.py"
    assert result["session_id"] == "sess-abc"
    assert result["hook_type"] == "aider_cli"


def test_normalize_prompt_field():
    payload = {"prompt": "Refactor this module", "session_id": "sess-xyz"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Refactor this module"
    assert result["session_id"] == "sess-xyz"


def test_normalize_user_input_field():
    payload = {"user_input": "Add type hints", "chat_id": "chat-1"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Add type hints"
    assert result["session_id"] == "chat-1"


def test_normalize_chat_history_path():
    payload = {
        "input": "do the thing",
        "session_id": "s1",
        "chat_history_file": "/tmp/.aider.chat.history.md",
    }
    result = normalize(payload)
    assert result is not None
    assert result["transcript_path"] == "/tmp/.aider.chat.history.md"


def test_normalize_no_prompt_returns_none():
    payload = {"model": "claude-3-sonnet", "edits": []}
    result = normalize(payload)
    assert result is None


def test_normalize_empty_prompt_returns_none():
    payload = {"input": "   \t  "}
    result = normalize(payload)
    assert result is None


def test_normalize_missing_session_generates_id():
    payload = {"input": "say hi"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"].startswith("aider-")
    # Generated UUID hex slice => total prefix + 8 hex chars
    assert len(result["session_id"]) == len("aider-") + 8


def test_normalize_strips_whitespace():
    payload = {"input": "  trim me  \n", "session_id": "s2"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "trim me"


def test_normalize_transcript_default_empty():
    payload = {"input": "no transcript", "session_id": "s3"}
    result = normalize(payload)
    assert result is not None
    assert result["transcript_path"] == ""


@pytest.mark.parametrize(
    "payload,expected_prompt",
    [
        ({"input": "alpha"}, "alpha"),
        ({"prompt": "beta"}, "beta"),
        ({"user_input": "gamma"}, "gamma"),
    ],
)
def test_all_prompt_field_variants(payload, expected_prompt):
    payload["session_id"] = "test-session"
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == expected_prompt
    assert result["hook_type"] == "aider_cli"


def test_normalize_hook_type_constant():
    """Sanity-check: hook_type should be the aider tag, not another vendor."""
    result = normalize({"input": "anything"})
    assert result is not None
    assert result["hook_type"] == "aider_cli"
