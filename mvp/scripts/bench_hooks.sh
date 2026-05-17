#!/usr/bin/env bash
# bench_hooks.sh — Measure p50/p95/p99 wall-time latency of TINM hooks.
#
# Hard gate for v0.3.0: p99 < 200ms for session_start.sh AND user_prompt.sh.
#
# Methodology
# -----------
#   * Isolated TINM_HOME under a fresh mktemp so the benchmark never
#     touches the real ~/.tinm. The system venv is symlinked into the
#     temp home so $TINM_HOME/.venv/bin/python resolves the same way
#     production hooks expect.
#   * Warmup: 5 invocations per hook (results discarded — covers cold
#     import cache, fingerprint resolve + thread creation, etc.).
#   * Measure: 50 invocations per hook. Wall time captured via Python
#     time.perf_counter() bracketing subprocess.run, in ms.
#   * Percentiles computed via Python statistics on the raw 50 samples.
#
# Output
#   * Human stdout: "SessionStart: p50=Xms p95=Yms p99=Zms (50 runs)"
#   * JSON file:    /root/TNIM/benchmark/hook_perf_v0.3.0.json
#
# Exit codes
#   0  both hooks p99 < 200ms (gate PASS)
#   1  at least one hook p99 >= 200ms (gate FAIL)
#   2  setup error (venv missing, hook missing, etc.)

set -euo pipefail

# ---- config ----------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOKS_DIR="$REPO_ROOT/mvp/hooks"
SESSION_HOOK="$HOOKS_DIR/session_start.sh"
PROMPT_HOOK="$HOOKS_DIR/user_prompt.sh"

REAL_VENV="${REAL_TINM_HOME:-$HOME/.tinm}/.venv"
REAL_PY="$REAL_VENV/bin/python"

OUT_DIR="$REPO_ROOT/benchmark"
OUT_JSON="$OUT_DIR/hook_perf_v0.3.0.json"

WARMUP=5
MEASURE=50
GATE_P99_MS=200

# ---- preflight -------------------------------------------------------------
for f in "$SESSION_HOOK" "$PROMPT_HOOK"; do
    if [ ! -x "$f" ]; then
        echo "ERROR: hook not executable: $f" >&2
        exit 2
    fi
done
if [ ! -x "$REAL_PY" ]; then
    echo "ERROR: real venv python not found at $REAL_PY (run install.sh first)" >&2
    exit 2
fi

# ---- isolated TINM_HOME ----------------------------------------------------
BENCH_TINM_HOME="$(mktemp -d -t tinm-bench-XXXXXX)"
BENCH_LOG="$BENCH_TINM_HOME/bench.log"
trap 'rm -rf "$BENCH_TINM_HOME"' EXIT

# Symlink the real venv so the hooks find $TINM_HOME/.venv/bin/python.
ln -s "$REAL_VENV" "$BENCH_TINM_HOME/.venv"
mkdir -p "$BENCH_TINM_HOME/pcp"

export TINM_HOME="$BENCH_TINM_HOME"
export TINM_PCP_DIR="$BENCH_TINM_HOME/pcp"

# Fake transcript for user_prompt.sh's compaction-detect + L1 heuristics.
FAKE_TRANSCRIPT="$BENCH_TINM_HOME/transcript.jsonl"
: > "$FAKE_TRANSCRIPT"

# Stable synthetic session id — keeps per-session files in the temp dir.
SESSION_ID="bench-$(date +%s)-$$"

# Realistic payloads (single-line JSON; hook reads stdin once).
SESSION_PAYLOAD=$(printf '{"session_id":"%s","transcript_path":"%s","cwd":"%s","hook_event_name":"SessionStart"}' \
    "$SESSION_ID" "$FAKE_TRANSCRIPT" "$REPO_ROOT")
PROMPT_PAYLOAD=$(printf '{"session_id":"%s","transcript_path":"%s","cwd":"%s","hook_event_name":"UserPromptSubmit","prompt":"%s"}' \
    "$SESSION_ID" "$FAKE_TRANSCRIPT" "$REPO_ROOT" \
    "What did we ship earlier? Like we discussed previously about thread isolation.")

# Drive a SessionStart once to create the thread + handoff file so
# subsequent user_prompt.sh runs find THREAD_ID. This also primes the
# kernel page cache for fairness across the two hooks.
printf '%s' "$SESSION_PAYLOAD" | bash "$SESSION_HOOK" >/dev/null 2>>"$BENCH_LOG" || true

# ---- driver: python harness that times subprocesses ------------------------
# Single Python call per hook so percentile math stays in one place.
run_hook() {
    local hook_path="$1"
    local payload="$2"
    local label="$3"

    HOOK="$hook_path" PAYLOAD="$payload" LABEL="$label" \
        WARMUP="$WARMUP" MEASURE="$MEASURE" LOG="$BENCH_LOG" \
        "$REAL_PY" - <<'PYEOF'
import json, os, statistics, subprocess, sys, time

hook = os.environ["HOOK"]
payload = os.environ["PAYLOAD"].encode("utf-8")
label = os.environ["LABEL"]
warmup = int(os.environ["WARMUP"])
measure = int(os.environ["MEASURE"])
log_path = os.environ["LOG"]

def one_call():
    t0 = time.perf_counter()
    r = subprocess.run(
        ["bash", hook],
        input=payload,
        capture_output=True,
        timeout=30,
    )
    t1 = time.perf_counter()
    return (t1 - t0) * 1000.0, r.returncode, r.stderr

# Warmup
for _ in range(warmup):
    one_call()

# Measure
samples = []
nonzero_exits = 0
with open(log_path, "ab") as logf:
    for _ in range(measure):
        ms, rc, err = one_call()
        samples.append(ms)
        if rc != 0:
            nonzero_exits += 1
            logf.write(f"[{label}] rc={rc} stderr={err[:200]!r}\n".encode())

samples.sort()
def pct(p):
    # Nearest-rank percentile on sorted list (1-indexed).
    k = max(0, min(len(samples) - 1, int(round(p / 100.0 * len(samples))) - 1))
    return samples[k]

print(json.dumps({
    "label": label,
    "runs": len(samples),
    "p50": round(statistics.median(samples), 2),
    "p95": round(pct(95), 2),
    "p99": round(pct(99), 2),
    "min": round(min(samples), 2),
    "max": round(max(samples), 2),
    "mean": round(statistics.mean(samples), 2),
    "nonzero_exits": nonzero_exits,
}))
PYEOF
}

echo "TINM hook benchmark"
echo "  TINM_HOME=$BENCH_TINM_HOME"
echo "  warmup=$WARMUP  measure=$MEASURE  gate=p99<${GATE_P99_MS}ms"
echo

SESSION_JSON="$(run_hook "$SESSION_HOOK" "$SESSION_PAYLOAD" session_start)"
PROMPT_JSON="$(run_hook "$PROMPT_HOOK" "$PROMPT_PAYLOAD" user_prompt)"

# ---- report + gate ---------------------------------------------------------
GIT_SHA="$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$OUT_DIR"

REPO_ROOT="$REPO_ROOT" GATE="$GATE_P99_MS" \
SESSION_JSON="$SESSION_JSON" PROMPT_JSON="$PROMPT_JSON" \
GIT_SHA="$GIT_SHA" NOW_UTC="$NOW_UTC" OUT_JSON="$OUT_JSON" \
LOG="$BENCH_LOG" \
"$REAL_PY" - <<'PYEOF'
import json, os, pathlib, sys
ss = json.loads(os.environ["SESSION_JSON"])
up = json.loads(os.environ["PROMPT_JSON"])
gate = float(os.environ["GATE"])
ss_pass = ss["p99"] < gate
up_pass = up["p99"] < gate
gate_pass = ss_pass and up_pass

def fmt(d):
    return f'p50={d["p50"]}ms p95={d["p95"]}ms p99={d["p99"]}ms (mean={d["mean"]}ms, runs={d["runs"]})'

print(f"SessionStart:    {fmt(ss)}   {'PASS' if ss_pass else 'FAIL'}")
print(f"UserPromptSubmit:{fmt(up)}   {'PASS' if up_pass else 'FAIL'}")
print()
print(f"Gate (p99 < {gate}ms): {'PASS' if gate_pass else 'FAIL'}")

payload = {
    "date": os.environ["NOW_UTC"],
    "git_sha": os.environ["GIT_SHA"],
    "gate_threshold_ms": gate,
    "gate_pass": gate_pass,
    "hooks": {
        "session_start": ss,
        "user_prompt": up,
    },
    "log_file": os.environ["LOG"],
}
pathlib.Path(os.environ["OUT_JSON"]).write_text(json.dumps(payload, indent=2) + "\n")
print(f"\nResults: {os.environ['OUT_JSON']}")
sys.exit(0 if gate_pass else 1)
PYEOF
