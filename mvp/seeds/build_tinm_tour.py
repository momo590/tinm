"""Build the `tinm-tour` seed thread + artifacts JSON files.

This script is a dev tool. End users never run it; they get the
pre-built JSON via the installer + `/tinm demo`. Re-run after editing
the inline source data, or after the embedding model changes:

    ~/.tinm/.venv/bin/python mvp/seeds/build_tinm_tour.py

Writes:
    mvp/seeds/tinm-tour/thread.json
    mvp/seeds/tinm-tour/artifacts.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sentence_transformers import SentenceTransformer

PCP_VERSION = "0.1"
THREAD_ID = "tinm-tour"
EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
ALPHA = 0.85  # EMA decay used by tinm_update

# A short, real walkthrough of the founder shipping Phase 1 of the TINM paper.
# Pulled from the actual `tinm-paper-polish` thread so the install demo
# reflects how a real user uses TINM, not a fabricated marketing scene.
TURNS = [
    {
        "role": "user",
        "text": "Let's launch the additional experiments: 2Wiki and MuSiQue n=100",
    },
    {
        "role": "assistant",
        "text": "Both started. ETA ~45min for 2WikiMultihopQA, ~90min for MuSiQue. I'll pull results when done.",
    },
    {
        "role": "user",
        "text": "I pick 2WikiMultihopQA as the fifth benchmark",
    },
    {
        "role": "assistant",
        "text": "Locked in. 2WikiMultihopQA n=50 results are in pilot_wiki2hop_results.json: tinm_a085 0.481 best, +0.114 over rag_baseline (t=4.03). I updated the Pareto plot (fig 1) to show the new panel; tinm_a085 is now Pareto-best on 4 of 5 panels.",
    },
    {
        "role": "user",
        "text": "Modify the figure like we did for it before",
    },
    {
        "role": "assistant",
        "text": "Done. fig1_pareto.pdf rebuilt from table_pareto.csv with the new 2Wiki panel using the same color scheme as the prior fig 1 commit.",
    },
    {
        "role": "user",
        "text": "Phase 1 livrée. Post-merge dans main, ma nouvelle session Claude Code via tinm.artifact_find a sorti +0.114 t=4.03 sur 2WikiMultihopQA comme effet absolu max — sans avoir aucun fichier à lire, juste via l'artifact stocké. Première démo concrète que TINM apporte une valeur que la mémoire native n'a pas.",
    },
    {
        "role": "assistant",
        "text": "Locked. That's the N=1 demo. ~6000-9000 tokens saved on a single resumed question, no file reads, no manual context paste. The 'recall the biggest effect' question is the canonical TINM whoa moment.",
    },
]

# Artifacts — the entries TINM surfaces when a relevant query arrives.
# Each ref points at a file that exists in the TNIM repo so the install
# user can click through if they want.
ARTIFACTS = [
    {
        "id": "pareto-plot",
        "name": "Figure 1 Pareto plot",
        "aliases": ["the figure", "fig 1", "pareto plot", "the pareto"],
        "turn_first_mentioned": 4,
        "ref": "paper/figures/fig1_pareto.pdf",
        "summary": (
            "Quality vs token cost across 5 benchmarks; tinm_a085 is "
            "Pareto-best on 4 of 5 panels. Rendered by "
            "paper/figures/make_fig1.py from table_pareto.csv."
        ),
    },
    {
        "id": "wiki2hop-results",
        "name": "2WikiMultihopQA n=50 results",
        "aliases": [
            "2wiki results",
            "2wikimultihopqa",
            "wiki2hop",
            "the biggest absolute effect",
            "biggest effect we measured",
        ],
        "turn_first_mentioned": 4,
        "ref": "benchmark/runs/pilot_wiki2hop_results.json",
        "summary": (
            "Per-task metrics for the 4 agents on 2WikiMultihopQA n=50. "
            "tinm_a085 0.481 best; +0.114 absolute lift over rag_baseline "
            "(t=4.03, paired). This is the largest measured effect in the "
            "Phase 1 pilot set."
        ),
    },
    {
        "id": "tinm-substrate",
        "name": "TINM substrate doc — anchor + EMA mechanism",
        "aliases": ["tinm mechanism", "anchor ema", "the substrate"],
        "turn_first_mentioned": 1,
        "ref": "tinm_substrate.md",
        "summary": (
            "Compressed latent state z_t = anchor (EMA-α of embeddings of "
            "user turns). α=0.85. Trajectory persists in PCP v0 thread; "
            "anchor + top_terms drive artifact_find ranking."
        ),
    },
    {
        "id": "phase1-tokens-saved",
        "name": "Phase 1 tokens-saved measurement (N=1)",
        "aliases": [
            "tokens saved",
            "the savings",
            "the founder N=1",
            "how much did tinm save",
        ],
        "turn_first_mentioned": 7,
        "ref": "tinm_substrate.md",
        "summary": (
            "Self-reported by the founder on the canonical resume question "
            "('what was the biggest effect we measured?'): ~6000-9000 "
            "tokens saved vs the alternatives (re-paste, expanded "
            "CLAUDE.md, or re-reading paper_section.md). 5-10x cheaper."
        ),
    },
    {
        "id": "pcp-spec",
        "name": "PCP v0 spec — vendor-neutral thread format",
        "aliases": ["pcp", "the spec", "the open standard"],
        "turn_first_mentioned": 1,
        "ref": "mvp/pcp_v0_spec.md",
        "summary": (
            "File-based JSON. Two files per thread (trajectory + "
            "artifacts). Stable thread_id across vendors. Designed so "
            "Claude Code, openClaw, Cursor, and Claude Desktop can read "
            "the same thread."
        ),
    },
]


def utcnow() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def build() -> None:
    out_dir = Path(__file__).resolve().parent / THREAD_ID
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"loading {EMBED_MODEL_NAME} (~30s first time)...", file=sys.stderr)
    model = SentenceTransformer(EMBED_MODEL_NAME)

    # Anchor: EMA over user turns only (matches tinm_update behaviour).
    user_texts = [t["text"] for t in TURNS if t["role"] == "user"]
    user_embeds = model.encode(user_texts, normalize_embeddings=False)
    anchor = user_embeds[0].astype(float).tolist()
    for vec in user_embeds[1:]:
        anchor = [a * ALPHA + v * (1 - ALPHA) for a, v in zip(anchor, vec.tolist())]

    # Top terms — a simple bag-of-words frequency over user turns.
    from collections import Counter
    import re as _re

    stop = {
        "the","a","an","is","are","was","were","of","to","in","on","for","and",
        "or","but","with","as","by","at","from","this","that","these","those",
        "we","i","you","my","our","your","it","its","be","been","being",
        "le","la","les","de","des","du","et","ou","pour","dans","sur","avec",
        "sans","est","sont","une","un","aux","au","ce","ces","cette","ces",
        "que","qui","quoi","comment","par","tres","apres","just","what","when",
        "via","sans","do","did","does","they","them","then","now","not",
    }
    words = []
    for t in user_texts:
        for w in _re.findall(r"[a-zA-Z0-9_-]+", t.lower()):
            if len(w) > 2 and w not in stop:
                words.append(w)
    top_terms = [w for w, _ in Counter(words).most_common(10)]

    thread_doc = {
        "pcp_version": PCP_VERSION,
        "thread_id": THREAD_ID,
        "metadata": {
            "title": "TINM tour — Phase 1 paper walkthrough (demo seed)",
            "created_at": utcnow(),
            "last_updated": utcnow(),
            "embedding_model": EMBED_MODEL_NAME,
            "embedding_dim": EMBED_DIM,
            "project_root": "~/TNIM (demo)",
            "client_history": ["claude-code"],
            "note": (
                "Demo seed installed via `/tinm demo`. Try asking 'what was "
                "the biggest effect we measured?' to see TINM surface the "
                "wiki2hop-results artifact without reading any file."
            ),
        },
        "anchor": {
            "vector": anchor,
            "alpha": ALPHA,
            "turns_seen": len(user_texts),
        },
        "top_terms": top_terms,
        "trajectory": [
            {
                "turn": idx + 1,
                "role": t["role"],
                "text": t["text"],
                "client": "claude-code",
                "ts": utcnow(),
            }
            for idx, t in enumerate(TURNS)
        ],
    }

    # Embed each artifact's name + summary for the cosine fallback.
    artifact_texts = [f"{a['name']}. {a['summary']}" for a in ARTIFACTS]
    artifact_embeds = model.encode(artifact_texts, normalize_embeddings=False)
    artifacts_doc = {
        "pcp_version": PCP_VERSION,
        "thread_id": THREAD_ID,
        "artifacts": [
            {**a, "embedding": vec.astype(float).tolist()}
            for a, vec in zip(ARTIFACTS, artifact_embeds)
        ],
    }

    (out_dir / "thread.json").write_text(json.dumps(thread_doc, indent=2) + "\n")
    (out_dir / "artifacts.json").write_text(json.dumps(artifacts_doc, indent=2) + "\n")
    print(f"wrote {out_dir}/thread.json + artifacts.json", file=sys.stderr)


if __name__ == "__main__":
    build()
