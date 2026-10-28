"""Dashboard compatibility regression for current benchmark schema."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_dashboard_accepts_current_derived_metrics(tmp_path: Path):
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    summary = {
        "suite": "main",
        "repeat": 1,
        "seed": 42,
        "env": {
            "python": "3.x",
            "platform": "test",
            "source_tree_sha256": "abc123",
        },
        "results": {
            "A": {
                "label": "Text Baseline",
                "summary": {
                    "text_tokens": {"mean": 100.0},
                    "state_transfers": {"mean": 0.0},
                    "artifact_refs": {"mean": 0.0},
                },
                "derived": {
                    "success_rate": 1.0,
                    "token_saving_vs_baseline": 0.0,
                    "effective_hit_rate": 0.0,
                    "tool_call_reduction_vs_baseline": 0.0,
                },
            }
        },
    }
    (result_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    root = Path(__file__).resolve().parents[2]
    script = root / "dashboard" / "generate_dashboard.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(result_dir)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    html = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "总工具调用降低" in html
    assert "abc123" in html


def test_dashboard_escapes_labels_and_handles_legacy_missing_fields(tmp_path: Path):
    result_dir = tmp_path / "legacy"
    result_dir.mkdir()
    summary = {
        "suite": "main",
        "results": {
            "<A>": {
                "label": "<script>alert(1)</script>",
                "summary": {"text_tokens": {"mean": 10}},
                "derived": {"success_rate": 1.0},
            }
        },
    }
    (result_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    script = root / "dashboard" / "generate_dashboard.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(result_dir)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    rendered = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "0/0" in rendered  # legacy result has no state byte fields
