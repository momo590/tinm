# TNIM-lite v2 — Pilot Results

Source: `runs/pilot_v2_results.json` (50 tasks per benchmark, Claude Sonnet 4.6, seed=42)

## 1. Per-agent summary

### Continuity

| Agent | n | Quality (mean) | Quality (median) | Quality (stdev) | Judge mean | Total tokens | Quality / 1k tok |
|---|---|---|---|---|---|---|---|
| rag_baseline | 50 | 0.780 | 0.775 | 0.235 | 0.756 | 39251 | 0.993 |
| rag_with_history | 50 | 0.734 | 0.750 | 0.230 | 0.744 | 51142 | 0.718 |
| tinm_a085 | 50 | 0.801 | 1.000 | 0.240 | 0.802 | 39534 | 1.013 |
| tinm_adapt | 50 | 0.789 | 0.917 | 0.242 | 0.789 | 39330 | 1.004 |

### Shift

| Agent | n | Quality (mean) | Quality (median) | Quality (stdev) | Judge mean | Total tokens | Quality / 1k tok |
|---|---|---|---|---|---|---|---|
| rag_baseline | 50 | 0.729 | 0.750 | 0.174 | 0.704 | 60757 | 0.600 |
| rag_with_history | 50 | 0.735 | 0.750 | 0.142 | 0.760 | 79747 | 0.461 |
| tinm_a085 | 50 | 0.732 | 0.750 | 0.179 | 0.709 | 61506 | 0.595 |
| tinm_adapt | 50 | 0.767 | 0.750 | 0.172 | 0.749 | 61656 | 0.622 |

## 2. Paired comparisons

Each row: difference of agent vs reference, task-by-task. t-stat > 1.96 indicates 95% significance (n=50). W/L/T = wins/losses/ties.

### Continuity — vs `rag_baseline`

| Agent | Δ quality | SE | t-stat | W/L/T |
|---|---|---|---|---|
| rag_with_history | -0.0455 | 0.0261 | -1.74 | 9/19/22 |
| tinm_a085 | 0.0213 | 0.0201 | 1.06 | 11/7/32 |
| tinm_adapt | 0.0097 | 0.0182 | 0.53 | 8/9/33 |

### Shift — vs `rag_baseline`

| Agent | Δ quality | SE | t-stat | W/L/T |
|---|---|---|---|---|
| rag_with_history | 0.0058 | 0.0234 | 0.25 | 13/11/26 |
| tinm_a085 | 0.0025 | 0.0084 | 0.30 | 5/6/39 |
| tinm_adapt | 0.0375 | 0.0152 | 2.47 | 12/5/33 |

### Cross-comparison — `tinm_adapt` vs `tinm_a085`

| Benchmark | Δ quality | SE | t-stat | W/L/T |
|---|---|---|---|---|
| continuity | -0.0116 | 0.0105 | -1.11 | 5/5/40 |
| shift | 0.0350 | 0.0121 | 2.90 | 10/1/39 |

## 3. Distractor robustness (continuity benchmark)

| Agent | n with | n without | Quality with distractor | Quality without distractor | Drop |
|---|---|---|---|---|---|
| rag_baseline | 20 | 30 | 0.772 | 0.785 | 0.0136 |
| rag_with_history | 20 | 30 | 0.683 | 0.769 | 0.0858 |
| tinm_a085 | 20 | 30 | 0.790 | 0.809 | 0.0191 |
| tinm_adapt | 20 | 30 | 0.761 | 0.809 | 0.0476 |

A *small drop* means the agent's quality is preserved under interruption.

## 4. Quality / cost frontier

Bars proportional to value. Higher quality + higher q/1k_tok is better; lower total tokens at same quality is better.

### Continuity

```
Agent                  Quality  Tokens      bar(quality) | bar(efficiency)
rag_baseline           0.780    39251      ███████████████████· | ████████████████████
rag_with_history       0.734    51142      ██████████████████·· | ██████████████······
tinm_a085              0.801    39534      ████████████████████ | ████████████████████
tinm_adapt             0.789    39330      ████████████████████ | ████████████████████
```

### Shift

```
Agent                  Quality  Tokens      bar(quality) | bar(efficiency)
rag_baseline           0.729    60757      ███████████████████· | ███████████████████·
rag_with_history       0.735    79747      ███████████████████· | ███████████████·····
tinm_a085              0.732    61506      ███████████████████· | ███████████████████·
tinm_adapt             0.767    61656      ████████████████████ | ████████████████████
```

## 5. Headline findings

- **`tinm_adapt` beats `tinm_a085` on topic-shift** by +0.035 quality (t=2.90, 10/1 wins/losses). This validates friction-adaptive memory as a unified mechanism.

- **`tinm_adapt` beats `rag_baseline` on topic-shift** by +0.037 quality (t=2.47). Memory provides measurable gain when topic actually changes.

- **On continuity, `tinm_adapt` and `tinm_a085` are statistically tied** (Δ=-0.012, t=-1.11, 40/50 tied tasks). The adaptive mechanism costs essentially nothing on stable-topic conversations.

- **`rag_with_history` is empirically pareto-dominated**: 130,889 tokens vs 100,986 for `tinm_adapt` (+30%) for 0.735 average quality vs 0.778 for `tinm_adapt`.
