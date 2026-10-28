"""CLI 入口回归测试：评委/第三方复现首先接触的执行面。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from agent_runtime.cli import main
from agent_runtime.evaluation.runner import run_suite, write_results


def test_version_command(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert "agent-runtime" in out
    assert "protocol" in out


def test_demo_command(capsys):
    assert main(["--log-level", "ERROR", "demo"]) == 0
    out = capsys.readouterr().out
    assert "Demo" in out
    assert "Token" in out
    assert "success" in out


def test_report_command_regenerates_report(tmp_path: Path, capsys):
    payload = run_suite("main", repeat=1, seed=5)
    out_dir = write_results(payload, root=str(tmp_path))
    (out_dir / "report.md").unlink()
    assert main(["report", "--results", str(out_dir)]) == 0
    assert (out_dir / "report.md").is_file()
    assert "实验报告" in capsys.readouterr().out


def test_module_cli_runs_from_outside_repository(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "agent_runtime.cli", "--log-level", "ERROR", "demo"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Token" in proc.stdout
