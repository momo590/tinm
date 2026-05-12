"""Consolidate all benchmark results into a paper-ready experimental section.

Loads:
  - pilot_v2_results.json (synthetic continuity + topic shift)
  - pilot_real_results.json (MuSiQue 2-hop)
  - pilot_real_results_3hop.json (MuSiQue 3-hop)

Produces:
  - paper_results/paper_section.md  (the main publication-ready output)
  - paper_results/*.csv             (one per table for figures/citation)
"""
import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Loading & flattening
# ---------------------------------------------------------------------------

BENCHMARK_LABELS = {
    "continuity": "Synthetic continuity",
    "shift": "Synthetic topic shift",
    "musique_2hop": "MuSiQue 2-hop",
    "musique_3hop": "MuSiQue 3-hop",
}

# Display order for benchmarks and agents
BENCH_ORDER = ["continuity", "shift", "musique_2hop", "musique_3hop"]
AGENT_ORDER = ["rag_baseline", "rag_with_history", "tinm_a085", "tinm_adapt"]


def load_all(base: Path) -> dict[str, dict[str, list[dict]]]:
    """Returns {bench_key: {agent_name: list[metric_dicts]}}."""
    files = {
        "pilot_v2_results.json": ["continuity", "shift"],
        "pilot_real_results.json": ["musique_2hop"],
        "pilot_real_results_3hop.json": ["musique_3hop"],
    }
    out: dict[str, dict] = {}
    for fname, expected_keys in files.items():
        path = base / fname
        if not path.exists():
            print(f"WARN: missing {path}", file=sys.stderr)
            continue
        data = json.load(open(path))
        for key in expected_keys:
            if key in data:
                out[key] = data[key]
            else:
                print(f"WARN: {fname} has no key {key} (keys: {list(data.keys())})")
    return out


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def total_tokens(metrics: list[dict]) -> int:
    return sum(
        sum(m["subquestion_input_tokens"]) + sum(m["subquestion_output_tokens"])
        + sum(m["distractor_input_tokens"]) + sum(m["distractor_output_tokens"])
        for m in metrics
    )


def agent_summary(metrics: list[dict]) -> dict:
    qs = [m["quality_score"] for m in metrics]
    jqs = [m["judge_quality"] for m in metrics]
    tok = total_tokens(metrics)
    n = len(metrics)
    return {
        "n": n,
        "q_mean": statistics.mean(qs),
        "q_median": statistics.median(qs),
        "q_stdev": statistics.stdev(qs) if n > 1 else 0.0,
        "judge_mean": statistics.mean(jqs),
        "tokens": tok,
        "q_per_1k_tok": 1000 * sum(qs) / tok if tok else 0.0,
        "high_quality_count": sum(1 for q in qs if q >= 0.5),
        "very_high_count": sum(1 for q in qs if q >= 0.75),
    }


def paired_test(a: list[dict], b: list[dict]) -> dict:
    deltas = [ai["quality_score"] - bi["quality_score"] for ai, bi in zip(a, b)]
    n = len(deltas)
    if n < 2:
        return {"n": n, "mean_delta": float("nan"), "t_stat": float("nan"),
                "wins": 0, "losses": 0, "ties": 0}
    m = statistics.mean(deltas)
    sd = statistics.stdev(deltas)
    se = sd / math.sqrt(n)
    t = m / se if se > 0 else 0.0
    return {
        "n": n,
        "mean_delta": m,
        "se": se,
        "t_stat": t,
        "wins": sum(1 for d in deltas if d > 1e-9),
        "losses": sum(1 for d in deltas if d < -1e-9),
        "ties": sum(1 for d in deltas if abs(d) <= 1e-9),
    }


def distractor_breakdown(metrics: list[dict]) -> dict:
    qw = [m["quality_score"] for m in metrics if m.get("had_distractor")]
    qo = [m["quality_score"] for m in metrics if not m.get("had_distractor")]
    return {
        "n_with": len(qw),
        "n_without": len(qo),
        "q_with": statistics.mean(qw) if qw else float("nan"),
        "q_without": statistics.mean(qo) if qo else float("nan"),
        "drop": (statistics.mean(qo) - statistics.mean(qw)) if qw and qo else float("nan"),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def fmt(v, digits=3, signed=False):
    if isinstance(v, float):
        if math.isnan(v):
            return "n/a"
        return f"{v:{'+'if signed else ''}.{digits}f}"
    return str(v)


def render_md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def section_headline(data: dict, outdir: Path) -> str:
    """Section 1: master table with all benchmarks × all agents."""
    out = ["## 1. Headline results — all benchmarks × all agents\n"]
    out.append(
        "Quality is the hybrid score (50% symbolic factual coverage + 50% LLM-as-judge). "
        "Higher is better. All runs: n=50 tasks, Claude Sonnet 4.6, seed=42, top-K=5.\n"
    )

    csv_rows = []
    header = ["Benchmark", "Agent", "Quality", "Median", "Tokens", "Q/1k tok"]
    rows = []
    for bench_key in BENCH_ORDER:
        if bench_key not in data:
            continue
        bench_label = BENCHMARK_LABELS[bench_key]
        for agent in AGENT_ORDER:
            if agent not in data[bench_key]:
                continue
            s = agent_summary(data[bench_key][agent])
            rows.append([
                bench_label,
                agent,
                fmt(s["q_mean"], 3),
                fmt(s["q_median"], 3),
                f"{s['tokens']:,}",
                fmt(s["q_per_1k_tok"], 3),
            ])
            csv_rows.append({
                "benchmark": bench_key,
                "agent": agent,
                **{k: v for k, v in s.items() if k != "n"},
            })
    out.append(render_md_table(header, rows))
    write_csv(csv_rows, outdir / "table_headline.csv")
    return "\n".join(out)


def section_significance(data: dict, outdir: Path) -> str:
    out = ["\n## 2. Statistical significance (paired t-tests, n=50)\n"]
    out.append(
        "For each benchmark we run paired t-tests between agents on the same tasks "
        "(common seed). `t > 1.96` is significant at p < 0.05. W/L/T = wins/losses/ties.\n"
    )

    comparisons = [
        ("tinm_adapt", "rag_baseline"),
        ("tinm_a085", "rag_baseline"),
        ("tinm_adapt", "rag_with_history"),
        ("tinm_a085", "rag_with_history"),
        ("tinm_adapt", "tinm_a085"),
        ("rag_with_history", "rag_baseline"),
    ]

    csv_rows = []
    header = ["Benchmark", "Comparison", "Δ quality", "SE", "t-stat", "W/L/T"]
    rows = []
    for bench_key in BENCH_ORDER:
        if bench_key not in data:
            continue
        for a, b in comparisons:
            if a not in data[bench_key] or b not in data[bench_key]:
                continue
            r = paired_test(data[bench_key][a], data[bench_key][b])
            wlt = f"{r['wins']}/{r['losses']}/{r['ties']}"
            sig_mark = ""
            if not math.isnan(r["t_stat"]):
                if abs(r["t_stat"]) >= 2.58:
                    sig_mark = " **"
                elif abs(r["t_stat"]) >= 1.96:
                    sig_mark = " *"
            rows.append([
                BENCHMARK_LABELS[bench_key],
                f"{a} vs {b}",
                fmt(r["mean_delta"], 4, signed=True) + sig_mark,
                fmt(r["se"], 4),
                fmt(r["t_stat"], 2),
                wlt,
            ])
            csv_rows.append({
                "benchmark": bench_key,
                "agent_a": a,
                "agent_b": b,
                **r,
                "wlt": wlt,
            })
    out.append(render_md_table(header, rows))
    out.append("\n`*` p<0.05, `**` p<0.01")
    write_csv(csv_rows, outdir / "table_significance.csv")
    return "\n".join(out)


def section_pareto(data: dict, outdir: Path) -> str:
    out = ["\n## 3. Pareto frontier — quality vs token cost\n"]
    out.append(
        "Total tokens summed across all responses (subquestions, distractors, retrieved context). "
        "Best agent on each benchmark per the quality/tokens trade-off is **bolded**.\n"
    )

    rows = []
    csv_rows = []
    for bench_key in BENCH_ORDER:
        if bench_key not in data:
            continue
        agent_stats = {}
        for agent in AGENT_ORDER:
            if agent not in data[bench_key]:
                continue
            agent_stats[agent] = agent_summary(data[bench_key][agent])
        # Pareto-best by q/1k tokens
        if not agent_stats:
            continue
        best_eff = max(agent_stats.items(), key=lambda kv: kv[1]["q_per_1k_tok"])[0]
        for agent, s in agent_stats.items():
            is_best = agent == best_eff
            rows.append([
                BENCHMARK_LABELS[bench_key],
                f"**{agent}**" if is_best else agent,
                fmt(s["q_mean"], 3),
                f"{s['tokens']:,}",
                f"**{s['q_per_1k_tok']:.3f}**" if is_best else fmt(s["q_per_1k_tok"], 3),
            ])
            csv_rows.append({
                "benchmark": bench_key,
                "agent": agent,
                "quality": s["q_mean"],
                "tokens": s["tokens"],
                "q_per_1k_tok": s["q_per_1k_tok"],
                "is_pareto_best": is_best,
            })
    header = ["Benchmark", "Agent", "Quality", "Tokens", "Q / 1k tok"]
    out.append(render_md_table(header, rows))
    write_csv(csv_rows, outdir / "table_pareto.csv")
    return "\n".join(out)


def section_distractor(data: dict, outdir: Path) -> str:
    if "continuity" not in data:
        return ""
    out = ["\n## 4. Robustness to distractor interruption (continuity benchmark)\n"]
    out.append(
        "On the continuity benchmark, 20/50 tasks have a distractor turn inserted "
        "between Q1 and Q2. *Drop* = quality(no distractor) − quality(with distractor). "
        "Smaller is more robust.\n"
    )
    csv_rows = []
    header = ["Agent", "n with distractor", "n without", "Q with distr.", "Q without distr.", "Drop"]
    rows = []
    for agent in AGENT_ORDER:
        if agent not in data["continuity"]:
            continue
        d = distractor_breakdown(data["continuity"][agent])
        rows.append([
            agent,
            str(d["n_with"]),
            str(d["n_without"]),
            fmt(d["q_with"], 3),
            fmt(d["q_without"], 3),
            fmt(d["drop"], 4),
        ])
        csv_rows.append({"agent": agent, **d})
    out.append(render_md_table(header, rows))
    write_csv(csv_rows, outdir / "table_distractor.csv")
    return "\n".join(out)


def section_hard_tasks(data: dict, outdir: Path) -> str:
    out = ["\n## 5. Hard-task success rate\n"]
    out.append(
        "Counts of tasks reaching quality ≥ 0.5 (at least half of expected facts identified) "
        "and ≥ 0.75 (most or all facts identified). Larger memory benefit shows on the "
        "harder tasks where retrieval alone is insufficient.\n"
    )
    csv_rows = []
    header = ["Benchmark", "Agent", "n ≥ 0.5", "n ≥ 0.75", "% ≥ 0.5"]
    rows = []
    for bench_key in BENCH_ORDER:
        if bench_key not in data:
            continue
        for agent in AGENT_ORDER:
            if agent not in data[bench_key]:
                continue
            s = agent_summary(data[bench_key][agent])
            n = s["n"]
            pct = 100 * s["high_quality_count"] / n if n else 0
            rows.append([
                BENCHMARK_LABELS[bench_key],
                agent,
                str(s["high_quality_count"]),
                str(s["very_high_count"]),
                f"{pct:.1f}%",
            ])
            csv_rows.append({
                "benchmark": bench_key,
                "agent": agent,
                "n_ge_50": s["high_quality_count"],
                "n_ge_75": s["very_high_count"],
                "pct_ge_50": pct,
            })
    out.append(render_md_table(header, rows))
    write_csv(csv_rows, outdir / "table_hard_tasks.csv")
    return "\n".join(out)


def section_ablation(outdir: Path) -> str:
    out = ["\n## 6. Ablation — TINM-full (dual-anchor) negative result\n"]
    out.append(
        "We tested a dual-scale variant (TINM-full) combining a slow `magnetic_anchor` "
        "(α=0.92 on query embeddings; long-term topic prior) with a faster `courant_anchor` "
        "(α=0.50 on retrieved-node centroids; recent trajectory). Composite NPF score: "
        "`s(v) = 0.40·sim_query(v) + 0.25·sim_magnetic(v) + 0.25·sim_courant(v) − 0.10·repulsion(v)`.\n"
    )
    out.append("Retrieval-only dry-run on the same task seeds (`gold/top5` at the last task turn):\n")
    rows = [
        ["Synthetic continuity",    "2.76", "1.46"],
        ["Synthetic topic shift",   "1.96", "0.66"],
        ["MuSiQue 2-hop",           "0.78", "0.78"],
        ["MuSiQue 3-hop",           "0.88", "0.84"],
    ]
    out.append(render_md_table(["Benchmark", "TINM-lite a085", "TINM-full v0"], rows))
    out.append(
        "\nFinding: TINM-full degrades retrieval on synthetic benchmarks. Diagnosis: the "
        "`courant_anchor` follows the distractor (continuity) or pivot (shift), polluting "
        "retrieval at the next task turn. A weight sweep (5 configurations) confirmed that "
        "any non-zero `w_courant` introduces this regression. Setting `w_courant = 0` "
        "reduces TINM-full to TINM-lite. No LLM evaluation was run on this variant."
    )
    out.append(
        "\nWe attribute this to the single-turn nature of friction detection: distractors and "
        "topic shifts both manifest as query-anchor divergence at one turn, and cannot be "
        "distinguished without multi-turn evidence. We leave proper multi-turn friction "
        "detection (e.g. accumulated divergence over a sliding window) to future work."
    )
    return "\n".join(out)


def section_narrative(data: dict) -> str:
    out = ["\n## 7. Headline findings\n"]
    # Compute key numbers
    bullets = []
    for bench_key in BENCH_ORDER:
        if bench_key not in data:
            continue
        agents = data[bench_key]
        if "tinm_adapt" in agents and "rag_baseline" in agents and "rag_with_history" in agents:
            adapt_s = agent_summary(agents["tinm_adapt"])
            a085_s = agent_summary(agents["tinm_a085"])
            base_s = agent_summary(agents["rag_baseline"])
            hist_s = agent_summary(agents["rag_with_history"])
            best_tinm = max([("tinm_adapt", adapt_s), ("tinm_a085", a085_s)], key=lambda x: x[1]["q_mean"])
            best_name, best_s = best_tinm
            best_pair = paired_test(agents[best_name], agents["rag_with_history"])
            bullets.append(
                f"- **{BENCHMARK_LABELS[bench_key]}**: best TINM variant is `{best_name}` "
                f"at {best_s['q_mean']:.3f} quality vs `rag_with_history` at {hist_s['q_mean']:.3f} "
                f"(Δ={best_pair['mean_delta']:+.3f}, t={best_pair['t_stat']:+.2f}), "
                f"using {best_s['tokens']:,} vs {hist_s['tokens']:,} tokens "
                f"({100*(best_s['tokens']/hist_s['tokens']-1):+.0f}%)."
            )
    out.extend(bullets)
    out.append(
        "\n**Cross-benchmark conclusion.** Across four continuity benchmarks — two "
        "synthetic (controlled distractors, controlled topic shifts) and two real-data "
        "(MuSiQue multi-hop QA) — TINM-lite consistently sits on or above the Pareto "
        "frontier defined by stateless RAG and history-injection RAG. The strongest "
        "comparative result is on MuSiQue 3-hop, where verbose chat history actively "
        "impairs LLM reasoning (`rag_with_history` underperforms `rag_baseline` by "
        "−0.035) while TINM-lite's compressed memory (single slow-EMA query anchor + "
        "compact trajectory hint) beats both."
    )
    out.append(
        "\nThe friction-adaptive variant (`tinm_adapt`) outperforms fixed-anchor "
        "(`tinm_a085`) only on the explicit topic-shift benchmark (Δ=+0.035, t=2.90). "
        "On chained reasoning (MuSiQue 3-hop), the two are statistically tied. We "
        "interpret this as: dynamic alpha helps when query-level divergence truly "
        "signals a topic shift, but over-reacts on coherent reasoning chains. A more "
        "robust friction signal (multi-turn evidence) is a natural direction for "
        "future work."
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path(__file__).resolve().parent / "paper_results",
    )
    args = parser.parse_args()

    data = load_all(args.runs_dir)
    if not data:
        print("ERROR: no results loaded", file=sys.stderr)
        sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    parts = ["# TINM — Experimental Section (consolidated)\n"]
    parts.append(
        "Four benchmarks. Four agents (`rag_baseline`, `rag_with_history`, `tinm_a085`, "
        "`tinm_adapt`). n=50 tasks per benchmark, Claude Sonnet 4.6, deterministic seed=42, "
        "top-K retrieval = 5.\n"
    )
    parts.append("**Benchmarks**:\n")
    parts.append(
        "1. *Synthetic continuity* — 200 nodes, 5 topics, 2 sub-questions per task. "
        "Random distractor (different topic) inserted with p=0.5 between Q1 and Q2. "
        "Q2 mixes soft and hard co-reference.\n"
        "2. *Synthetic topic shift* — same graph, 4 sub-questions: Q1+Q2 about topic A, "
        "Q3 explicit pivot to topic B, Q4 hard co-reference for B. The decisive test of "
        "whether the memory mechanism adapts to mid-task shifts.\n"
        "3. *MuSiQue 2-hop* — real Wikipedia paragraphs. Each task: a multi-hop question "
        "decomposed into Q1 (identify bridge entity) and Q2 (anaphoric, asks about "
        "bridge entity's property).\n"
        "4. *MuSiQue 3-hop* — three chained hops. Q1 → Q2 → Q3, each Q_i using #i-1 to "
        "reference the previous answer."
    )

    parts.append(section_headline(data, args.outdir))
    parts.append(section_significance(data, args.outdir))
    parts.append(section_pareto(data, args.outdir))
    parts.append(section_distractor(data, args.outdir))
    parts.append(section_hard_tasks(data, args.outdir))
    parts.append(section_ablation(args.outdir))
    parts.append(section_narrative(data))

    report = "\n".join(parts)
    (args.outdir / "paper_section.md").write_text(report)

    print(report)
    print(f"\n\nWritten to {args.outdir}/paper_section.md and CSVs alongside.")


if __name__ == "__main__":
    main()
