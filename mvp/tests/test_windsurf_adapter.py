"""Tests for tinm_windsurf.py — Windsurf (Cascade) hook JSON normalization."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from tinm_windsurf import normalize


def test_normalize_prompt_field():
    payload = {"prompt": "Fix the bug in auth.ts", "session_id": "sess-abc"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Fix the bug in auth.ts"
    assert result["session_id"] == "sess-abc"
    assert result["hook_type"] == "windsurf_beforeAgent"


def test_normalize_text_field():
    payload = {"text": "Explain this function", "cascade_id": "cas-1"}
    result = normalize(payload)
    assert result["prompt"] == "Explain this function"
    assert result["session_id"] == "cas-1"


def test_normalize_user_input_field():
    payload = {"user_input": "Refactor this", "agent_id": "ag-7"}
    result = normalize(payload)
    assert result["prompt"] == "Refactor this"
    assert result["session_id"] == "ag-7"


def test_normalize_message_field():
    payload = {"message": "A message", "sessionId": "s9"}
    result = normalize(payload)
    assert result["prompt"] == "A message"
    assert result["session_id"] == "s9"


def test_normalize_query_field():
    payload = {"query": "search query", "session_id": "s1"}
    result = normalize(payload)
    assert result["prompt"] == "search query"


def test_normalize_nested_message():
    payload = {"message": {"text": "nested windsurf prompt"}}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "nested windsurf prompt"


def test_normalize_no_prompt_returns_none():
    payload = {"model": "claude", "workspace": "/home/user"}
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
    assert result["session_id"].startswith("windsurf-")
    assert len(result["session_id"]) > 9


def test_normalize_transcript_path_extracted():
    payload = {
        "prompt": "test",
        "session_id": "s1",
        "transcript_path": "/tmp/windsurf-cascade.jsonl",
    }
    result = normalize(payload)
    assert result["transcript_path"] == "/tmp/windsurf-cascade.jsonl"


def test_normalize_transcript_path_default_empty():
    payload = {"prompt": "test", "session_id": "s1"}
    result = normalize(payload)
    assert result["transcript_path"] == ""


def test_normalize_hook_type_is_windsurf_beforeAgent():
    payload = {"prompt": "x", "session_id": "s1"}
    result = normalize(payload)
    assert result["hook_type"] == "windsurf_beforeAgent"


# Fixture test: all prompt field variants
@pytest.mark.parametrize("payload,expected_prompt", [
    ({"prompt": "hello"}, "hello"),
    ({"text": "world"}, "world"),
    ({"user_input": "foo"}, "foo"),
    ({"message": "bar"}, "bar"),
    ({"query": "baz"}, "baz"),
])
def test_all_prompt_field_variants(payload, expected_prompt):
    payload["session_id"] = "test"
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == expected_prompt


# Fixture test: all session field variants
@pytest.mark.parametrize("session_field", [
    "session_id",
    "cascade_id",
    "agent_id",
    "sessionId",
])
def test_all_session_field_variants(session_field):
    payload = {"prompt": "test", session_field: "id-xyz"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"] == "id-xyz"
