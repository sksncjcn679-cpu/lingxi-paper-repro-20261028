from agent_runtime.evaluation.state_quantization_benchmark import run_state_quantization_benchmark


def test_state_quantization_benchmark_counts_int8_scale_and_fidelity():
    payload = run_state_quantization_benchmark(samples=20, dim=64)
    r = payload["results"]
    assert r["float32"]["materialization_saving"] == 0.0
    assert r["int8"]["total_physical_bytes"] < r["float16"]["total_physical_bytes"]
    assert r["int8"]["materialization_saving"] > 0.70
    assert r["int8"]["cosine"]["min"] > 0.999
