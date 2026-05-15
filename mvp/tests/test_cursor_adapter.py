"""Tests for tinm_cursor.py — Cursor hook JSON normalization."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from tinm_cursor import normalize


def test_normalize_prompt_field():
    payload = {"prompt": "Fix the bug in auth.ts", "session_id": "sess-abc"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Fix the bug in auth.ts"
    assert result["session_id"] == "sess-abc"
    assert result["hook_type"] == "cursor_beforeSubmitPrompt"


def test_normalize_text_field():
    payload = {"text": "Explain this function", "sessionId": "sess-123"}
    result = normalize(payload)
    assert result["prompt"] == "Explain this function"
    assert result["session_id"] == "sess-123"


def test_normalize_message_field():
    payload = {"message": "Refactor this", "session": "s1"}
    result = normalize(payload)
    assert result["prompt"] == "Refactor this"


def test_normalize_nested_message():
    payload = {"message": {"text": "nested prompt"}}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "nested prompt"


def test_normalize_no_prompt_returns_none():
    payload = {"model": "gpt-4", "workspace": "/home/user"}
    result = normalize(payload)
    assert result is None


def test_normalize_empty_prompt_returns_none():
    payload = {"prompt": "   "}
    result = normalize(payload)
    assert result is None


def test_normalize_missing_session_generates_id():
    payload = {"prompt": "test prompt"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"].startswith("cursor-")
    assert len(result["session_id"]) > 7


def test_normalize_transcript_path_empty():
    payload = {"prompt": "test", "session_id": "s1"}
    result = normalize(payload)
    assert result["transcript_path"] == ""


# Fixture test: simulate real Cursor JSON schema candidates
@pytest.mark.parametrize("payload,expected_prompt", [
    ({"prompt": "hello"}, "hello"),
    ({"text": "world"}, "world"),
    ({"input": "foo"}, "foo"),
    ({"query": "bar"}, "bar"),
    ({"content": "baz"}, "baz"),
])
def test_all_prompt_field_variants(payload, expected_prompt):
    payload["session_id"] = "test"
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == expected_prompt
