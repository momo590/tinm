"""Tests for tinm_continue.py — Continue.dev normalize + import."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill"))

from tinm_continue import (  # noqa: E402
    _extract_user_messages,
    import_session_file,
    normalize,
)


# ── normalize() ────────────────────────────────────────────────────────────


def test_normalize_text_field():
    payload = {"text": "rename this function", "sessionId": "abc"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "rename this function"
    assert result["session_id"] == "abc"
    assert result["hook_type"] == "continue_dev"


def test_normalize_message_field():
    payload = {"message": "refactor", "session_id": "xyz"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "refactor"
    assert result["session_id"] == "xyz"


def test_normalize_prompt_field():
    payload = {"prompt": "add tests", "id": "session-9"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "add tests"
    assert result["session_id"] == "session-9"


def test_normalize_input_field():
    payload = {"input": "fix the lint errors", "sessionId": "s10"}
    result = normalize(payload)
    assert result is not None
    assert result["prompt"] == "fix the lint errors"


def test_normalize_session_history_path():
    payload = {
        "text": "with history",
        "sessionId": "s1",
        "sessionHistoryPath": "/home/u/.continue/sessions/s1.json",
    }
    result = normalize(payload)
    assert result is not None
    assert result["transcript_path"] == "/home/u/.continue/sessions/s1.json"


def test_normalize_no_prompt_returns_none():
    payload = {"model": "gpt-4", "context": []}
    result = normalize(payload)
    assert result is None


def test_normalize_missing_session_generates_id():
    payload = {"text": "say hi"}
    result = normalize(payload)
    assert result is not None
    assert result["session_id"].startswith("continue-")


# ── _extract_user_messages() ───────────────────────────────────────────────


def test_extract_messages_string_content():
    session = {
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi back"},
            {"role": "user", "content": "second user msg"},
        ]
    }
    msgs = _extract_user_messages(session)
    assert msgs == ["hello", "second user msg"]


def test_extract_messages_history_key():
    """Some Continue.dev versions use 'history' instead of 'messages'."""
    session = {
        "history": [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
    }
    msgs = _extract_user_messages(session)
    assert msgs == ["first", "second"]


def test_extract_messages_multipart_content():
    """Multi-part content: list of {'type': 'text', 'text': '...'} parts."""
    session = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "part one"},
                    {"type": "text", "text": "part two"},
                ],
            }
        ]
    }
    msgs = _extract_user_messages(session)
    assert msgs == ["part one part two"]


def test_extract_messages_skips_non_user():
    session = {
        "messages": [
            {"role": "assistant", "content": "no"},
            {"role": "system", "content": "also no"},
        ]
    }
    msgs = _extract_user_messages(session)
    assert msgs == []


def test_extract_messages_empty_session():
    assert _extract_user_messages({}) == []
    assert _extract_user_messages({"messages": []}) == []
    assert _extract_user_messages({"messages": "not a list"}) == []


def test_extract_messages_skips_empty_content():
    session = {
        "messages": [
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "user", "content": "good"},
        ]
    }
    msgs = _extract_user_messages(session)
    assert msgs == ["good"]


# ── import_session_file() ──────────────────────────────────────────────────


@pytest.fixture
def fixture_session_file(tmp_path: Path) -> Path:
    """Write a sample Continue.dev session JSON to disk."""
    p = tmp_path / "abc123.json"
    p.write_text(
        json.dumps(
            {
                "sessionId": "abc123",
                "messages": [
                    {"role": "user", "content": "first prompt"},
                    {"role": "assistant", "content": "reply"},
                    {"role": "user", "content": "second prompt"},
                ],
            }
        )
    )
    return p


def test_import_session_file_missing(tmp_path):
    n = import_session_file(str(tmp_path / "does_not_exist.json"))
    assert n == 0


def test_import_session_file_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    n = import_session_file(str(p))
    assert n == 0


def test_import_session_file_no_user_messages(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"messages": [{"role": "assistant", "content": "x"}]}))
    n = import_session_file(str(p))
    assert n == 0


def test_import_session_file_happy_path(fixture_session_file, monkeypatch):
    """Stub out user_prompt.sh so we don't hit the real pipeline.

    We replace subprocess.Popen with a fake that records each call and
    returns immediately. import_session_file should forward both user
    messages.
    """
    calls = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            self.stdin = None

        def communicate(self, input=None, timeout=None):
            calls[-1]["input"] = input
            return b"", b""

        def kill(self):
            pass

    # Force the hook resolver to point at a file that exists so the
    # function doesn't bail out before trying to spawn.
    fake_hook = fixture_session_file.parent / "user_prompt.sh"
    fake_hook.write_text("#!/bin/bash\nexit 0\n")

    import tinm_continue

    monkeypatch.setattr(tinm_continue, "_resolve_hook_path", lambda: fake_hook)
    monkeypatch.setattr(tinm_continue.subprocess, "Popen", FakePopen)

    n = import_session_file(str(fixture_session_file))
    assert n == 2
    assert len(calls) == 2

    # Each call should have been fed a normalized JSON payload
    payloads = [json.loads(c["input"]) for c in calls]
    prompts = [p["prompt"] for p in payloads]
    assert prompts == ["first prompt", "second prompt"]
    # All payloads should carry the Continue.dev hook tag
    assert all(p["hook_type"] == "continue_dev" for p in payloads)
    # And the session_id from the file
    assert all(p["session_id"] == "abc123" for p in payloads)


def test_import_session_file_no_hook(fixture_session_file, monkeypatch):
    """If user_prompt.sh is missing, return 0 without raising."""
    missing = fixture_session_file.parent / "absent.sh"
    import tinm_continue

    monkeypatch.setattr(tinm_continue, "_resolve_hook_path", lambda: missing)
    n = import_session_file(str(fixture_session_file))
    assert n == 0
