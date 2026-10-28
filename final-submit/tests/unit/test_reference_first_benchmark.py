from __future__ import annotations

from agent_runtime.evaluation.runner import run_suite


def test_reference_first_suite_reduces_control_plane_wire_without_success_loss():
    payload = run_suite(suite="reference_first", repeat=1, seed=29)
    results = payload["results"]
    assert payload["baseline_id"] == "R0"
    assert set(results) == {"R0", "R1"}
    assert results["R0"]["derived"]["success_rate"] == 1.0
    assert results["R1"]["derived"]["success_rate"] == 1.0

    old = results["R0"]["summary"]
    new = results["R1"]["summary"]
    assert new["message_count"]["mean"] == old["message_count"]["mean"]
    assert new["wire_bytes"]["mean"] < old["wire_bytes"]["mean"]
    assert new["text_tokens"]["mean"] < old["text_tokens"]["mean"]
