"""Render Figure 1 (Pareto: quality vs token cost across the 5 benchmarks).

Reads benchmark/runs/paper_results/table_pareto.csv and writes
paper/figures/fig1_pareto.{pdf,png}.

Usage:
    /Users/user/TNIM/benchmark/.venv/bin/python paper/figures/make_fig1.py
"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = REPO_ROOT / "benchmark" / "runs" / "paper_results" / "table_pareto.csv"
OUT_DIR = Path(__file__).resolve().parent


# Display order + human-friendly titles
PANEL_ORDER = [
    ("continuity", "Synthetic continuity"),
    ("shift", "Synthetic topic shift"),
    ("musique_2hop", "MuSiQue 2-hop (n=100)"),
    ("musique_3hop", "MuSiQue 3-hop"),
    ("wiki2hop", "2WikiMultihopQA"),
]

# Marker + colour per agent (consistent across panels)
AGENT_STYLE = {
    "rag_baseline":     {"marker": "D", "color": "#888888", "label": "rag_baseline"},
    "rag_with_history": {"marker": "o", "color": "#d62728", "label": "rag_with_history"},
    "tinm_a085":        {"marker": "^", "color": "#1f77b4", "label": "tinm_a085"},
    "tinm_adapt":       {"marker": "*", "color": "#2ca02c", "label": "tinm_adapt"},
}


def load_rows() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            out.setdefault(row["benchmark"], []).append(row)
    return out


def main() -> None:
    rows_by_bench = load_rows()

    fig, axes = plt.subplots(1, 5, figsize=(15, 3.4), constrained_layout=True)

    for ax, (bench_key, title) in zip(axes, PANEL_ORDER):
        rows = rows_by_bench[bench_key]
        # Plot each agent
        for r in rows:
            agent = r["agent"]
            style = AGENT_STYLE[agent]
            x = float(r["tokens"]) / 1000.0
            y = float(r["quality"])
            is_best = r["is_pareto_best"] == "True"
            ax.scatter(
                x, y,
                marker=style["marker"],
                color=style["color"],
                s=180 if is_best else 90,
                edgecolors="black" if is_best else "none",
                linewidths=1.6 if is_best else 0,
                zorder=3,
                label=style["label"],
            )
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Total tokens (k)", fontsize=9)
        ax.grid(True, linestyle=":", alpha=0.4, zorder=1)
        ax.tick_params(labelsize=8)

    axes[0].set_ylabel("Quality (hybrid score)", fontsize=10)

    # One legend at the figure level, not per-panel
    handles = [
        plt.Line2D(
            [0], [0],
            marker=style["marker"],
            color="white",
            markerfacecolor=style["color"],
            markeredgecolor="black",
            markersize=10,
            label=style["label"],
        )
        for style in AGENT_STYLE.values()
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=4,
        bbox_to_anchor=(0.5, -0.06),
        frameon=False,
        fontsize=9,
    )

    fig.suptitle(
        "Figure 1. Quality vs. token cost across five benchmarks. "
        "Pareto-optimal agent per panel is bordered.",
        fontsize=10,
        y=1.04,
    )

    pdf = OUT_DIR / "fig1_pareto.pdf"
    png = OUT_DIR / "fig1_pareto.png"
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, bbox_inches="tight", dpi=180)
    print(f"Wrote {pdf}")
    print(f"Wrote {png}")


if __name__ == "__main__":
    main()
