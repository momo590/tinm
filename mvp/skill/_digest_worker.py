#!/usr/bin/env python3
"""Background worker: generates a TINM digest via Haiku 4.5 and writes it to a file."""
import json
import os
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 6:
        sys.exit(1)
    session_id, thread_id, transcript_path, turn_count_str, out_file = sys.argv[1:6]
    turn_count = int(turn_count_str)

    # Read last N turns from transcript
    transcript = Path(transcript_path)
    if not transcript.is_file():
        sys.exit(0)

    turns = []
    with transcript.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            inner = msg.get("message") if isinstance(msg.get("message"), dict) else msg
            role = inner.get("role", "")
            content = inner.get("content", "")
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(parts)
            if role in ("user", "assistant") and content.strip():
                turns.append(f"{role.upper()}: {content[:500]}")

    if not turns:
        sys.exit(0)

    # Take last 40 turns
    recent = turns[-40:]
    transcript_text = "\n\n".join(recent)

    # Call Haiku
    try:
        import anthropic
    except ImportError:
        sys.exit(0)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        sys.exit(0)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        "You are compressing a Claude Code session into a compact digest.\n"
        "Below are the last ~40 turns of the session. Produce a ~600-800 token digest that:\n"
        "1. Lists the key decisions made (bullet points)\n"
        "2. Lists the key artifacts/files created or modified (with one-line descriptions)\n"
        "3. Identifies the current task/goal in 1-2 sentences\n"
        "4. Notes any approaches that were tried and REJECTED (so they are not repeated)\n\n"
        "Be terse. No fluff. This digest will be prepended to the next turn so Claude knows what happened.\n\n"
        f"SESSION TRANSCRIPT (last ~40 turns):\n{transcript_text}\n\nDIGEST:"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        digest = response.content[0].text
    except Exception:
        sys.exit(0)

    # Write output file
    out = Path(out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"TINM_DIGEST_V1\n"
        f"session_id: {session_id}\n"
        f"turn_count: {turn_count}\n"
        f"---\n"
        f"{digest}\n"
    )


if __name__ == "__main__":
    main()
