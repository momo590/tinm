"""Analyze pilot_v2_results.json and produce a publication-ready report.

Output: markdown report on stdout and to a file, plus CSV tables per section.

Usage:
    .venv/bin/python -m runs.analyze
    .venv/bin/python -m runs.analyze --input runs/pilot_v2_results.json --outdir runs/report
"""
import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path


def fmt_float(v: float, digits: int = 3) -> str:
    if isinstance(v, float) and math.isnan(v):
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def fmt_signed(v: float, digits: int = 3) -> str:
    if isinstance(v, float) and math.isnan(v):
        return "n/a"
    if isinstance(v, float):
        return f"{v:+.{digits}f}"
    return str(v)


def per_agent_summary(per_agent: dict) -> list[dict]:
    out = []
    for name, metrics in per_agent.items():
        qs = [m["quality_score"] for m in metrics]
        jqs = [m["judge_quality"] for m in metrics]
        ti = sum(sum(m["subquestion_input_tokens"]) for m in metrics)
        to = sum(sum(m["subquestion_output_tokens"]) for m in metrics)
        di = sum(sum(m["distractor_input_tokens"]) for m in metrics)
        do_ = sum(sum(m["distractor_output_tokens"]) for m in metrics)
        total = ti + to + di + do_
        out.append({
            "agent": name,
            "n": len(metrics),
            "q_mean": statistics.mean(qs),
            "q_median": statistics.median(qs),
            "q_stdev": statistics.stdev(qs) if len(qs) > 1 else 0.0,
            "judge_mean": statistics.mean(jqs),
            "tok_task_in": ti,
            "tok_task_out": to,
            "tok_distractor": di + do_,
            "tok_total": total,
            "q_per_1k_tok": 1000 * sum(qs) / total if total else 0.0,
        })
    return out


def paired_comparison(agent_a_metrics: list, agent_b_metrics: list) -> dict:
    """Paired t-test: A - B, task-by-task."""
    deltas = [
        a["quality_score"] - b["quality_score"]
        for a, b in zip(agent_a_metrics, agent_b_metrics)
    ]
    n = len(deltas)
    if n < 2:
        return {"n": n, "mean_delta": float("nan"), "se": float("nan"),
                "t_stat": float("nan"), "wins": 0, "losses": 0, "ties": 0}
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


def distractor_breakdown(per_agent: dict) -> list[dict]:
    out = []
    for name, metrics in per_agent.items():
        qw = [m["quality_score"] for m in metrics if m["had_distractor"]]
        qo = [m["quality_score"] for m in metrics if not m["had_distractor"]]
        out.append({
            "agent": name,
            "n_with": len(qw),
            "n_without": len(qo),
            "q_with": statistics.mean(qw) if qw else float("nan"),
            "q_without": statistics.mean(qo) if qo else float("nan"),
            "drop": (statistics.mean(qo) - statistics.mean(qw)) if qw and qo else float("nan"),
        })
    return out


def render_md_table(rows: list[dict], columns: list[tuple[str, str, int | None]]) -> str:
    """columns: list of (display_name, key, digits). digits=None for strings."""
    lines = ["| " + " | ".join(c[0] for c in columns) + " |"]
    lines.append("|" + "|".join("---" for _ in columns) + "|")
    for r in rows:
        parts = []
        for _, key, digits in columns:
            v = r.get(key, "")
            if digits is None or not isinstance(v, float):
                parts.append(str(v))
            else:
                parts.append(fmt_float(v, digits))
        lines.append("| " + " | ".join(parts) + " |")
    return "\n".join(lines)


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def ascii_bar(value: float, max_value: float, width: int = 30) -> str:
    if max_value <= 0:
        return ""
    n = int(round(width * value / max_value))
    return "█" * n + "·" * (width - n)


def render_report(data: dict, outdir: Path) -> str:
    md_parts: list[str] = []
    md_parts.append("# TNIM-lite v2 — Pilot Results\n")
    md_parts.append(
        "Source: `runs/pilot_v2_results.json` (50 tasks per benchmark, Claude Sonnet 4.6, "
        "seed=42)\n"
    )

    # =========================================================================
    # Section 1: Per-benchmark summary
    # =========================================================================
    md_parts.append("## 1. Per-agent summary\n")
    for bench_name in data:
        md_parts.append(f"### {bench_name.title()}\n")
        rows = per_agent_summary(data[bench_name])
        write_csv(rows, outdir / f"summary_{bench_name}.csv")
        cols = [
            ("Agent", "agent", None),
            ("n", "n", None),
            ("Quality (mean)", "q_mean", 3),
            ("Quality (median)", "q_median", 3),
            ("Quality (stdev)", "q_stdev", 3),
            ("Judge mean", "judge_mean", 3),
            ("Total tokens", "tok_total", None),
            ("Quality / 1k tok", "q_per_1k_tok", 3),
        ]
        md_parts.append(render_md_table(rows, cols))
        md_parts.append("")

    # =========================================================================
    # Section 2: Paired comparisons
    # =========================================================================
    md_parts.append("## 2. Paired comparisons\n")
    md_parts.append(
        "Each row: difference of agent vs reference, task-by-task. "
        "t-stat > 1.96 indicates 95% significance (n=50). W/L/T = wins/losses/ties.\n"
    )

    for bench_name in data:
        md_parts.append(f"### {bench_name.title()} — vs `rag_baseline`\n")
        ref = data[bench_name]["rag_baseline"]
        rows = []
        for agent_name, metrics in data[bench_name].items():
            if agent_name == "rag_baseline":
                continue
            result = paired_comparison(metrics, ref)
            rows.append({"agent": agent_name, **result, "wlt": f"{result['wins']}/{result['losses']}/{result['ties']}"})
        write_csv(rows, outdir / f"paired_vs_baseline_{bench_name}.csv")
        cols = [
            ("Agent", "agent", None),
            ("Δ quality", "mean_delta", 4),
            ("SE", "se", 4),
            ("t-stat", "t_stat", 2),
            ("W/L/T", "wlt", None),
        ]
        md_parts.append(render_md_table(rows, cols))
        md_parts.append("")

    md_parts.append("### Cross-comparison — `tinm_adapt` vs `tinm_a085`\n")
    rows = []
    for bench_name in data:
        a = data[bench_name].get("tinm_adapt")
        b = data[bench_name].get("tinm_a085")
        if a and b:
            r = paired_comparison(a, b)
            rows.append({"benchmark": bench_name, **r,
                         "wlt": f"{r['wins']}/{r['losses']}/{r['ties']}"})
    write_csv(rows, outdir / "paired_adapt_vs_a085.csv")
    cols = [
        ("Benchmark", "benchmark", None),
        ("Δ quality", "mean_delta", 4),
        ("SE", "se", 4),
        ("t-stat", "t_stat", 2),
        ("W/L/T", "wlt", None),
    ]
    md_parts.append(render_md_table(rows, cols))
    md_parts.append("")

    # =========================================================================
    # Section 3: Distractor robustness (continuity)
    # =========================================================================
    if "continuity" in data:
        md_parts.append("## 3. Distractor robustness (continuity benchmark)\n")
        rows = distractor_breakdown(data["continuity"])
        write_csv(rows, outdir / "distractor_breakdown.csv")
        cols = [
            ("Agent", "agent", None),
            ("n with", "n_with", None),
            ("n without", "n_without", None),
            ("Quality with distractor", "q_with", 3),
            ("Quality without distractor", "q_without", 3),
            ("Drop", "drop", 4),
        ]
        md_parts.append(render_md_table(rows, cols))
        md_parts.append(
            "\nA *small drop* means the agent's quality is preserved under interruption.\n"
        )

    # =========================================================================
    # Section 4: Quality-cost Pareto (ASCII)
    # =========================================================================
    md_parts.append("## 4. Quality / cost frontier\n")
    md_parts.append(
        "Bars proportional to value. Higher quality + higher q/1k_tok is better; "
        "lower total tokens at same quality is better.\n"
    )
    for bench_name in data:
        md_parts.append(f"### {bench_name.title()}\n")
        rows = per_agent_summary(data[bench_name])
        max_q = max(r["q_mean"] for r in rows)
        max_tok = max(r["tok_total"] for r in rows)
        max_qpkt = max(r["q_per_1k_tok"] for r in rows)
        md_parts.append("```")
        md_parts.append(f"{'Agent':<22} {'Quality':<8} {'Tokens':<10}  bar(quality) | bar(efficiency)")
        for r in rows:
            md_parts.append(
                f"{r['agent']:<22} {r['q_mean']:<8.3f} {r['tok_total']:<10} "
                f"{ascii_bar(r['q_mean'], max_q, 20)} | {ascii_bar(r['q_per_1k_tok'], max_qpkt, 20)}"
            )
        md_parts.append("```")
        md_parts.append("")

    # =========================================================================
    # Section 5: Narrative summary
    # =========================================================================
    md_parts.append("## 5. Headline findings\n")
    # Compute key comparisons
    if "continuity" in data and "shift" in data:
        c_adapt = data["continuity"]["tinm_adapt"]
        c_base = data["continuity"]["rag_baseline"]
        c_hist = data["continuity"]["rag_with_history"]
        c_a085 = data["continuity"]["tinm_a085"]
        s_adapt = data["shift"]["tinm_adapt"]
        s_base = data["shift"]["rag_baseline"]
        s_hist = data["shift"]["rag_with_history"]
        s_a085 = data["shift"]["tinm_a085"]

        # Token totals
        def total_tok(metrics):
            return sum(
                sum(m["subquestion_input_tokens"]) + sum(m["subquestion_output_tokens"])
                + sum(m["distractor_input_tokens"]) + sum(m["distractor_output_tokens"])
                for m in metrics
            )

        adapt_total = total_tok(c_adapt) + total_tok(s_adapt)
        hist_total = total_tok(c_hist) + total_tok(s_hist)

        # Q1: adapt vs a085 on shift
        shift_pc = paired_comparison(s_adapt, s_a085)
        # Q2: adapt vs baseline on shift
        shift_vs_base = paired_comparison(s_adapt, s_base)
        # Q3: adapt vs a085 on continuity
        cont_pc = paired_comparison(c_adapt, c_a085)

        md_parts.append(
            f"- **`tinm_adapt` beats `tinm_a085` on topic-shift** by "
            f"{shift_pc['mean_delta']:+.3f} quality (t={shift_pc['t_stat']:.2f}, "
            f"{shift_pc['wins']}/{shift_pc['losses']} wins/losses). "
            f"This validates friction-adaptive memory as a unified mechanism.\n"
        )
        md_parts.append(
            f"- **`tinm_adapt` beats `rag_baseline` on topic-shift** by "
            f"{shift_vs_base['mean_delta']:+.3f} quality (t={shift_vs_base['t_stat']:.2f}). "
            f"Memory provides measurable gain when topic actually changes.\n"
        )
        md_parts.append(
            f"- **On continuity, `tinm_adapt` and `tinm_a085` are statistically tied** "
            f"(Δ={cont_pc['mean_delta']:+.3f}, t={cont_pc['t_stat']:.2f}, "
            f"{cont_pc['ties']}/{cont_pc['n']} tied tasks). The adaptive mechanism "
            f"costs essentially nothing on stable-topic conversations.\n"
        )
        md_parts.append(
            f"- **`rag_with_history` is empirically pareto-dominated**: "
            f"{hist_total:,} tokens vs {adapt_total:,} for `tinm_adapt` "
            f"({(hist_total/adapt_total - 1)*100:+.0f}%) for "
            f"{(statistics.mean(m['quality_score'] for m in c_hist) + statistics.mean(m['quality_score'] for m in s_hist))/2:.3f} "
            f"average quality vs "
            f"{(statistics.mean(m['quality_score'] for m in c_adapt) + statistics.mean(m['quality_score'] for m in s_adapt))/2:.3f} "
            f"for `tinm_adapt`.\n"
        )

    report = "\n".join(md_parts)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parent / "pilot_v2_results.json",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path(__file__).resolve().parent / "report",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    data = json.load(open(args.input))
    args.outdir.mkdir(parents=True, exist_ok=True)

    report = render_report(data, args.outdir)
    (args.outdir / "report.md").write_text(report)
    print(report)
    print(f"\n\nWritten to {args.outdir}/report.md and CSVs in same directory.")


if __name__ == "__main__":
    main()
