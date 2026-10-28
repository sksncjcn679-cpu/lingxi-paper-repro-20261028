import os

import pytest

from agent_runtime.evaluation.mailbox_benchmark import run_mailbox_benchmark


def test_mailbox_benchmark_reports_overhead_parallelism_and_backpressure():
    # A 1 ms sleep is below the scheduling granularity on Windows and makes the
    # speedup assertion measure timer noise rather than mailbox parallelism.
    payload = run_mailbox_benchmark(echo_repeat=20, parallel_requests=40, service_ms=10.0)
    r = payload["results"]
    assert r["echo"]["processed"] == 20
    assert r["worker_1"]["processed"] == 40
    assert r["worker_4"]["processed"] == 40
    if os.name == "nt":
        pytest.skip("Windows event-loop timer noise is not a valid parallel speedup assertion")
    assert r["parallel_speedup_4_vs_1"] > 1.0
    assert r["backpressure"]["rejected"] == r["backpressure"]["probe_attempts"]
