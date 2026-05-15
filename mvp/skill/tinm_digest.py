"""TINM digest generator — compresses long sessions via Haiku 4.5.

Triggered when session token usage crosses TINM_DIGEST_TOKEN_THRESHOLD (default: 40000).
Runs as async background subprocess to keep UserPromptSubmit hook non-blocking.
Output written to ~/.tinm/buffer/digest-<session_id>-<turn_count>.txt.

Format of digest file:
    TINM_DIGEST_V1
    session_id: <id>
    turn_count: <N>
    ---
    <digest text, ~800 tokens>

user_prompt.sh reads the file, checks turn_count matches (turn_count+1 == current),
prepends "[TINM digest: ...]" to prompt context, then deletes the file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

DIGEST_TOKEN_THRESHOLD = int(os.environ.get("TINM_DIGEST_TOKEN_THRESHOLD", "40000"))
HAIKU_MODEL = "claude-haiku-4-5-20251001"
BUFFER_DIR = Path.home() / ".tinm" / "buffer"


def _estimate_tokens_from_transcript(transcript_path: str) -> int:
    """Estimate token count from transcript JSONL line count * avg tokens per line."""
    try:
        p = Path(transcript_path)
        if not p.is_file():
            return 0
        line_count = sum(1 for _ in p.open() if _.strip())
        # Empirical: ~400 tokens per JSONL line in typical CC transcripts
        return line_count * 400
    except Exception:
        return 0


def should_trigger_digest(
    transcript_path: str,
    session_id: str,
    threshold: int = DIGEST_TOKEN_THRESHOLD,
) -> bool:
    """True if session token estimate exceeds threshold and no digest pending."""
    est = _estimate_tokens_from_transcript(transcript_path)
    if est < threshold:
        return False
    # Check if a digest is already buffered (pending injection)
    existing = list(BUFFER_DIR.glob(f"digest-{session_id}-*.txt"))
    if existing:
        return False  # already generating or already pending
    return True


def launch_digest_async(
    session_id: str,
    thread_id: str,
    transcript_path: str,
    turn_count: int,
) -> None:
    """Launch digest generation as a detached background subprocess."""
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    out_file = BUFFER_DIR / f"digest-{session_id}-{turn_count}.txt"
    script = Path(__file__).parent / "_digest_worker.py"
    if not script.exists():
        _write_digest_worker(script)
    venv_py = Path.home() / ".tinm" / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable
    subprocess.Popen(
        [py, str(script), session_id, thread_id, transcript_path, str(turn_count), str(out_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def get_pending_digest(session_id: str, current_turn: int) -> Optional[str]:
    """Return digest text if a valid digest exists for current_turn-1, else None."""
    target = BUFFER_DIR / f"digest-{session_id}-{current_turn - 1}.txt"
    if not target.is_file():
        return None
    try:
        content = target.read_text()
        lines = content.splitlines()
        if not lines or lines[0] != "TINM_DIGEST_V1":
            return None
        # Parse header
        header = {}
        sep = -1
        for i, line in enumerate(lines[1:], 1):
            if line == "---":
                sep = i
                break
            if ":" in line:
                k, v = line.split(":", 1)
                header[k.strip()] = v.strip()
        if sep < 0:
            return None
        stored_turn = int(header.get("turn_count", -1))
        if stored_turn != current_turn - 1:
            return None  # stale
        digest_text = "\n".join(lines[sep + 1:]).strip()
        target.unlink(missing_ok=True)
        return digest_text
    except Exception:
        return None


def _write_digest_worker(path: Path) -> None:
    """Write the background worker script."""
    path.write_text(
        '#!/usr/bin/env python3\n'
        '"""Background worker: generates a TINM digest via Haiku 4.5 and writes it to a file."""\n'
        'import json\n'
        'import os\n'
        'import sys\n'
        'from pathlib import Path\n'
        '\n'
        '\n'
        'def main():\n'
        '    if len(sys.argv) < 6:\n'
        '        sys.exit(1)\n'
        '    session_id, thread_id, transcript_path, turn_count_str, out_file = sys.argv[1:6]\n'
        '    turn_count = int(turn_count_str)\n'
        '\n'
        '    # Read last N turns from transcript\n'
        '    transcript = Path(transcript_path)\n'
        '    if not transcript.is_file():\n'
        '        sys.exit(0)\n'
        '\n'
        '    turns = []\n'
        '    with transcript.open() as f:\n'
        '        for line in f:\n'
        '            line = line.strip()\n'
        '            if not line:\n'
        '                continue\n'
        '            try:\n'
        '                msg = json.loads(line)\n'
        '            except json.JSONDecodeError:\n'
        '                continue\n'
        '            inner = msg.get("message") if isinstance(msg.get("message"), dict) else msg\n'
        '            role = inner.get("role", "")\n'
        '            content = inner.get("content", "")\n'
        '            if isinstance(content, list):\n'
        '                parts = [b.get("text", "") for b in content\n'
        '                         if isinstance(b, dict) and b.get("type") == "text"]\n'
        '                content = " ".join(parts)\n'
        '            if role in ("user", "assistant") and content.strip():\n'
        '                turns.append(f"{role.upper()}: {content[:500]}")\n'
        '\n'
        '    if not turns:\n'
        '        sys.exit(0)\n'
        '\n'
        '    # Take last 40 turns\n'
        '    recent = turns[-40:]\n'
        '    transcript_text = "\\n\\n".join(recent)\n'
        '\n'
        '    # Call Haiku\n'
        '    try:\n'
        '        import anthropic\n'
        '    except ImportError:\n'
        '        sys.exit(0)\n'
        '\n'
        '    api_key = os.environ.get("ANTHROPIC_API_KEY", "")\n'
        '    if not api_key:\n'
        '        sys.exit(0)\n'
        '\n'
        '    client = anthropic.Anthropic(api_key=api_key)\n'
        '    prompt = (\n'
        '        "You are compressing a Claude Code session into a compact digest.\\n"\n'
        '        "Below are the last ~40 turns of the session. Produce a ~600-800 token digest that:\\n"\n'
        '        "1. Lists the key decisions made (bullet points)\\n"\n'
        '        "2. Lists the key artifacts/files created or modified (with one-line descriptions)\\n"\n'
        '        "3. Identifies the current task/goal in 1-2 sentences\\n"\n'
        '        "4. Notes any approaches that were tried and REJECTED (so they are not repeated)\\n\\n"\n'
        '        "Be terse. No fluff. This digest will be prepended to the next turn so Claude knows what happened.\\n\\n"\n'
        '        f"SESSION TRANSCRIPT (last ~40 turns):\\n{transcript_text}\\n\\nDIGEST:"\n'
        '    )\n'
        '\n'
        '    try:\n'
        '        response = client.messages.create(\n'
        '            model="claude-haiku-4-5-20251001",\n'
        '            max_tokens=900,\n'
        '            messages=[{"role": "user", "content": prompt}],\n'
        '        )\n'
        '        digest = response.content[0].text\n'
        '    except Exception:\n'
        '        sys.exit(0)\n'
        '\n'
        '    # Write output file\n'
        '    out = Path(out_file)\n'
        '    out.parent.mkdir(parents=True, exist_ok=True)\n'
        '    out.write_text(\n'
        '        f"TINM_DIGEST_V1\\n"\n'
        '        f"session_id: {session_id}\\n"\n'
        '        f"turn_count: {turn_count}\\n"\n'
        '        f"---\\n"\n'
        '        f"{digest}\\n"\n'
        '    )\n'
        '\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )
    path.chmod(0o755)
