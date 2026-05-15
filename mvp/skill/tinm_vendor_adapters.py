"""Generic vendor adapter registry — DRY normalizer for hook-based vendors.

Every vendor adapter (tinm_cursor.py, tinm_openclaw.py, tinm_windsurf.py, ...)
delegates JSON normalization to this module. To add a new vendor:

  1. Add an entry to VENDORS dict below with field-name candidates
  2. Create a thin wrapper module `tinm_<vendor>.py` with `normalize(payload)`
     that just calls `tinm_vendor_adapters.normalize_for("<vendor>", payload)`
  3. Create a shell hook `mvp/hooks/<vendor>_hook.sh` following the cursor pattern

Output schema (canonical TINM UserPromptSubmit format):
  {
    "prompt": str,           # the user's message
    "session_id": str,       # session identifier (generated if missing)
    "transcript_path": str,  # path to transcript JSONL ("" if unavailable)
    "hook_type": str         # vendor-specific tag, e.g. "cursor_beforeSubmitPrompt"
  }
"""
from __future__ import annotations

import uuid
from typing import Optional


# ── Vendor field-mapping registry ──────────────────────────────────────────
# Each entry: prompt_fields (in priority order), session_fields, hook_type
# All vendors are [BETA-unverified] until tested against a real install.

VENDORS: dict[str, dict] = {
    "cursor": {
        "prompt_fields": ["prompt", "text", "message", "input", "query", "content"],
        "session_fields": ["session_id", "sessionId", "session", "conversation_id", "conversationId"],
        "transcript_fields": [],
        "hook_type": "cursor_beforeSubmitPrompt",
    },
    "openclaw": {
        # OpenClaw plugin manifest exposes incoming messages via stdin JSON.
        # Schema based on the documented plugin-system structure (opencanvasdc).
        "prompt_fields": ["message", "text", "input", "prompt", "user_message", "content"],
        "session_fields": ["session_id", "thread_id", "conversation_id", "context_id"],
        "transcript_fields": ["transcript_path", "history_path", "log_path"],
        "hook_type": "openclaw_plugin",
    },
    "windsurf": {
        # Windsurf (Cascade) hook surface — derived from Cursor lineage.
        # The "agent" mode passes JSON before-submit similar to Cursor.
        "prompt_fields": ["prompt", "text", "user_input", "message", "query"],
        "session_fields": ["session_id", "cascade_id", "agent_id", "sessionId"],
        "transcript_fields": ["transcript_path"],
        "hook_type": "windsurf_beforeAgent",
    },
    "cline": {
        # Cline (formerly Claude Dev) — VS Code extension. The hook is
        # via VS Code task notifications. The task object contains
        # taskId + user message.
        "prompt_fields": ["text", "message", "prompt", "userMessage", "user_message"],
        "session_fields": ["taskId", "task_id", "id", "session_id"],
        "transcript_fields": ["taskHistoryPath", "history_path"],
        "hook_type": "cline_vscode",
    },
    "aider": {
        # Aider CLI — captured via the wrap-the-binary pattern.
        # JSON from tinm_cli_wrap.py with stdin/stdout pairs.
        "prompt_fields": ["input", "prompt", "user_input"],
        "session_fields": ["session_id", "chat_id"],
        "transcript_fields": ["chat_history_file"],  # default: .aider.chat.history.md
        "hook_type": "aider_cli",
    },
    "codex": {
        # OpenAI Codex CLI — similar wrap-the-binary pattern.
        "prompt_fields": ["input", "prompt", "message", "user_message"],
        "session_fields": ["conversation_id", "session_id", "thread_id"],
        "transcript_fields": [],
        "hook_type": "openai_codex_cli",
    },
    "continue": {
        # Continue.dev — VS Code/JetBrains extension. Uses .continue/ config dir.
        "prompt_fields": ["text", "message", "prompt", "input"],
        "session_fields": ["sessionId", "session_id", "id"],
        "transcript_fields": ["sessionHistoryPath"],
        "hook_type": "continue_dev",
    },
    "clipboard": {
        # Clipboard capture — opt-in via "# TINM SAVE" trigger.
        # Not really a hook; the payload comes from tinm_clipboard.py.
        "prompt_fields": ["text"],
        "session_fields": ["session_id"],
        "transcript_fields": [],
        "hook_type": "clipboard_opt_in",
    },
}


def _extract_field(payload: dict, candidates: list[str]) -> Optional[str]:
    """Try each candidate field name. Supports one level of nesting."""
    for field in candidates:
        val = payload.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
        # Handle nested: {"message": {"text": "..."}}
        if isinstance(val, dict):
            for sub in candidates:
                sub_val = val.get(sub)
                if isinstance(sub_val, str) and sub_val.strip():
                    return sub_val.strip()
    return None


def normalize_for(vendor: str, payload: dict) -> Optional[dict]:
    """Normalize a vendor-specific hook payload to TINM's canonical schema.

    Args:
        vendor: vendor name (must be a key in VENDORS)
        payload: parsed JSON dict from the vendor's hook

    Returns:
        Canonical TINM dict, or None if no recognizable prompt field exists.
    """
    if vendor not in VENDORS:
        return None
    cfg = VENDORS[vendor]

    prompt_text = _extract_field(payload, cfg["prompt_fields"])
    if not prompt_text:
        return None

    session_id = _extract_field(payload, cfg["session_fields"])
    if not session_id:
        session_id = f"{vendor}-{uuid.uuid4().hex[:8]}"

    transcript_path = _extract_field(payload, cfg["transcript_fields"]) or ""

    return {
        "prompt": prompt_text,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "hook_type": cfg["hook_type"],
    }


def list_vendors() -> list[str]:
    """Return the list of supported vendor names."""
    return list(VENDORS.keys())
