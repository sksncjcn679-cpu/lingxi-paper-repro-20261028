from __future__ import annotations

import hashlib
import json

import pytest

from agent_runtime.evaluation.idempotency_benchmark import (
    run_idempotency_benchmark,
    write_idempotency_results,
)
from agent_runtime.evaluation.resilience_benchmark import (
    run_resilience_benchmark,
    write_resilience_results,
)
from agent_runtime.evaluation.state_quantization_benchmark import (
    run_state_quantization_benchmark,
    write_state_quantization_results,
)


def _assert_manifest(out):
    assert (out / "summary.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "metadata.json").is_file()
    manifest = out / "MANIFEST.sha256"
    assert manifest.is_file()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["env"]["source_tree_sha256"]


def test_idempotency_result_bundle_is_self_verifying(tmp_path):
    payload = run_idempotency_benchmark(repeat=1)
    out = write_idempotency_results(payload, root=str(tmp_path))
    _assert_manifest(out)
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "does not claim distributed exactly-once" in report
    assert "100.0%" in report  # mock benchmark avoids all four LLM stage calls


def test_resilience_result_bundle_is_self_verifying(tmp_path):
    payload = run_resilience_benchmark()
    out = write_resilience_results(payload, root=str(tmp_path))
    _assert_manifest(out)
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "Synthetic always-failing agent" in report
    assert "R0" in report and "R2" in report


def test_state_quantization_result_bundle_is_self_verifying(tmp_path):
    payload = run_state_quantization_benchmark(samples=8, dim=32)
    out = write_state_quantization_results(payload, root=str(tmp_path))
    _assert_manifest(out)
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "int8" in report
    assert "not a claim about real-model retrieval accuracy" in report


def test_new_benchmarks_reject_invalid_parameters():
    with pytest.raises(ValueError, match="repeat"):
        run_idempotency_benchmark(repeat=0)
    with pytest.raises(ValueError, match="samples and dim"):
        run_state_quantization_benchmark(samples=0, dim=32)
    with pytest.raises(ValueError, match="samples and dim"):
        run_state_quantization_benchmark(samples=1, dim=0)
