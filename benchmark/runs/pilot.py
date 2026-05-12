"""Pilot run: synthetic continuity benchmark with co-reference + distractors.

Tests two agents on the same task sequences:
  - rag_baseline:    stateless top-k retrieval (the floor)
  - rag_with_history: multi-turn prompt + history-enriched retrieval (the realistic baseline)

Requires ANTHROPIC_API_KEY in the environment.

Usage:
    .venv/bin/python -m runs.pilot           # full pilot (50 tasks)
    .venv/bin/python -m runs.pilot --smoke   # smoke test (2 tasks)
"""
import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import anthropic
import numpy as np

# Allow `python -m runs.pilot` from the benchmark/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base import Turn
from agents.rag_baseline import RAGBaselineAgent
from agents.rag_with_history import RAGWithHistoryAgent
from agents.tinm_lite import TINMLiteAgent
from config import Config
from env.encoder import TfidfEncoder
from env.graph_generator import generate_graph
from env.task_generator import Task, generate_tasks
from eval.metrics import SessionMetrics
from eval.quality import combined_quality


@dataclass
class TurnSpec:
    query: str
    is_distractor: bool
    source_task_id: int


def build_turn_sequence(
    task: Task,
    all_tasks: list[Task],
    rng: np.random.Generator,
    distractor_probability: float,
) -> list[TurnSpec]:
    """For a given task, produce the interleaved sequence of turns the agent faces.

    - Subq 1 always comes first (explicit topic phrasing).
    - Before each later subq, with `distractor_probability` we insert a turn
      from a different-topic task (explicit phrasing of its first subq).
    """
    if not task.subquestions:
        return []
    turns = [TurnSpec(task.subquestions[0], False, task.id)]
    other_tasks = [t for t in all_tasks if t.topic_id != task.topic_id]

    for i in range(1, len(task.subquestions)):
        if other_tasks and rng.random() < distractor_probability:
            other = other_tasks[int(rng.integers(0, len(other_tasks)))]
            turns.append(TurnSpec(other.subquestions[0], True, other.id))
        turns.append(TurnSpec(task.subquestions[i], False, task.id))
    return turns


def run_agent_on_task(
    agent,
    task: Task,
    turn_seq: list[TurnSpec],
    encoder: TfidfEncoder,
    judge_client,
    judge_model: str,
) -> SessionMetrics:
    agent.reset(task)
    m = SessionMetrics(task_id=task.id, agent_name=agent.name)
    history: list[Turn] = []

    for turn in turn_seq:
        q_emb = encoder.encode(turn.query, convert_to_numpy=True, show_progress_bar=False)
        resp = agent.respond(turn.query, q_emb, history=history)

        if turn.is_distractor:
            m.distractor_input_tokens.append(resp.input_tokens)
            m.distractor_output_tokens.append(resp.output_tokens)
            m.had_distractor = True
        else:
            m.subquestion_input_tokens.append(resp.input_tokens)
            m.subquestion_output_tokens.append(resp.output_tokens)
            m.subquestion_cache_read_tokens.append(resp.cache_read_tokens)
            m.subquestion_nodes_visited.append(resp.nodes_visited)
            m.responses.append(resp.text)

        history.append(Turn(query=turn.query, response=resp.text, is_distractor=turn.is_distractor))

    quality, symbolic, judge_found, judge_score = combined_quality(
        judge_client, judge_model, task, m.responses
    )
    m.quality_score = quality
    m.facts_found_symbolic = symbolic
    m.facts_found_judge = judge_found
    m.judge_quality = judge_score
    return m


def run(cfg: Config, smoke: bool = False) -> dict[str, list[SessionMetrics]]:
    if smoke:
        cfg = Config(**{**asdict(cfg), "n_tasks": 4})  # 4 tasks -> ~2 with distractor

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
    print(f"      -> {len(graph.nodes)} nodes, {len(graph.edges)} edges")

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

    print(f"[5/5] Running agents...")
    agents = {
        "rag_baseline": RAGBaselineAgent(
            nodes=graph.nodes,
            top_k=cfg.rag_top_k,
            model=cfg.llm_model,
            max_tokens=cfg.max_tokens,
        ),
        "rag_with_history": RAGWithHistoryAgent(
            nodes=graph.nodes,
            encoder=encoder,
            top_k=cfg.rag_top_k,
            model=cfg.llm_model,
            max_tokens=cfg.max_tokens,
        ),
        "tinm_lite": TINMLiteAgent(
            nodes=graph.nodes,
            top_k=cfg.rag_top_k,
            model=cfg.llm_model,
            max_tokens=cfg.max_tokens,
        ),
    }
    judge_client = anthropic.Anthropic()

    results: dict[str, list[SessionMetrics]] = {name: [] for name in agents}
    t0 = time.time()
    for agent_name, agent in agents.items():
        print(f"\n--- Agent: {agent_name} ---")
        for i, (task, turn_seq) in enumerate(zip(tasks, turn_sequences)):
            m = run_agent_on_task(agent, task, turn_seq, encoder, judge_client, cfg.judge_model)
            results[agent_name].append(m)
            print(
                f"  [{i + 1:>3}/{len(tasks)}] topic={task.topic_id} "
                f"q={m.quality_score:.2f} judge={m.judge_quality:.2f} "
                f"distr={'Y' if m.had_distractor else 'N'} "
                f"tok_task={m.task_input_tokens}+{m.task_output_tokens} "
                f"tok_distr={sum(m.distractor_input_tokens)}+{sum(m.distractor_output_tokens)}"
            )

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    _report(results, tasks)
    return results


def _report(results: dict[str, list[SessionMetrics]], tasks: list[Task]) -> None:
    print("\n=== Aggregate ===")
    print(f"{'Agent':<22} {'Quality':>9} {'Judge':>9} {'Recall':>9} {'TaskTok_in':>12} {'TaskTok_out':>13} {'DistrTok':>11}")
    for agent_name, metrics in results.items():
        n = len(metrics)
        if n == 0:
            continue
        avg_q = sum(m.quality_score for m in metrics) / n
        avg_judge = sum(m.judge_quality for m in metrics) / n
        avg_recall = sum(
            m.gold_path_recall(set(t.gold_path)) for m, t in zip(metrics, tasks)
        ) / n
        avg_in = sum(m.task_input_tokens for m in metrics) / n
        avg_out = sum(m.task_output_tokens for m in metrics) / n
        avg_distr = sum(
            sum(m.distractor_input_tokens) + sum(m.distractor_output_tokens) for m in metrics
        ) / n
        print(
            f"{agent_name:<22} {avg_q:>9.3f} {avg_judge:>9.3f} {avg_recall:>9.3f} "
            f"{avg_in:>12.1f} {avg_out:>13.1f} {avg_distr:>11.1f}"
        )

    print("\n=== With vs Without Distractor (quality on task subqs) ===")
    print(f"{'Agent':<22} {'q_no_distr':>12} {'q_with_distr':>14} {'delta':>9}")
    for agent_name, metrics in results.items():
        q_no = [m.quality_score for m in metrics if not m.had_distractor]
        q_yes = [m.quality_score for m in metrics if m.had_distractor]
        avg_no = sum(q_no) / len(q_no) if q_no else float("nan")
        avg_yes = sum(q_yes) / len(q_yes) if q_yes else float("nan")
        delta = avg_no - avg_yes if q_no and q_yes else float("nan")
        print(f"{agent_name:<22} {avg_no:>12.3f} {avg_yes:>14.3f} {delta:>9.3f}")


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
        default=Path(__file__).resolve().parent / "pilot_results.json",
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
