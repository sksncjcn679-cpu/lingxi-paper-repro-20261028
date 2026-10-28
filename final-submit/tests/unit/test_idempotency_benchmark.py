from __future__ import annotations

from agent_runtime.evaluation.idempotency_benchmark import run_idempotency_benchmark


def test_idempotency_benchmark_avoids_completed_stage_execution():
    payload = run_idempotency_benchmark(repeat=2)
    s = payload["summary"]
    assert s["first_mean_llm_calls"] == 4.0
    assert s["replay_mean_llm_calls"] == 0.0
    assert s["llm_call_avoidance"] == 1.0
    assert s["replay_events_per_run"] == 4.0
    assert s["first_latency_ms"]["median"] > 0
    assert s["replay_latency_ms"]["median"] > 0
