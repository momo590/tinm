"""Tests for tinm_assistant_capture.

Covers T13-T22, T28, T32, T35-T37 from the design doc.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture
def tinm_tmp(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="tinm-test-")
    monkeypatch.setenv("TINM_HOME", tmp)
    monkeypatch.setenv("TINM_PCP_DIR", str(pathlib.Path(tmp) / "pcp"))
    # Disable telemetry side-effects during tests.
    monkeypatch.delenv("TINM_TELEMETRY_DISABLED", raising=False)
    for mod in [
        "tinm_paths",
        "tinm_telemetry",
        "tinm_scoring",
        "tinm_artifact",
        "tinm_assistant_capture",
    ]:
        sys.modules.pop(mod, None)
    yield pathlib.Path(tmp)


def _init_thread(pcp_dir: pathlib.Path, thread_id: str) -> None:
    (pcp_dir / "threads").mkdir(parents=True, exist_ok=True)
    (pcp_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (pcp_dir / "threads" / f"{thread_id}.json").write_text(json.dumps({
        "thread_id": thread_id, "trajectory": []
    }))
    (pcp_dir / "artifacts" / f"{thread_id}.json").write_text(json.dumps({
        "thread_id": thread_id, "artifacts": []
    }))


class TestExtractFinalText:
    def test_strips_thinking_blocks(self, tinm_tmp):
        from tinm_assistant_capture import extract_final_text
        text = "<thinking>secret reasoning</thinking>\nVoici la réponse."
        assert extract_final_text(text) == "Voici la réponse."

    def test_preserves_prose(self, tinm_tmp):
        from tinm_assistant_capture import extract_final_text
        assert extract_final_text("Plain prose.") == "Plain prose."

    def test_empty_returns_empty(self, tinm_tmp):
        from tinm_assistant_capture import extract_final_text
        assert extract_final_text("") == ""
        assert extract_final_text("<thinking>only</thinking>") == ""


class TestBufferRoundtrip:
    def test_write_and_read(self, tinm_tmp):
        from tinm_assistant_capture import read_buffer, write_buffer
        ok = write_buffer("t1", "sess1", "Hello world", anchor_terms=["hello"])
        assert ok
        buf = read_buffer("sess1")
        assert buf is not None
        assert buf["assistant_text"] == "Hello world"
        assert buf["thread_id"] == "t1"
        assert buf["session_id"] == "sess1"

    def test_tool_only_turn_skips(self, tinm_tmp):
        from tinm_assistant_capture import read_buffer, write_buffer
        ok = write_buffer("t1", "sess1", "")
        assert not ok
        assert read_buffer("sess1") is None

    def test_clear_buffer(self, tinm_tmp):
        from tinm_assistant_capture import clear_buffer, read_buffer, write_buffer
        write_buffer("t1", "sess1", "Some text")
        clear_buffer("sess1")
        assert read_buffer("sess1") is None

    def test_multi_session_isolation(self, tinm_tmp):
        """T37 — concurrent sessions on same host don't collide."""
        from tinm_assistant_capture import read_buffer, write_buffer
        write_buffer("t1", "sessA", "Text A")
        write_buffer("t1", "sessB", "Text B")
        assert read_buffer("sessA")["assistant_text"] == "Text A"
        assert read_buffer("sessB")["assistant_text"] == "Text B"


class TestScoreAndFlush:
    def test_no_buffer(self, tinm_tmp):
        from tinm_assistant_capture import score_and_flush
        r = score_and_flush("t1", "sess1", "OK")
        assert r["decision"] == "no_buffer"

    def test_approved_creates_artifact(self, tinm_tmp):
        _init_thread(tinm_tmp / "pcp", "t1")
        from tinm_assistant_capture import score_and_flush, write_buffer
        write_buffer("t1", "sess1", "Voici ma recommandation détaillée.")
        r = score_and_flush("t1", "sess1", "C'est top!", next_user_turn=12)
        assert r["decision"] == "approved"

        art = json.loads((tinm_tmp / "pcp" / "artifacts" / "t1.json").read_text())
        assert len(art["artifacts"]) == 1
        entry = art["artifacts"][0]
        assert entry["source"] == "approved_exchange"
        assert entry["approval"]["signal_w"] == 0.9
        assert entry["approval"]["next_user_turn"] == 12

    def test_rejected_appends_log(self, tinm_tmp):
        _init_thread(tinm_tmp / "pcp", "t1")
        from tinm_assistant_capture import score_and_flush, write_buffer
        write_buffer("t1", "sess1", "Ship telemetry first.")
        r = score_and_flush("t1", "sess1", "Le pain n'est pas là",
                            next_user_turn=30)
        assert r["decision"] == "rejected"

        log = (tinm_tmp / "pcp" / "rejected" / "t1.jsonl").read_text().splitlines()
        assert len(log) == 1
        entry = json.loads(log[0])
        assert "Ship telemetry first" in entry["rejected_assistant_text"]
        assert entry["signal_w"] == -0.8

    def test_neutral_appends_log_compact(self, tinm_tmp):
        _init_thread(tinm_tmp / "pcp", "t1")
        from tinm_assistant_capture import score_and_flush, write_buffer
        write_buffer("t1", "sess1", "Some neutral response.")
        r = score_and_flush("t1", "sess1", "Comment ça marche ?")
        assert r["decision"] == "neutral"

        log = (tinm_tmp / "pcp" / "assistant_log" / "t1.jsonl").read_text().splitlines()
        assert len(log) == 1

    def test_stale_buffer_discarded(self, tinm_tmp, monkeypatch):
        _init_thread(tinm_tmp / "pcp", "t1")
        from tinm_assistant_capture import (
            read_buffer,
            score_and_flush,
            write_buffer,
        )
        write_buffer("t1", "sess1", "Stale text")
        # Forge an old timestamp.
        buf_path = list((tinm_tmp / "buffer").glob("*sess1.json"))[0]
        payload = json.loads(buf_path.read_text())
        payload["ts"] = time.time() - 600  # 10 min ago
        buf_path.write_text(json.dumps(payload))

        r = score_and_flush("t1", "sess1", "C'est top!")
        assert r["decision"] == "stale_discard"
        # Buffer is cleared
        assert read_buffer("sess1") is None

    def test_thread_mismatch_orphan(self, tinm_tmp):
        _init_thread(tinm_tmp / "pcp", "t1")
        from tinm_assistant_capture import score_and_flush, write_buffer
        write_buffer("t-other", "sess1", "Some text")
        r = score_and_flush("t1", "sess1", "C'est top!")
        assert r["decision"] == "thread_mismatch"

    def test_buffer_always_cleared_after_score(self, tinm_tmp):
        _init_thread(tinm_tmp / "pcp", "t1")
        from tinm_assistant_capture import read_buffer, score_and_flush, write_buffer
        write_buffer("t1", "sess1", "Some text")
        score_and_flush("t1", "sess1", "OK")
        assert read_buffer("sess1") is None


class TestRejectedLogCap:
    def test_rollover_at_100(self, tinm_tmp):
        """T28 — Rejected log rollover at N=100."""
        from tinm_assistant_capture import REJECTED_LOG_CAP, append_rejected
        for i in range(REJECTED_LOG_CAP + 5):
            append_rejected("t1", {"ts": f"2026-05-{i:02d}", "i": i})

        log_path = tinm_tmp / "pcp" / "rejected" / "t1.jsonl"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(entries) == REJECTED_LOG_CAP
        # Oldest 5 dropped; first surviving is entry index 5.
        assert entries[0]["i"] == 5
        assert entries[-1]["i"] == REJECTED_LOG_CAP + 4


class TestAssistantLogCap:
    def test_rollover_at_20(self, tinm_tmp):
        from tinm_assistant_capture import ASSISTANT_LOG_CAP, append_assistant_log
        for i in range(ASSISTANT_LOG_CAP + 5):
            append_assistant_log("t1", {"ts": f"2026-05-{i:02d}", "i": i})

        log_path = tinm_tmp / "pcp" / "assistant_log" / "t1.jsonl"
        entries = [
            json.loads(line) for line in log_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(entries) == ASSISTANT_LOG_CAP
        assert entries[0]["i"] == 5


class TestArtifactIdSafety:
    def test_id_generated_safe(self, tinm_tmp):
        from tinm_assistant_capture import _artifact_id_for
        aid = _artifact_id_for("my-thread-id", "2026-05-14T22:00:00Z")
        # No colons, no T, no Z, no slashes
        assert ":" not in aid
        assert "/" not in aid
        assert aid.startswith("auto_")
