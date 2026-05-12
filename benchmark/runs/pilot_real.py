"""Pilot on real data: MuSiQue 2-hop QA.

Tests whether the TNIM-lite v2 mechanism (friction-adaptive memory) transfers
from synthetic to real text. Each MuSiQue example provides a natural continuity
test: Q1 establishes a bridge entity, Q2 is anaphoric ("its X").

Usage:
    .venv/bin/python -m runs.pilot_real --smoke   # 4 tasks (~$0.30)
    .venv/bin/python -m runs.pilot_real           # 50 tasks (~$5-6)
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
from config import Config
from env.encoder import TfidfEncoder
from env.musique_loader import load_musique_benchmark
from eval.metrics import SessionMetrics
from runs.pilot import TurnSpec, run_agent_on_task
from runs.pilot_v2 import _per_agent_stats, build_shift_turn_sequence, make_agents


def run(cfg: Config, n_tasks: int = 50, n_hops: int = 2, smoke: bool = False):
    if smoke:
        n_tasks = 4

    print(f"[1/4] Initializing encoder...")
    encoder = TfidfEncoder()

    print(f"[2/4] Loading MuSiQue {n_hops}-hop ({n_tasks} examples)...")
    graph, tasks = load_musique_benchmark(encoder, n_tasks=n_tasks, n_hops=n_hops, seed=cfg.seed)
    print(f"      Graph: {len(graph.nodes)} unique paragraphs")
    print(f"      Tasks: {len(tasks)}")
    avg_gold = statistics.mean(len(t.gold_path) for t in tasks)
    print(f"      Avg gold paragraphs per task: {avg_gold:.1f}")

    print(f"[3/4] Building turn sequences (no distractors — multi-hop is the test)...")
    turn_seqs = [build_shift_turn_sequence(t) for t in tasks]

    print(f"[4/4] Running agents...")
    agents = make_agents(graph.nodes, encoder, cfg)
    judge_client = anthropic.Anthropic()

    results = {"musique_2hop": {name: [] for name in agents}}
    t0 = time.time()
    for agent_name, agent in agents.items():
        print(f"\n  --- {agent_name} ---")
        for i, (task, ts) in enumerate(zip(tasks, turn_seqs)):
            m = run_agent_on_task(agent, task, ts, encoder, judge_client, cfg.judge_model)
            results["musique_2hop"][agent_name].append(m)
            print(
                f"    [{i + 1:>3}/{len(tasks)}] q={m.quality_score:.2f} "
                f"tok={m.task_input_tokens}+{m.task_output_tokens}"
            )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print()
    _per_agent_stats(results["musique_2hop"])
    return results


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
    parser.add_argument("--smoke", action="store_true", help="Run 4 tasks only")
    parser.add_argument("--n", type=int, default=50, help="Number of tasks for full run")
    parser.add_argument("--hops", type=int, default=2, help="MuSiQue chain length (2, 3, or 4)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (defaults to runs/pilot_real_results_{hops}hop.json)",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if args.out is None:
        args.out = Path(__file__).resolve().parent / f"pilot_real_results_{args.hops}hop.json"

    cfg = Config()
    results = run(cfg, n_tasks=args.n, n_hops=args.hops, smoke=args.smoke)
    # Rename top-level key to reflect hops
    results = {f"musique_{args.hops}hop": results.pop("musique_2hop")}
    _save_results(results, args.out)


if __name__ == "__main__":
    main()
