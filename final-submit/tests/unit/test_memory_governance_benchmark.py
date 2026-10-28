from agent_runtime.evaluation.memory_governance import run_memory_governance_benchmark


def test_memory_governance_targeted_suite_exposes_lifecycle_value():
    payload = run_memory_governance_benchmark()
    s = payload["summary"]
    assert s["cases"] == 3
    assert s["governed_correct"] == 3
    assert s["governed_accuracy"] == 1.0
    # The purpose is to exercise cases v0.3 did not model, not to fabricate a broad quality score.
    assert s["legacy_correct"] < s["governed_correct"]
    assert s["prune"]["expired"] == 1
    assert s["prune"]["evicted"] == 1
    assert s["prune"]["remaining"] == 2
    assert set(s["prune"]["remaining_ids"]) == {"mem_prune_high1", "mem_prune_high2"}
