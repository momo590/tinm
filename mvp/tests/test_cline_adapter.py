"""Tests for tinm_cline.py — Cline (VS Code extension) hook JSON normalization."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from tinm_cline import normalize


def test_normalize_text_field():
    payload = {"text": "Fix the bug in auth.ts", "taskId": "task-abc"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "Fix the bug in auth.ts"
    assert result["session_id"] == "task-abc"
    assert result["hook_type"] == "cline_vscode"


def test_normalize_message_field():
    payload = {"message": "Explain this function", "task_id": "task-123"}
    result = normalize(payload)
    assert result["prompt"] == "Explain this function"
    assert result["session_id"] == "task-123"


def test_normalize_prompt_field():
    payload = {"prompt": "A prompt", "id": "id-9"}
    result = normalize(payload)
    assert result["prompt"] == "A prompt"
    assert result["session_id"] == "id-9"


def test_normalize_user_message_field():
    payload = {"userMessage": "From the user", "session_id": "s1"}
    result = normalize(payload)
    assert result["prompt"] == "From the user"


def test_normalize_user_message_snake_case():
    payload = {"user_message": "snake case", "session_id": "s1"}
    result = normalize(payload)
    assert result["prompt"] == "snake case"


def test_normalize_nested_message():
    payload = {"message": {"text": "nested cline prompt"}}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "nested cline prompt"


def test_normalize_no_prompt_returns_none():
    payload = {"vsCodeVersion": "1.95", "extensionId": "cline.cline"}
    result = normalize(payload)
    assert result is None


def test_normalize_empty_prompt_returns_none():
    payload = {"text": "   "}
    result = normalize(payload)
    assert result is None


def test_normalize_missing_session_generates_id():
    payload = {"text": "test prompt"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"].startswith("cline-")
    assert len(result["session_id"]) > 6


def test_normalize_task_history_path_extracted():
    payload = {
        "text": "test",
        "taskId": "task-1",
        "taskHistoryPath": "/home/user/.vscode/cline/history.json",
    }
    result = normalize(payload)
    assert result["transcript_path"] == "/home/user/.vscode/cline/history.json"


def test_normalize_history_path_extracted():
    payload = {
        "text": "test",
        "taskId": "task-1",
        "history_path": "/tmp/cline-history.log",
    }
    result = normalize(payload)
    assert result["transcript_path"] == "/tmp/cline-history.log"


def test_normalize_hook_type_is_cline_vscode():
    payload = {"text": "x", "taskId": "task-1"}
    result = normalize(payload)
    assert result["hook_type"] == "cline_vscode"


def test_normalize_taskid_is_primary_session_field():
    """Cline's primary session identifier is taskId, not session_id."""
    payload = {"text": "test", "taskId": "primary-task-id"}
    result = normalize(payload)
    assert result["session_id"] == "primary-task-id"


# Fixture test: all prompt field variants
@pytest.mark.parametrize("payload,expected_prompt", [
    ({"text": "hello"}, "hello"),
    ({"message": "world"}, "world"),
    ({"prompt": "foo"}, "foo"),
    ({"userMessage": "bar"}, "bar"),
    ({"user_message": "baz"}, "baz"),
])
def test_all_prompt_field_variants(payload, expected_prompt):
    payload["taskId"] = "test"
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == expected_prompt


# Fixture test: all session field variants
@pytest.mark.parametrize("session_field", [
    "taskId",
    "task_id",
    "id",
    "session_id",
])
def test_all_session_field_variants(session_field):
    payload = {"text": "test", session_field: "id-xyz"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"] == "id-xyz"
