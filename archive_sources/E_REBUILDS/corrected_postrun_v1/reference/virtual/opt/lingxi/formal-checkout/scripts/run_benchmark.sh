#!/usr/bin/env bash
# v0.6.0 全套正式评测：赛题核心证据优先 + Runtime reliability 扩展证据。
# 每个 CLI 调用都会生成独立带时间戳目录，不覆盖历史结果。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"
if [ -f "$VENV_DIR/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
fi

REPEAT="${1:-5}"
SEED="${2:-42}"

run() { echo; echo "[benchmark] $*"; "$@"; }

run python -m agent_runtime.cli benchmark --suite main --repeat "$REPEAT" --seed "$SEED"
run python -m agent_runtime.cli benchmark --suite ablation --repeat "$REPEAT" --seed "$SEED"
run python -m agent_runtime.cli benchmark --suite memory --repeat "$REPEAT" --seed "$SEED"
run python -m agent_runtime.cli benchmark --suite memory_guard --repeat "$REPEAT" --seed "$SEED"
run python -m agent_runtime.cli benchmark --suite state --repeat "$REPEAT" --seed "$SEED"
run python -m agent_runtime.cli memory-efficiency --repeat 10
run python -m agent_runtime.cli memory-accuracy
run python -m agent_runtime.cli stability-benchmark --rounds 10

# Micro/synthetic suites use their own repeat semantics. These values match the
# scale used by the current v0.5 evidence while keeping a one-command workflow.
run python -m agent_runtime.cli ipc-benchmark --sizes 1024,65536,1048576 --repeat 50 --warmup 8
run python -m agent_runtime.cli codec-benchmark --repeat 2000
run python -m agent_runtime.cli memory-governance
run python -m agent_runtime.cli reliability-benchmark --repeat 30 --warmup 3
run python -m agent_runtime.cli mailbox-benchmark --echo-repeat 1000 --parallel-requests 240 --service-ms 2
run python -m agent_runtime.cli idempotency-benchmark --repeat 20
run python -m agent_runtime.cli resilience-benchmark
run python -m agent_runtime.cli state-quantization --samples 200 --dim 256

echo
run python -m agent_runtime.cli competition-check --rounds 10

echo "[benchmark] 完成。赛题核心证据 + 扩展证据均已生成；请不要跨源码 SHA 混用结果。"
