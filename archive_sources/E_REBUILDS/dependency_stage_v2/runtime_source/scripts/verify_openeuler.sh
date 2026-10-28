#!/usr/bin/env bash
# openEuler 目标环境验证：完整打印证据；任一必需检查失败则最终非零退出。
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"
if [ -f "$VENV_DIR/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
fi

# Always verify *this checkout*.  A target host may already have an older agent-runtime
# installed globally; invoking bare ``pytest`` / ``python -m agent_runtime`` could otherwise
# test the stale installation and produce a false pass/fail for the submitted source tree.
if [ -x "$VENV_DIR/bin/python" ]; then
    PYTHON_BIN="$VENV_DIR/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
else
    echo "  [FAIL] Python interpreter not found." >&2
    exit 1
fi
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export AGENT_PROJECT_ROOT="$ROOT_DIR"

FAIL=0
run_check() {
    local label="$1"
    shift
    echo
    echo "## $label"
    if "$@"; then
        echo "  [PASS] $label"
    else
        local rc=$?
        echo "  [FAIL] $label (exit=$rc)" >&2
        FAIL=1
    fi
}

echo "============================================================"
echo " openEuler 兼容性验证报告 — Agent Runtime (version read from package)"
echo "============================================================"

echo "## OS / 版本"
OS_BLOB=""
if [ -f /etc/os-release ]; then
    grep -E '^(NAME|ID|VERSION|VERSION_ID)=' /etc/os-release || true
    OS_BLOB="$(cat /etc/os-release)"
fi
if [ -f /etc/openEuler-release ]; then
    cat /etc/openEuler-release
    OS_BLOB="$OS_BLOB $(cat /etc/openEuler-release)"
fi
if ! printf '%s' "$OS_BLOB" | grep -Eqi '(^|[="[:space:]])openEuler(["[:space:]]|$)'; then
    echo "  [FAIL] 当前系统不是 openEuler；本脚本不能据此签发 openEuler 通过结论。" >&2
    FAIL=1
fi
if ! printf '%s' "$OS_BLOB" | grep -Fq '24.03'; then
    echo "  [FAIL] 未检测到目标版本 24.03。" >&2
    FAIL=1
fi
if ! printf '%s' "$OS_BLOB" | grep -Fqi 'LTS-SP3'; then
    echo "  [FAIL] 未检测到目标 Service Pack LTS-SP3。" >&2
    FAIL=1
fi

echo
echo "## 内核 / 架构"
uname -r
ARCH="$(uname -m)"
echo "$ARCH"
if [ "$ARCH" != "x86_64" ]; then
    echo "  [FAIL] 目标架构要求 x86_64，当前为 $ARCH。" >&2
    FAIL=1
fi

echo
echo "## Python"
"$PYTHON_BIN" --version 2>&1 || FAIL=1

run_check "当前源码身份（防止误测旧安装）" "$PYTHON_BIN" scripts/verify_source_identity.py "$ROOT_DIR"

run_check "核心依赖" "$PYTHON_BIN" - <<'PY'
mods = ["pydantic", "numpy", "psutil", "yaml", "msgpack"]
for name in mods:
    mod = __import__(name)
    print(f"  {name}: {getattr(mod, '__version__', 'ok')}")
PY

run_check "系统能力（沙箱 / IPC / 共享内存）" "$PYTHON_BIN" - <<'PY'
from agent_runtime.sandbox.capabilities import summary
for key, value in summary().items():
    print(f"  {key}: {value}")
PY

echo
echo "## cgroup v2"
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
    echo "  可用: $(cat /sys/fs/cgroup/cgroup.controllers)"
else
    echo "  不可用（subprocess 后端仍可运行；强隔离能力减弱）"
fi

echo
echo "## 共享内存 /dev/shm"
if [ -d /dev/shm ] && [ -w /dev/shm ]; then
    echo "  可用可写"
else
    echo "  不可用（状态交换将降级到进程内传输）"
fi

run_check "Python 编译检查" "$PYTHON_BIN" -m compileall -q src scenarios
run_check "单元 + 集成测试" "$PYTHON_BIN" -m pytest -q -m "not stability"
run_check "10 轮稳定性 + 故障注入" "$PYTHON_BIN" -m pytest -q -m stability
run_check "版本身份（包版本 == pyproject 版本 / Protocol 1.3）" "$PYTHON_BIN" - <<'PY'
from agent_runtime import __version__, PROTOCOL_VERSION
print(f"  agent-runtime: {__version__}")
print(f"  protocol: {PROTOCOL_VERSION}")
import pathlib, re
root = pathlib.Path(__import__("agent_runtime").__file__).resolve().parents[2]
declared = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
print(f"  pyproject: {declared}")
assert __version__ == declared, (__version__, declared)
assert PROTOCOL_VERSION == "1.3"
PY

run_check "内核能力探测（memfd 密封映射模式 / SCM_RIGHTS / pidfd / cgroup）" "$PYTHON_BIN" -m agent_runtime.cli os-probe
run_check "Demo" "$PYTHON_BIN" -m agent_runtime.cli demo
run_check "Linux/openEuler赛题主工作负载" "$PYTHON_BIN" -m agent_runtime.cli contest-workload
run_check "Protocol-1.3 10轮通信压力矩阵" "$PYTHON_BIN" -m agent_runtime.cli competition-stress --rounds 10
run_check "Small/Medium/Large ×10 规模曲线与95%CI" "$PYTHON_BIN" -m agent_runtime.cli scale-benchmark --repeat 10
run_check "Memory Fast Path 10轮等成功率实验" "$PYTHON_BIN" -m agent_runtime.cli memory-efficiency --repeat 10
run_check "生成当前源码结果索引" "$PYTHON_BIN" scripts/build_current_index.py
run_check "结果来源 / 原生 Linux 门禁" "$PYTHON_BIN" scripts/verify_results.py --require-linux
if [ "$FAIL" -eq 0 ]; then
    AGENT_OPENEULER_PRECHECKS="$("$PYTHON_BIN" -c 'from agent_runtime.evaluation.runner import _env_info; print(_env_info()["source_tree_sha256"])')"
    export AGENT_OPENEULER_PRECHECKS
    echo "  [SIGN] prechecks passed; signing source ${AGENT_OPENEULER_PRECHECKS:0:12} for the target-OS gate"
else
    unset AGENT_OPENEULER_PRECHECKS
    echo "  [NO-SIGN] earlier checks failed; the target-OS gate will stay PENDING" >&2
fi
run_check "赛题逐条验收（含10轮与目标OS门禁）" "$PYTHON_BIN" -m agent_runtime.cli competition-check --rounds 10 --require-openeuler

echo "============================================================"
if [ "$FAIL" -eq 0 ]; then
    echo " 验证通过。请保存本输出并回填 docs/OPENEULER_24_03_LTS_SP3_EXTERNAL_VERIFICATION.md"
else
    echo " 验证存在失败项；不要将本环境标记为 openEuler 已验证。" >&2
fi
echo "============================================================"
exit "$FAIL"
