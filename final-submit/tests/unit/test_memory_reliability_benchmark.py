from agent_runtime.evaluation.memory_reliability_benchmark import run_memory_reliability_benchmark


def test_memory_reliability_benchmark_reduces_harmful_reuse() -> None:
    payload = run_memory_reliability_benchmark(rounds=6)
    assert payload["quality_only"]["harmful_adoptions"] > payload["reliability_governed"]["harmful_adoptions"]
    assert payload["reliability_governed"]["bad_quarantined"] is True
