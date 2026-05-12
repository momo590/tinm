"""Pilot v2: continuity + topic-shift benchmarks with 4 agents.

Agents:
  rag_baseline      — stateless, query-only retrieval (reference floor)
  rag_with_history  — history-as-context in LLM prompt (realistic memory baseline)
  tinm_a085         — fixed slow EMA (TINM-lite v1, for ablation reference)
  tinm_adapt        — friction-adaptive alpha (TINM-lite v2 default)

Benchmarks:
  CONTINUITY  — 50 tasks, n_subq=2, distractor between Q1 and Q2 with p=0.5
  TOPIC SHIFT — 50 tasks, n_subq=4, Q1+Q2 about topic A, Q3 pivot, Q4 hard coref B

Usage:
    .venv/bin/python -m runs.pilot_v2           # full (50 + 50)
    .venv/bin/python -m runs.pilot_v2 --smoke   # 4 + 4
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

from agents.base import Turn
from agents.rag_baseline import RAGBaselineAgent
from agents.rag_with_history import RAGWithHistoryAgent
from agents.tinm_lite import TINMLiteAgent
from config import Config
from env.encoder import TfidfEncoder
from env.graph_generator import generate_graph
from env.task_generator import Task, generate_shift_tasks, generate_tasks
from eval.metrics import SessionMetrics
from runs.pilot import TurnSpec, build_turn_sequence, run_agent_on_task


def build_shift_turn_sequence(task: Task) -> list[TurnSpec]:
    """No distractors in v1 of the shift benchmark — just the 4 task subqs."""
    return [TurnSpec(query=q, is_distractor=False, source_task_id=task.id) for q in task.subquestions]


def make_agents(graph_nodes, encoder, cfg: Config) -> dict:
    common = dict(
        nodes=graph_nodes,
        top_k=cfg.rag_top_k,
        model=cfg.llm_model,
        max_tokens=cfg.max_tokens,
    )
    return {
        "rag_baseline": RAGBaselineAgent(**common),
        "rag_with_history": RAGWithHistoryAgent(**common, encoder=encoder),
        "tinm_a085": TINMLiteAgent(
            **common,
            adaptive_alpha=False,
            anchor_alpha=0.85,
            w_query=0.5,
            w_anchor=0.5,
        ),
        "tinm_adapt": TINMLiteAgent(
            **common,
            adaptive_alpha=True,
            alpha_min=0.20,
            alpha_max=0.95,
            w_query=0.5,
            w_anchor=0.5,
        ),
    }


def run(cfg: Config, smoke: bool = False) -> dict[str, dict[str, list[SessionMetrics]]]:
    if smoke:
        cfg = Config(**{**asdict(cfg), "n_tasks": 4})

    print(f"[1/6] Initializing TF-IDF encoder...")
    encoder = TfidfEncoder()

    print(f"[2/6] Generating graph ({cfg.n_nodes} nodes, {cfg.n_topics} topics)...")
    graph = generate_graph(
        n_nodes=cfg.n_nodes,
        n_topics=cfg.n_topics,
        encoder=encoder,
        seed=cfg.seed,
        intra_topic_edge_prob=cfg.intra_topic_edge_prob,
        cross_topic_edge_prob=cfg.cross_topic_edge_prob,
    )
    print(f"      -> {len(graph.nodes)} nodes")

    print(f"[3/6] Generating continuity tasks ({cfg.n_tasks})...")
    tasks_c = generate_tasks(
        graph=graph,
        n_tasks=cfg.n_tasks,
        facts_min=cfg.facts_per_task_min,
        facts_max=cfg.facts_per_task_max,
        n_subq=cfg.n_subquestions,
        encoder=encoder,
        seed=cfg.seed,
        hard_coref_probability=cfg.hard_coref_probability,
    )
    rng_dist = np.random.default_rng(cfg.seed + 7)
    turn_seqs_c = [
        build_turn_sequence(t, tasks_c, rng_dist, cfg.distractor_probability)
        for t in tasks_c
    ]
    n_with_d = sum(any(ts.is_distractor for ts in seq) for seq in turn_seqs_c)
    print(f"      -> {n_with_d}/{len(tasks_c)} with distractor")

    print(f"[4/6] Generating topic-shift tasks ({cfg.n_tasks})...")
    tasks_s = generate_shift_tasks(graph, cfg.n_tasks, encoder, cfg.seed, facts_per_half=2)
    turn_seqs_s = [build_shift_turn_sequence(t) for t in tasks_s]

    judge_client = anthropic.Anthropic()
    results: dict[str, dict[str, list[SessionMetrics]]] = {"continuity": {}, "shift": {}}

    print(f"[5/6] Running CONTINUITY benchmark...")
    agents_c = make_agents(graph.nodes, encoder, cfg)
    t0 = time.time()
    for agent_name, agent in agents_c.items():
        print(f"\n  --- {agent_name} ---")
        results["continuity"][agent_name] = []
        for i, (task, ts) in enumerate(zip(tasks_c, turn_seqs_c)):
            m = run_agent_on_task(agent, task, ts, encoder, judge_client, cfg.judge_model)
            results["continuity"][agent_name].append(m)
            print(
                f"    [{i + 1:>3}/{len(tasks_c)}] q={m.quality_score:.2f} "
                f"distr={'Y' if m.had_distractor else 'N'} tok={m.task_input_tokens}"
            )

    print(f"\n[6/6] Running TOPIC-SHIFT benchmark...")
    agents_s = make_agents(graph.nodes, encoder, cfg)  # fresh agent state
    for agent_name, agent in agents_s.items():
        print(f"\n  --- {agent_name} ---")
        results["shift"][agent_name] = []
        for i, (task, ts) in enumerate(zip(tasks_s, turn_seqs_s)):
            m = run_agent_on_task(agent, task, ts, encoder, judge_client, cfg.judge_model)
            results["shift"][agent_name].append(m)
            print(
                f"    [{i + 1:>3}/{len(tasks_s)}] q={m.quality_score:.2f} "
                f"tok={m.task_input_tokens}"
            )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    _report_combined(results)
    return results


def _per_agent_stats(per_agent: dict[str, list[SessionMetrics]]) -> None:
    print(f"{'Agent':<22} {'Quality':>9} {'Median':>9} {'Q/1k_tok':>10} {'TotalTok':>10}")
    print("-" * 65)
    for name, metrics in per_agent.items():
        n = len(metrics)
        if n == 0:
            continue
        qs = [m.quality_score for m in metrics]
        total_tok = sum(
            sum(m.subquestion_input_tokens) + sum(m.subquestion_output_tokens)
            + sum(m.distractor_input_tokens) + sum(m.distractor_output_tokens)
            for m in metrics
        )
        mean_q = statistics.mean(qs)
        med_q = statistics.median(qs)
        qpkt = 1000 * sum(qs) / total_tok if total_tok else 0.0
        print(f"{name:<22} {mean_q:>9.3f} {med_q:>9.3f} {qpkt:>10.3f} {total_tok:>10}")


def _report_combined(results: dict[str, dict[str, list[SessionMetrics]]]) -> None:
    print("\n" + "=" * 70)
    print("=== CONTINUITY ===")
    _per_agent_stats(results["continuity"])

    # Continuity: with vs without distractor
    print(f"\nQuality with vs without distractor:")
    print(f"{'Agent':<22} {'with_distr':>11} {'no_distr':>11} {'Δ':>9}")
    for name, metrics in results["continuity"].items():
        q_with = [m.quality_score for m in metrics if m.had_distractor]
        q_without = [m.quality_score for m in metrics if not m.had_distractor]
        mean_with = statistics.mean(q_with) if q_with else float("nan")
        mean_without = statistics.mean(q_without) if q_without else float("nan")
        delta = (mean_without - mean_with) if q_with and q_without else float("nan")
        print(f"{name:<22} {mean_with:>11.3f} {mean_without:>11.3f} {delta:>+9.3f}")

    print("\n" + "=" * 70)
    print("=== TOPIC SHIFT ===")
    _per_agent_stats(results["shift"])


def _save_results(results, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable: dict = {}
    for bench_name, per_agent in results.items():
        serializable[bench_name] = {}
        for agent_name, metrics in per_agent.items():
            agent_list = []
            for m in metrics:
                d = asdict(m)
                d["subquestion_nodes_visited"] = [
                    [int(x) for x in v] for v in m.subquestion_nodes_visited
                ]
                agent_list.append(d)
            serializable[bench_name][agent_name] = agent_list
    path.write_text(json.dumps(serializable, indent=2))
    print(f"Saved results to {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="Run 4 tasks per benchmark")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "pilot_v2_results.json",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    cfg = Config()
    results = run(cfg, smoke=args.smoke)
    _save_results(results, args.out)


if __name__ == "__main__":
    main()
