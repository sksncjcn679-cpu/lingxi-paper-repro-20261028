#!/usr/bin/env bash
# fable7: regenerate every result suite for the CURRENT source tree (one SHA), then index/verify.
# Usage: bash scripts/run_all_suites.sh [LOG]
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${1:-/tmp/agent-runtime-fable7-all-suites.log}"
cd "$ROOT"
# OS-SOTA1.6: pick an interpreter and never trust PYTHONPATH separators (':' vs ';' on Windows).
PYBIN="${PYTHON:-python3}"
command -v "$PYBIN" >/dev/null 2>&1 || PYBIN=python
SEP="$("$PYBIN" -c 'import os; print(os.pathsep)')"
SRC_NATIVE="$("$PYBIN" -c 'import sys, pathlib; print(pathlib.Path(sys.argv[1]).resolve())' "$ROOT/src")"
export PYTHONPATH="$SRC_NATIVE${PYTHONPATH:+$SEP$PYTHONPATH}"
export AGENT_PROJECT_ROOT="$ROOT"
: > "$LOG"
FAILED=0
run_suite() {
    printf '\n===== %s =====\n' "$*" | tee -a "$LOG"
    "$@" >> "$LOG" 2>&1
    local rc=$?
    [ $rc -ne 0 ] && FAILED=$((FAILED+1))
    printf '===== EXIT:%s =====\n' "$rc" | tee -a "$LOG"
}
PY=("$PYBIN" "$ROOT/scripts/rt.py")
# Identity preflight: abort the whole run if a stale install would be tested instead of this tree.
if ! "$PYBIN" "$ROOT/scripts/rt.py" version >/dev/null 2>"$LOG.preflight"; then
    cat "$LOG.preflight"; echo "preflight failed: not running suites against the wrong source tree" | tee -a "$LOG"; exit 97
fi
printf 'SOURCE_TREE %s\n' "$("$PYBIN" "$ROOT/scripts/verify_source_identity.py" 2>/dev/null | tail -1)" | tee -a "$LOG"
run_suite "${PY[@]}" os-probe
run_suite "${PY[@]}" contest-workload
run_suite "${PY[@]}" mechanism-ablation --repeat 8
run_suite "${PY[@]}" long-horizon
run_suite "${PY[@]}" decision-certified-state --plans 300
run_suite "${PY[@]}" agent-isolation --repeat 3
run_suite "${PY[@]}" sandbox-security --repeat 40
run_suite "${PY[@]}" bilingual-memory
run_suite "${PY[@]}" competition-stress
run_suite "${PY[@]}" scale-benchmark --repeat 10
run_suite "${PY[@]}" benchmark --suite main --repeat 5 --seed 42
run_suite "${PY[@]}" benchmark --suite ablation --repeat 5 --seed 42
run_suite "${PY[@]}" benchmark --suite memory --repeat 5 --seed 42
run_suite "${PY[@]}" benchmark --suite memory_guard --repeat 5 --seed 42
run_suite "${PY[@]}" benchmark --suite state --repeat 5 --seed 42
run_suite "${PY[@]}" benchmark --suite reference_first --repeat 5 --seed 42
run_suite "${PY[@]}" benchmark --suite text_fairness --repeat 5 --seed 42
run_suite "${PY[@]}" benchmark --suite versioned_pipeline --repeat 5 --seed 42
run_suite "${PY[@]}" ipc-benchmark --sizes 1024,65536,1048576 --repeat 30 --warmup 5
run_suite "${PY[@]}" codec-benchmark --repeat 2000
run_suite "${PY[@]}" mailbox-benchmark --echo-repeat 1000 --parallel-requests 240 --service-ms 2
run_suite "${PY[@]}" reliability-benchmark --repeat 30 --warmup 3
run_suite "${PY[@]}" idempotency-benchmark --repeat 20
run_suite "${PY[@]}" resilience-benchmark
run_suite "${PY[@]}" state-delta --repeat 20
run_suite "${PY[@]}" state-quantization --samples 200 --dim 256
run_suite "${PY[@]}" memory-governance
run_suite "${PY[@]}" memory-reliability --rounds 6
run_suite "${PY[@]}" memory-efficiency --repeat 10
run_suite "${PY[@]}" memory-accuracy
run_suite "${PY[@]}" memory-scale
run_suite "${PY[@]}" memory-consolidation
run_suite "${PY[@]}" memory-hard-set
run_suite "${PY[@]}" retrieval-expanded
run_suite "${PY[@]}" candidate-workflow --repeat 5
run_suite "${PY[@]}" decision-workload --repeat 5
if "$PYBIN" -c 'import sys; sys.exit(0 if sys.platform.startswith("linux") else 1)'; then
    run_suite "${PY[@]}" decision-workload --repeat 5 --agent-processes
fi
run_suite "${PY[@]}" stability-benchmark --rounds 10
run_suite "${PY[@]}" fd-passing-benchmark --repeat 30
run_suite "${PY[@]}" large-state-workload --repeat 5 --dim 300000
run_suite "${PY[@]}" concurrency-benchmark --repeat 5
run_suite "${PY[@]}" chaos-benchmark
run_suite "${PY[@]}" session-dictionary --repeat 3
run_suite "${PY[@]}" latency-profile --repeat 10
run_suite "${PY[@]}" cgroup-benchmark
run_suite "${PY[@]}" external-llm --allow-mock --repeat 2
# OS-SOTA1: billing decomposition of the archived real-LLM rerun + coalescing / group-commit experiment
run_suite "${PY[@]}" billing-model
run_suite "${PY[@]}" coalesce-benchmark --repeat 3 --rounds 10
AGENT_LLM_PROVIDER=mock-gateway run_suite "${PY[@]}" external-llm --allow-mock --repeat 3
AGENT_LLM_PROVIDER=mock-gateway run_suite "${PY[@]}" long-session --allow-mock --rounds 10
run_suite "${PY[@]}" competition-check --rounds 10
run_suite "$PYBIN" scripts/build_current_index.py
# OS-SOTA1.5: informational gate report (never fails the run; PENDING until real/openEuler evidence exists)
run_suite "${PY[@]}" sota2-gate
run_suite "$PYBIN" scripts/verify_results.py --require-linux
run_suite "${PY[@]}" criteria-report
printf '\nALL_SUITES_DONE failed=%s\n' "$FAILED" | tee -a "$LOG"
exit "$FAILED"
