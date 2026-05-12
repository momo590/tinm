"""Ablation study on TINM-lite: vary anchor_alpha to identify which mechanism matters.

Variants:
  rag_baseline   : reference (no memory, query-only retrieval)
  tinm_a100      : anchor frozen at Q1 (no EMA update)
  tinm_a085      : slow EMA — current default
  tinm_a050      : medium EMA
  tinm_a000      : no memory carry (anchor = current query each turn)

Hypothesis: a085 and a100 should perform similarly (both protect against
distractor pollution). a050 should degrade slightly. a000 should collapse
toward rag_baseline (and possibly below it on distractor tasks).

Usage:
    .venv/bin/python -m runs.ablations           # full ablation (50 tasks × 5 agents)
    .venv/bin/python -m runs.ablations --smoke   # 4 tasks
"""
import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

import anthropic
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.rag_baseline import RAGBaselineAgent
from agents.tinm_lite import TINMLiteAgent
from config import Config
from env.encoder import TfidfEncoder
from env.graph_generator import generate_graph
from env.task_generator import generate_tasks
from eval.metrics import SessionMetrics
from runs.pilot import build_turn_sequence, run_agent_on_task


def run_ablations(cfg: Config, smoke: bool = False) -> dict[str, list[SessionMetrics]]:
    if smoke:
        cfg = Config(**{**asdict(cfg), "n_tasks": 4})

    print(f"[1/5] Initializing TF-IDF encoder...")
    encoder = TfidfEncoder()

    print(f"[2/5] Generating graph ({cfg.n_nodes} nodes, {cfg.n_topics} topics)...")
    graph = generate_graph(
        n_nodes=cfg.n_nodes,
        n_topics=cfg.n_topics,
        encoder=encoder,
        seed=cfg.seed,
        intra_topic_edge_prob=cfg.intra_topic_edge_prob,
        cross_topic_edge_prob=cfg.cross_topic_edge_prob,
    )
    print(f"      -> {len(graph.nodes)} nodes")

    print(f"[3/5] Generating tasks ({cfg.n_tasks}) with co-referential subQs...")
    tasks = generate_tasks(
        graph=graph,
        n_tasks=cfg.n_tasks,
        facts_min=cfg.facts_per_task_min,
        facts_max=cfg.facts_per_task_max,
        n_subq=cfg.n_subquestions,
        encoder=encoder,
        seed=cfg.seed,
        hard_coref_probability=cfg.hard_coref_probability,
    )

    print(f"[4/5] Building turn sequences (distractor p={cfg.distractor_probability})...")
    rng_dist = np.random.default_rng(cfg.seed + 7)
    turn_sequences = [
        build_turn_sequence(t, tasks, rng_dist, cfg.distractor_probability)
        for t in tasks
    ]
    n_with_distractor = sum(any(ts.is_distractor for ts in seq) for seq in turn_sequences)
    print(f"      -> {n_with_distractor}/{len(tasks)} tasks have a distractor")

    print(f"[5/5] Running ablation agents...")
    common = dict(
        nodes=graph.nodes,
        top_k=cfg.rag_top_k,
        model=cfg.llm_model,
        max_tokens=cfg.max_tokens,
    )
    agents = {
        "rag_baseline": RAGBaselineAgent(**common),
        "tinm_a100": TINMLiteAgent(**common, anchor_alpha=1.00, w_query=0.5, w_anchor=0.5),
        "tinm_a085": TINMLiteAgent(**common, anchor_alpha=0.85, w_query=0.5, w_anchor=0.5),
        "tinm_a050": TINMLiteAgent(**common, anchor_alpha=0.50, w_query=0.5, w_anchor=0.5),
        "tinm_a000": TINMLiteAgent(**common, anchor_alpha=0.00, w_query=0.5, w_anchor=0.5),
    }
    judge_client = anthropic.Anthropic()

    results: dict[str, list[SessionMetrics]] = {name: [] for name in agents}
    t0 = time.time()
    for agent_name, agent in agents.items():
        print(f"\n--- {agent_name} ---")
        for i, (task, turn_seq) in enumerate(zip(tasks, turn_sequences)):
            m = run_agent_on_task(agent, task, turn_seq, encoder, judge_client, cfg.judge_model)
            results[agent_name].append(m)
            print(
                f"  [{i + 1:>3}/{len(tasks)}] q={m.quality_score:.2f} "
                f"distr={'Y' if m.had_distractor else 'N'} "
                f"tok={m.task_input_tokens}+{m.task_output_tokens}"
            )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    _report_ablations(results)
    return results


def _report_ablations(results: dict[str, list[SessionMetrics]]) -> None:
    print("\n" + "=" * 90)
    print(f"{'Agent':<16} {'Quality':>9} {'Median':>9} {'Q_dist':>9} {'Q_nodist':>10} {'Δ_dist':>9} {'Tokens':>10} {'Q/1k':>8}")
    print("-" * 90)
    for name, metrics in results.items():
        n = len(metrics)
        if n == 0:
            continue
        qs = [m.quality_score for m in metrics]
        q_with = [m.quality_score for m in metrics if m.had_distractor]
        q_without = [m.quality_score for m in metrics if not m.had_distractor]
        total_tok = sum(
            sum(m.subquestion_input_tokens) + sum(m.subquestion_output_tokens)
            + sum(m.distractor_input_tokens) + sum(m.distractor_output_tokens)
            for m in metrics
        )
        mean_q = statistics.mean(qs)
        med_q = statistics.median(qs)
        mean_with = statistics.mean(q_with) if q_with else float("nan")
        mean_without = statistics.mean(q_without) if q_without else float("nan")
        delta = (mean_without - mean_with) if q_with and q_without else float("nan")
        print(
            f"{name:<16} {mean_q:>9.3f} {med_q:>9.3f} {mean_with:>9.3f} "
            f"{mean_without:>10.3f} {delta:>+9.3f} {total_tok:>10} {1000*sum(qs)/total_tok:>8.3f}"
        )


def _save_results(results: dict[str, list[SessionMetrics]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {}
    for agent_name, metrics in results.items():
        agent_list = []
        for m in metrics:
            d = asdict(m)
            d["subquestion_nodes_visited"] = [
                [int(x) for x in v] for v in m.subquestion_nodes_visited
            ]
            agent_list.append(d)
        serializable[agent_name] = agent_list
    path.write_text(json.dumps(serializable, indent=2))
    print(f"Saved results to {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="Run 4 tasks only")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "ablation_results.json",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    cfg = Config()
    results = run_ablations(cfg, smoke=args.smoke)
    _save_results(results, args.out)


if __name__ == "__main__":
    main()
