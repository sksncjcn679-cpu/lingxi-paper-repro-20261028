from agent_runtime.evaluation.memory_accuracy import run_memory_accuracy_benchmark


def test_memory_accuracy_covers_explicit_semantic_keyword_tag_and_hybrid_modes():
    payload = run_memory_accuracy_benchmark()
    assert payload["suite"] == "memory_accuracy"
    assert len(payload["cases"]) == 2
    for mode in ("semantic", "keyword", "tag", "hybrid"):
        metrics = payload["summary"][mode]
        assert 0.0 <= metrics["precision_at_1"] <= 1.0
        assert 0.0 <= metrics["recall_at_3"] <= 1.0
        assert 0.0 <= metrics["mrr"] <= 1.0
        assert metrics["recall_at_3"] > 0.0
