"""tinm_conv_add.py — CLI shim for user_prompt.sh to index a conversation turn.

Usage:
    printf '%s' "$PROMPT_TEXT" | python tinm_conv_add.py <session_id> <turn_id> <role>

Reads text from stdin so shell quoting around arbitrary user text never
becomes a correctness hazard. Exits silently on any error — hook failures
must never interrupt the user's prompt flow.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    session_id = sys.argv[1]
    turn_id = int(sys.argv[2])
    role = sys.argv[3]
except (IndexError, ValueError):
    sys.exit(0)

try:
    text = sys.stdin.read()
    from tinm_conv_index import add_turn
    # v0.3.0: defer embedding to the async anchor worker so this call
    # returns in < 100ms even on the SentenceTransformer cold-import
    # path. The worker calls backfill_embeddings() after the model loads.
    add_turn(session_id, turn_id, role, text, defer_embedding=True)
except Exception:
    sys.exit(0)
