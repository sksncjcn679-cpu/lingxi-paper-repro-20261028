from __future__ import annotations

from agent_runtime.memory.models import MemoryRecord
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.mock import MockEmbedding


def _record(summary: str = "repair divide by zero") -> MemoryRecord:
    return MemoryRecord(
        source_agent="reviewer",
        task_topic="code repair",
        summary=summary,
        keywords=["repair", "divide", "zero"],
        tags=["python"],
        payload={"operation": "codefix", "solution": "guard denominator"},
    )


def test_feedback_reliability_is_based_on_adopted_outcomes_only() -> None:
    store = MemoryStore(MockEmbedding())
    rec, _ = store.write(_record())
    assert store.reliability_score(rec) == 0.5

    store.mark_used(rec.memory_id)
    rec = store.get(rec.memory_id)
    assert rec is not None
    assert rec.reuse_count == 1
    assert rec.effective_reuse_count == 0
    assert rec.harmful_reuse_count == 0
    assert store.reliability_score(rec) == 0.5

    store.record_feedback(rec.memory_id, effective=True)
    rec = store.get(rec.memory_id)
    assert rec is not None
    assert rec.effective_reuse_count == 1
    assert store.reliability_score(rec) > 0.5


def test_repeated_harmful_reuse_quarantines_memory() -> None:
    store = MemoryStore(MockEmbedding())
    rec, _ = store.write(_record())
    store.record_feedback(rec.memory_id, effective=False)
    one = store.get(rec.memory_id)
    assert one is not None and not one.quarantined

    store.record_feedback(rec.memory_id, effective=False)
    two = store.get(rec.memory_id)
    assert two is not None and two.quarantined
    assert two.harmful_reuse_count == 2
    assert store.reliability_score(two) < 0.45

    hits = store.retrieve(
        "repair divide zero",
        keywords=["repair", "divide", "zero"],
        top_k=5,
    )
    assert all(hit.record.memory_id != rec.memory_id for hit in hits)

    diagnostic = store.retrieve(
        "repair divide zero",
        keywords=["repair", "divide", "zero"],
        top_k=5,
        include_quarantined=True,
    )
    assert any(hit.record.memory_id == rec.memory_id for hit in diagnostic)


def test_quarantine_can_be_released_without_erasing_feedback() -> None:
    store = MemoryStore(MockEmbedding())
    rec, _ = store.write(_record())
    store.record_feedback(rec.memory_id, effective=False)
    store.record_feedback(rec.memory_id, effective=False)
    assert store.release_quarantine(rec.memory_id)

    recovered = store.get(rec.memory_id)
    assert recovered is not None
    assert not recovered.quarantined
    assert recovered.harmful_reuse_count == 2


def test_reliability_contributes_to_ranking_for_equally_relevant_memories() -> None:
    store = MemoryStore(MockEmbedding(), dedup_threshold=1.1)
    good, _ = store.write(_record("repair divide by zero strategy alpha"))
    risky, _ = store.write(_record("repair divide by zero strategy beta"))

    for _ in range(3):
        store.record_feedback(good.memory_id, effective=True)
    store.record_feedback(risky.memory_id, effective=False)

    hits = store.retrieve(
        "repair divide by zero strategy",
        keywords=["repair", "divide", "zero"],
        top_k=2,
    )
    assert {h.record.memory_id for h in hits} == {good.memory_id, risky.memory_id}
    by_id = {h.record.memory_id: h for h in hits}
    assert by_id[good.memory_id].components["reliability"] > by_id[risky.memory_id].components["reliability"]
