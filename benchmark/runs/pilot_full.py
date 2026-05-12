"""Run TINM-full on all 4 benchmarks. Outputs into pilot_full_results.json.

Designed to be merged with existing pilot_v2_results.json and pilot_real_results*.json
for the final paper analysis.

Usage:
    .venv/bin/python -m runs.pilot_full --smoke      # 4 tasks per benchmark
    .venv/bin/python -m runs.pilot_full              # 50 tasks per benchmark
    .venv/bin/python -m runs.pilot_full --only continuity,shift
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

from agents.tinm_full import TINMFullAgent
from config import Config
from env.encoder import TfidfEncoder
from env.graph_generator import generate_graph
from env.musique_loader import load_musique_benchmark
from env.task_generator import generate_shift_tasks, generate_tasks
from eval.metrics import SessionMetrics
from runs.pilot import build_turn_sequence, run_agent_on_task
from runs.pilot_v2 import build_shift_turn_sequence


def _make_agent(graph_nodes, cfg: Config) -> TINMFullAgent:
    return TINMFullAgent(
        nodes=graph_nodes,
        top_k=cfg.rag_top_k,
        model=cfg.llm_model,
        max_tokens=cfg.max_tokens,
    )


def _run_one_benchmark(
    agent,
    tasks,
    turn_sequences,
    encoder,
    judge_client,
    judge_model: str,
    label: str,
) -> list[SessionMetrics]:
    results = []
    print(f"\n  --- {label} ({len(tasks)} tasks) ---")
    for i, (task, ts) in enumerate(zip(tasks, turn_sequences)):
        m = run_agent_on_task(agent, task, ts, encoder, judge_client, judge_model)
        results.append(m)
        print(
            f"    [{i + 1:>3}/{len(tasks)}] q={m.quality_score:.2f} "
            f"tok_task={m.task_input_tokens}+{m.task_output_tokens}"
        )
    return results


def run(cfg: Config, smoke: bool = False, only: set[str] | None = None) -> dict:
    if smoke:
        cfg = Config(**{**asdict(cfg), "n_tasks": 4})

    print(f"[setup] Initializing encoder...")
    encoder = TfidfEncoder()

    print(f"[setup] Generating synthetic graph...")
    graph = generate_graph(
        n_nodes=cfg.n_nodes,
        n_topics=cfg.n_topics,
        encoder=encoder,
        seed=cfg.seed,
        intra_topic_edge_prob=cfg.intra_topic_edge_prob,
        cross_topic_edge_prob=cfg.cross_topic_edge_prob,
    )

    print(f"[setup] Generating continuity tasks...")
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
    seqs_c = [build_turn_sequence(t, tasks_c, rng_dist, cfg.distractor_probability) for t in tasks_c]

    print(f"[setup] Generating shift tasks...")
    tasks_s = generate_shift_tasks(graph, cfg.n_tasks, encoder, cfg.seed, facts_per_half=2)
    seqs_s = [build_shift_turn_sequence(t) for t in tasks_s]

    judge_client = anthropic.Anthropic()
    results = {}

    if only is None or "continuity" in only:
        agent = _make_agent(graph.nodes, cfg)
        results["continuity"] = _run_one_benchmark(
            agent, tasks_c, seqs_c, encoder, judge_client, cfg.judge_model, "CONTINUITY"
        )

    if only is None or "shift" in only:
        agent = _make_agent(graph.nodes, cfg)
        results["shift"] = _run_one_benchmark(
            agent, tasks_s, seqs_s, encoder, judge_client, cfg.judge_model, "SHIFT"
        )

    if only is None or "musique_2hop" in only:
        print(f"\n[setup] Loading MuSiQue 2-hop...")
        graph_m2, tasks_m2 = load_musique_benchmark(
            TfidfEncoder(), n_tasks=cfg.n_tasks, n_hops=2, seed=cfg.seed
        )
        seqs_m2 = [build_shift_turn_sequence(t) for t in tasks_m2]
        # Need a fresh encoder for the LLM eval too; recreate it with the m2 graph
        encoder_m2 = TfidfEncoder()
        encoder_m2.fit([n.content for n in graph_m2.nodes])
        agent = _make_agent(graph_m2.nodes, cfg)
        results["musique_2hop"] = _run_one_benchmark(
            agent, tasks_m2, seqs_m2, encoder_m2, judge_client, cfg.judge_model, "MuSiQue 2-hop"
        )

    if only is None or "musique_3hop" in only:
        print(f"\n[setup] Loading MuSiQue 3-hop...")
        graph_m3, tasks_m3 = load_musique_benchmark(
            TfidfEncoder(), n_tasks=cfg.n_tasks, n_hops=3, seed=cfg.seed
        )
        seqs_m3 = [build_shift_turn_sequence(t) for t in tasks_m3]
        encoder_m3 = TfidfEncoder()
        encoder_m3.fit([n.content for n in graph_m3.nodes])
        agent = _make_agent(graph_m3.nodes, cfg)
        results["musique_3hop"] = _run_one_benchmark(
            agent, tasks_m3, seqs_m3, encoder_m3, judge_client, cfg.judge_model, "MuSiQue 3-hop"
        )

    return results


def _save(results: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {}
    for bench, metrics in results.items():
        agent_list = []
        for m in metrics:
            d = asdict(m)
            d["subquestion_nodes_visited"] = [[int(x) for x in v] for v in m.subquestion_nodes_visited]
            agent_list.append(d)
        serializable[bench] = {"tinm_full": agent_list}
    path.write_text(json.dumps(serializable, indent=2))
    print(f"\nSaved to {path}")


def _print_summary(results: dict) -> None:
    print("\n" + "=" * 60)
    print(f"{'Benchmark':<18} {'n':>3} {'q_mean':>8} {'q_med':>8} {'tokens':>9}")
    print("-" * 60)
    for bench, metrics in results.items():
        n = len(metrics)
        if n == 0:
            continue
        qs = [m.quality_score for m in metrics]
        total = sum(
            sum(m.subquestion_input_tokens) + sum(m.subquestion_output_tokens)
            + sum(m.distractor_input_tokens) + sum(m.distractor_output_tokens)
            for m in metrics
        )
        print(f"{bench:<18} {n:>3} {statistics.mean(qs):>8.3f} {statistics.median(qs):>8.3f} {total:>9}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated subset: continuity,shift,musique_2hop,musique_3hop",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "pilot_full_results.json",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    cfg = Config()
    t0 = time.time()
    results = run(cfg, smoke=args.smoke, only=only)
    print(f"\nTotal time: {time.time() - t0:.1f}s")
    _print_summary(results)
    _save(results, args.out)


if __name__ == "__main__":
    main()
