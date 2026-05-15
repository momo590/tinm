"""Tests for tinm_openclaw.py — OpenClaw plugin manifest JSON normalization."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from tinm_openclaw import normalize


def test_normalize_message_field():
    payload = {"message": "Hello from WhatsApp", "session_id": "sess-abc"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Hello from WhatsApp"
    assert result["session_id"] == "sess-abc"
    assert result["hook_type"] == "openclaw_plugin"


def test_normalize_text_field():
    payload = {"text": "Test message", "thread_id": "thr-123"}
    result = normalize(payload)
    assert result["prompt"] == "Test message"
    assert result["session_id"] == "thr-123"


def test_normalize_input_field():
    payload = {"input": "Process this", "conversation_id": "conv-9"}
    result = normalize(payload)
    assert result["prompt"] == "Process this"
    assert result["session_id"] == "conv-9"


def test_normalize_prompt_field():
    payload = {"prompt": "A prompt", "context_id": "ctx-1"}
    result = normalize(payload)
    assert result["prompt"] == "A prompt"
    assert result["session_id"] == "ctx-1"


def test_normalize_user_message_field():
    payload = {"user_message": "From the user", "session_id": "s1"}
    result = normalize(payload)
    assert result["prompt"] == "From the user"


def test_normalize_content_field():
    payload = {"content": "Some content", "session_id": "s1"}
    result = normalize(payload)
    assert result["prompt"] == "Some content"


def test_normalize_nested_message():
    payload = {"message": {"text": "nested openclaw prompt"}}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "nested openclaw prompt"


def test_normalize_no_prompt_returns_none():
    payload = {"plugin_version": "1.0", "manifest": {}}
    result = normalize(payload)
    assert result is None


def test_normalize_empty_prompt_returns_none():
    payload = {"message": "   "}
    result = normalize(payload)
    assert result is None


def test_normalize_missing_session_generates_id():
    payload = {"message": "test prompt"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"].startswith("openclaw-")
    assert len(result["session_id"]) > 9


def test_normalize_transcript_path_extracted():
    """OpenClaw exposes a transcript_path field — unlike Cursor."""
    payload = {
        "message": "test",
        "session_id": "s1",
        "transcript_path": "/tmp/openclaw-thread-1.jsonl",
    }
    result = normalize(payload)
    assert result["transcript_path"] == "/tmp/openclaw-thread-1.jsonl"


def test_normalize_history_path_extracted():
    payload = {
        "message": "test",
        "session_id": "s1",
        "history_path": "/var/log/openclaw/history.log",
    }
    result = normalize(payload)
    assert result["transcript_path"] == "/var/log/openclaw/history.log"


def test_normalize_log_path_extracted():
    payload = {
        "message": "test",
        "session_id": "s1",
        "log_path": "/tmp/openclaw.log",
    }
    result = normalize(payload)
    assert result["transcript_path"] == "/tmp/openclaw.log"


def test_normalize_hook_type_is_openclaw_plugin():
    payload = {"message": "x", "session_id": "s1"}
    result = normalize(payload)
    assert result["hook_type"] == "openclaw_plugin"


# Fixture test: all prompt field variants
@pytest.mark.parametrize("payload,expected_prompt", [
    ({"message": "hello"}, "hello"),
    ({"text": "world"}, "world"),
    ({"input": "foo"}, "foo"),
    ({"prompt": "bar"}, "bar"),
    ({"user_message": "baz"}, "baz"),
    ({"content": "qux"}, "qux"),
])
def test_all_prompt_field_variants(payload, expected_prompt):
    payload["session_id"] = "test"
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == expected_prompt


# Fixture test: all session field variants
@pytest.mark.parametrize("session_field", [
    "session_id",
    "thread_id",
    "conversation_id",
    "context_id",
])
def test_all_session_field_variants(session_field):
    payload = {"message": "test", session_field: "id-xyz"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"] == "id-xyz"
