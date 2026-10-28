from agent_runtime.evaluation.resilience_benchmark import run_resilience_benchmark


def test_resilience_benchmark_quantifies_failure_amplification_controls():
    payload = run_resilience_benchmark()
    r = payload["results"]
    assert r["R0"]["handle_calls"] == 6
    assert r["R1"]["handle_calls"] == 2
    assert r["R2"]["handle_calls"] == 1
    assert r["R1"]["failed_work_reduction_vs_R0"] > 0.60
    assert r["R2"]["failed_work_reduction_vs_R0"] > 0.80
