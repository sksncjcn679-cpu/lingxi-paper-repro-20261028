#!/usr/bin/env bash
# 严格质量门禁：任一静态检查、测试或覆盖率失败即非零退出。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-.venv}"
if [ -f "$VENV_DIR/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
fi

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "[tests] 缺少 $1；请先执行: pip install -e \".[dev]\"" >&2
        exit 2
    fi
}

for cmd in ruff mypy pytest coverage; do
    require_cmd "$cmd"
done

echo "[tests] ruff check"
ruff check src tests scenarios

echo "[tests] ruff format --check"
ruff format --check src tests scenarios

echo "[tests] mypy"
mypy src

echo "[tests] pytest"
pytest -q

echo "[tests] coverage（分支覆盖率，最低 80%）"
coverage erase
coverage run -m pytest -q
coverage report --fail-under=80

echo "[tests] 全部质量门禁通过。"
