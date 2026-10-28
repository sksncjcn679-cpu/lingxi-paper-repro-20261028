"""IPC benchmark smoke/regression tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agent_runtime.evaluation.ipc_benchmark import run_ipc_benchmark, write_ipc_results


def test_ipc_benchmark_measures_exact_copy_vs_descriptor_wire_bytes():
    payload = run_ipc_benchmark(sizes=(1024, 4096), repeat=3, warmup=1)
    assert payload["suite"] == "ipc"
    assert len(payload["cases"]) == 2
    for case in payload["cases"]:
        assert case["checksum"] >= 0
        assert case["copy"]["wire_bytes"] > case["payload_bytes"]
        assert case["shm"]["control_wire_bytes"] < case["copy"]["wire_bytes"]
        assert case["shm"]["materialized_bytes"] == case["payload_bytes"]
        assert case["derived"]["transfer_wire_reduction"] > 0.5
        assert case["copy"]["latency"]["median_ms"] > 0
        assert case["shm"]["latency"]["median_ms"] > 0


def test_ipc_results_include_valid_manifest(tmp_path: Path):
    payload = run_ipc_benchmark(sizes=(1024,), repeat=2, warmup=0)
    out = write_ipc_results(payload, root=str(tmp_path / "results"))
    assert (out / "summary.json").is_file()
    assert (out / "report.md").is_file()
    manifest = (out / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    assert manifest
    for line in manifest:
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
