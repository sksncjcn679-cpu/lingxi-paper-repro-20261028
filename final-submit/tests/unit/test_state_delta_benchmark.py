from agent_runtime.evaluation.state_delta_benchmark import run_state_delta_benchmark


def test_state_delta_benchmark_reports_real_savings_and_fallbacks() -> None:
    payload = run_state_delta_benchmark(repeat=2)
    assert payload["suite"] == "state_delta"
    assert any(c["kind"] == "delta" and c["saving"] > 0.5 for c in payload["cases"])
    assert any(c["kind"] == "snapshot" for c in payload["cases"])
    assert payload["chain"]["saving"] > 0.5
