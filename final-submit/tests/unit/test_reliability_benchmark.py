from agent_runtime.evaluation.reliability_benchmark import run_reliability_benchmark


def test_reliability_benchmark_reports_all_durability_modes():
    payload = run_reliability_benchmark(repeat=2, warmup=0)
    assert list(payload["results"]) == ["off", "buffered", "task", "sync"]
    assert payload["results"]["off"]["events_mean"] == 0
    for mode in ("buffered", "task", "sync"):
        assert payload["results"][mode]["events_mean"] > 0
        assert payload["results"][mode]["journal_bytes_mean"] > 0
        assert payload["results"][mode]["overhead_vs_off"] is not None
