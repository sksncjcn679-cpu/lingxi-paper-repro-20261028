from __future__ import annotations

import socket

from agent_runtime.evaluation import topology_benchmark


def test_topology_benchmark_uses_native_transport_and_alternates_pairs(monkeypatch):
    calls = []

    def fake_run_once(config, _task_factory, seed):
        calls.append((config.pipeline_topology, config.control_transport, seed))
        return {
            "task_success": 1,
            "message_count": 9 if config.pipeline_topology == "request_reply" else 5,
            "text_tokens": 100 if config.pipeline_topology == "request_reply" else 80,
            "wire_bytes": 400 if config.pipeline_topology == "request_reply" else 320,
            "state_bytes": 0,
            "artifact_bytes": 0,
            "latency_sec": 0.01 if config.pipeline_topology == "request_reply" else 0.009,
        }

    monkeypatch.setattr(topology_benchmark, "_self_contained_tasks", lambda: [])
    monkeypatch.setattr(topology_benchmark, "run_once", fake_run_once)
    payload = topology_benchmark.run_topology_benchmark(repeat=2)

    expected_transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    assert [call[0] for call in calls] == [
        "request_reply",
        "forward",
        "forward",
        "request_reply",
    ]
    assert {call[1] for call in calls} == {expected_transport}
    assert [call[2] for call in calls] == [42, 42, 43, 43]
    assert payload["methodology"]["state_shared_memory"] is False
    assert payload["paired_forward_minus_request_reply"]["latency_sec"]["n"] == 2
