"""Lane F: head-to-head eval of tinm_b2_conductor vs tinm_full on wiki2hop.

Question this answers: does the digest layer (pre-computed summary injected at
prompt time) measurably improve correctness or reduce hallucination over the
plain TINM-full agent? Pre-gate for v0.3.0 ship.

Per-task protocol:
  1. Build digest_text from `task.goal_text` (the original full multi-hop
     question, which is a faithful proxy of what `_digest_worker` would write
     to cache from a prior session).
  2. Run tinm_full on the task's subq sequence (Q1 with explicit entity, Q2
     anaphoric "And what is its X?").
  3. Run tinm_b2_conductor(digest_text=task.goal_text) on the same sequence.
  4. Score each via combined_quality (50 % symbolic fact match + 50 % LLM judge).
  5. Aggregate correctness, hallucination, and Anthropic spend.

Hallucination = fraction of (task, fact) pairs where the judge marks the fact
as confidently identified but the symbolic check finds no value match in the
designated response (judge believed the agent, symbolic says no).

Usage:
    /root/.tinm/.venv/bin/python3 /root/TNIM/benchmark/runs/lane_f_eval_v030.py
    /root/.tinm/.venv/bin/python3 /root/TNIM/benchmark/runs/lane_f_eval_v030.py --smoke
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.tinm_b2_conductor import TINMConductorAgent
from agents.tinm_full import TINMFullAgent
from config import Config
from env.encoder import TfidfEncoder
from env.wiki2hop_loader import load_wiki2hop_benchmark
from eval.metrics import SessionMetrics
from runs.pilot import run_agent_on_task
from runs.pilot_v2 import build_shift_turn_sequence


# Sonnet 4.6 list prices (per million input / output tokens).
SONNET_4_6_INPUT_USD_PER_MTOK = 3.0
SONNET_4_6_OUTPUT_USD_PER_MTOK = 15.0


def _agent_cost_usd(metrics: list[SessionMetrics]) -> float:
    """Sum Anthropic spend across all (task, subq) calls for one agent."""
    in_tok = sum(sum(m.subquestion_input_tokens) for m in metrics)
    out_tok = sum(sum(m.subquestion_output_tokens) for m in metrics)
    return (
        in_tok * SONNET_4_6_INPUT_USD_PER_MTOK / 1_000_000
        + out_tok * SONNET_4_6_OUTPUT_USD_PER_MTOK / 1_000_000
    )


def _hallucination_rate(metrics: list[SessionMetrics]) -> float:
    """Fraction of (task, fact) pairs where judge says found but symbolic says no."""
    confident_wrong = 0
    total = 0
    for m in metrics:
        for key in m.facts_found_judge:
            total += 1
            if m.facts_found_judge.get(key, False) and not m.facts_found_symbolic.get(key, False):
                confident_wrong += 1
    return confident_wrong / total if total else 0.0


def _correctness_stats(metrics: list[SessionMetrics]) -> tuple[float, float]:
    """Mean and standard error of the combined quality score."""
    if not metrics:
        return 0.0, 0.0
    qs = [m.quality_score for m in metrics]
    n = len(qs)
    mean = sum(qs) / n
    if n < 2:
        return mean, 0.0
    var = sum((q - mean) ** 2 for q in qs) / (n - 1)
    sem = math.sqrt(var / n)
    return mean, sem


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent.parent.parent), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def run(n_tasks: int = 30, seed: int = 42) -> dict:
    print(f"[1/4] Initializing TF-IDF encoder...")
    encoder = TfidfEncoder()

    print(f"[2/4] Loading 2WikiMultihopQA ({n_tasks} tasks)...")
    graph, tasks = load_wiki2hop_benchmark(encoder, n_tasks=n_tasks, seed=seed)
    print(f"      Graph: {len(graph.nodes)} unique paragraphs")
    print(f"      Tasks: {len(tasks)}")

    turn_seqs = [build_shift_turn_sequence(t) for t in tasks]

    print(f"[3/4] Constructing agents (tinm_full + tinm_b2_conductor)...")
    cfg = Config()
    common = dict(
        nodes=graph.nodes,
        top_k=cfg.rag_top_k,
        model=cfg.llm_model,
        max_tokens=cfg.max_tokens,
    )
    agents = {
        "tinm_full": TINMFullAgent(**common),
        "tinm_b2_conductor": TINMConductorAgent(**common, digest_text=""),
    }
    judge_client = anthropic.Anthropic(max_retries=8)

    print(f"[4/4] Running both agents on each task...")
    results: dict[str, list[SessionMetrics]] = {name: [] for name in agents}
    t0 = time.time()
    for agent_name, agent in agents.items():
        print(f"\n  --- {agent_name} ---")
        for i, (task, ts) in enumerate(zip(tasks, turn_seqs)):
            # Inject digest for conductor: simulates _digest_worker writing a
            # prior-session summary to cache. Using goal_text is the most
            # faithful proxy — it's exactly what a worker would synthesize
            # from the full conversation arc.
            if agent_name == "tinm_b2_conductor":
                agent.digest_text = f"User's overall goal: {task.goal_text}"

            try:
                m = run_agent_on_task(agent, task, ts, encoder, judge_client, cfg.judge_model)
            except Exception as exc:
                print(f"    [{i + 1:>3}/{len(tasks)}] FAILED: {exc!r}")
                continue
            results[agent_name].append(m)
            print(
                f"    [{i + 1:>3}/{len(tasks)}] q={m.quality_score:.2f} "
                f"judge={m.judge_quality:.2f} "
                f"tok={m.task_input_tokens}+{m.task_output_tokens}"
            )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s\n")

    # Per-agent aggregates
    summary = {}
    for name, ms in results.items():
        mean_q, sem_q = _correctness_stats(ms)
        summary[name] = {
            "n_tasks_completed": len(ms),
            "correctness_mean": round(mean_q, 4),
            "correctness_sem": round(sem_q, 4),
            "hallucination_rate": round(_hallucination_rate(ms), 4),
            "cost_usd": round(_agent_cost_usd(ms), 4),
            "judge_quality_mean": round(sum(m.judge_quality for m in ms) / len(ms), 4) if ms else 0.0,
        }

    delta_corr = (
        summary["tinm_b2_conductor"]["correctness_mean"]
        - summary["tinm_full"]["correctness_mean"]
    )
    delta_hall = (
        summary["tinm_b2_conductor"]["hallucination_rate"]
        - summary["tinm_full"]["hallucination_rate"]
    )

    # Gate: digest must not regress correctness AND must hold hallucination < 0.01
    gate_pass = (
        summary["tinm_b2_conductor"]["correctness_mean"]
        >= summary["tinm_full"]["correctness_mean"]
        and summary["tinm_b2_conductor"]["hallucination_rate"] < 0.01
    )

    report = {
        "date": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "n_tasks": n_tasks,
        "n_tasks_completed": min(len(ms) for ms in results.values()),
        "dataset": "voidful/2WikiMultihopQA (validation, 2-hop linear chains)",
        "seed": seed,
        "model_under_test": cfg.llm_model,
        "judge_model": cfg.judge_model,
        "digest_strategy": "task.goal_text (full multi-hop question as prior-session summary)",
        "agents": summary,
        "gate_pass": gate_pass,
        "delta_correctness": round(delta_corr, 4),
        "delta_hallucination": round(delta_hall, 4),
        "elapsed_seconds": round(elapsed, 1),
        "notes": (
            "Lane F head-to-head: tinm_b2_conductor (digest-augmented) vs "
            "tinm_full (baseline) on a 2-hop QA subset. Correctness is the "
            "combined_quality score (0.5 symbolic + 0.5 LLM judge). "
            "Hallucination = (judge says found) AND (symbolic check refutes). "
            "Cost excludes judge tokens (shared infra across agents)."
        ),
    }
    return report, results


def _save(report: dict, results: dict, out_path: Path, raw_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"Saved aggregate report to {out_path}")

    serializable_raw: dict = {}
    for agent_name, metrics in results.items():
        agent_list = []
        for m in metrics:
            d = asdict(m)
            d["subquestion_nodes_visited"] = [
                [int(x) for x in v] for v in m.subquestion_nodes_visited
            ]
            agent_list.append(d)
        serializable_raw[agent_name] = agent_list
    raw_path.write_text(json.dumps(serializable_raw, indent=2))
    print(f"Saved per-task raw metrics to {raw_path}")


def _print_table(report: dict) -> None:
    print("=" * 72)
    print("LANE F RESULT")
    print("=" * 72)
    print(f"{'agent':<22} {'correct':>10} {'sem':>8} {'judge':>8} {'halluc':>8} {'cost$':>9}")
    print("-" * 72)
    for name, s in report["agents"].items():
        print(
            f"{name:<22} "
            f"{s['correctness_mean']:>10.3f} "
            f"{s['correctness_sem']:>8.3f} "
            f"{s['judge_quality_mean']:>8.3f} "
            f"{s['hallucination_rate']:>8.3f} "
            f"{s['cost_usd']:>9.4f}"
        )
    print("-" * 72)
    print(f"  Δ correctness    : {report['delta_correctness']:+.4f}")
    print(f"  Δ hallucination  : {report['delta_hallucination']:+.4f}")
    print(f"  gate_pass        : {report['gate_pass']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="Run 3 tasks only")
    parser.add_argument("--n", type=int, default=30, help="Number of tasks")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/root/TNIM/benchmark/lane_f_eval_v0.3.0.json"),
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=Path("/root/TNIM/benchmark/runs/lane_f_eval_v0.3.0_raw.json"),
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    n = 3 if args.smoke else args.n
    report, results = run(n_tasks=n)
    _save(report, results, args.out, args.raw)
    _print_table(report)


if __name__ == "__main__":
    main()
