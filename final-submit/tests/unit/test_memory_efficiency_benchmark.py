from agent_runtime.evaluation.memory_efficiency import run_memory_efficiency_benchmark


def test_memory_efficiency_is_equal_success_and_reduces_tool_compute():
    p = run_memory_efficiency_benchmark(repeat=2)
    assert p["summary"]["success_equal"] is True
    assert p["summary"]["E1"]["memory_used_mean"] > 0
    assert p["summary"]["E1"]["memory_effective_mean"] > 0
    assert p["summary"]["tool_call_reduction"] > 0


def test_memory_efficiency_artifacts_have_manifest(tmp_path):
    from agent_runtime.evaluation.memory_efficiency import write_memory_efficiency_results
    p = run_memory_efficiency_benchmark(repeat=1)
    out = write_memory_efficiency_results(p, root=str(tmp_path))
    assert (out / "summary.json").exists()
    assert "report.md" in (out / "MANIFEST.sha256").read_text()
