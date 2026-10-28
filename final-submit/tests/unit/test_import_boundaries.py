"""包级导入边界回归测试：防止低层子模块被公共 API 的 eager import 拉成环。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _fresh_python(code: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_direct_code_strategy_import_has_no_cycle():
    proc = _fresh_python(
        "from agent_runtime.agents.code_strategies import apply_strategy; "
        "assert apply_strategy('guard_zero_division', 'def f(a,b):\\n    return a/b\\n')"
    )
    assert proc.returncode == 0, proc.stderr


def test_runtime_public_api_still_imports_lazily():
    proc = _fresh_python(
        "from agent_runtime.runtime import Orchestrator, Task, run_task; "
        "assert Orchestrator and Task and run_task"
    )
    assert proc.returncode == 0, proc.stderr


def test_packaged_scenario_imports_without_repository_on_sys_path(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from agent_runtime.scenarios.demo import make_demo_task; "
            "assert make_demo_task().expected['answer'] == 12",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
