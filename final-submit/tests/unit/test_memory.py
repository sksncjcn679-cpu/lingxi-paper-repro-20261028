"""共享记忆存储单元测试：写入、去重、质量、反馈。"""

from __future__ import annotations

from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.mock import MockEmbedding


def _store():
    return MemoryStore(embedding=MockEmbedding(dim=64, seed=1))


def _rec(**kw) -> MemoryRecord:
    base = {
        "source_agent": "reviewer",
        "task_topic": "sum-task",
        "summary": "operation sum over a,b,c -> 12",
        "keywords": ["sum", "a", "b", "c"],
        "success_feedback": True,
    }
    base.update(kw)
    return MemoryRecord(**base)


def test_write_creates_record():
    store = _store()
    rec, created = store.write(_rec())
    assert created is True
    assert store.count() == 1
    assert store.get(rec.memory_id) is not None


def test_exact_duplicate_is_deduped_without_inflating_reuse_metrics():
    store = _store()
    first, _ = store.write(_rec())
    rec2, created2 = store.write(_rec())
    assert created2 is False
    assert store.count() == 1
    assert rec2.memory_id == first.memory_id
    assert rec2.reuse_count == 0
    assert rec2.last_accessed_at is None

    # 只有真正采用检索结果时才计一次 reuse。
    store.mark_used(first.memory_id, effective=True)
    used = store.get(first.memory_id)
    assert used is not None
    assert used.reuse_count == 1
    assert used.last_accessed_at is not None


def test_error_memory_downweighted():
    store = _store()
    rec, _ = store.write(_rec(summary="bad fix", success_feedback=False))
    assert rec.quality_score <= 0.2


def test_record_feedback_adjusts_quality():
    store = _store()
    rec, _ = store.write(_rec())
    before = store.get(rec.memory_id).quality_score
    store.record_feedback(rec.memory_id, effective=False)
    after = store.get(rec.memory_id).quality_score
    assert after < before


def test_mark_used_increments_reuse():
    store = _store()
    rec, _ = store.write(_rec())
    store.mark_used(rec.memory_id)
    assert store.get(rec.memory_id).reuse_count == 1


def test_memory_type_persisted():
    store = _store()
    rec, _ = store.write(_rec(memory_type=MemoryType.PROCEDURAL))
    assert store.get(rec.memory_id).memory_type == MemoryType.PROCEDURAL


def test_unknown_vector_backend_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="unsupported vector backend"):
        MemoryStore(embedding=MockEmbedding(dim=8, seed=1), vector_backend="mystery")


def test_optional_vector_backend_fallback_is_explicit():
    import pytest

    with pytest.warns(RuntimeWarning, match="falling back"):
        store = MemoryStore(embedding=MockEmbedding(dim=8, seed=1), vector_backend="hnswlib")
    try:
        assert store.vector_backend_requested == "hnswlib"
        assert store.vector_backend_actual == "numpy"
    finally:
        store.close()


def test_memory_record_rejects_invalid_quality_time_and_expiration_policy():
    import math
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _rec(quality_score=math.nan)
    with pytest.raises(ValidationError):
        _rec(confidence=1.5)
    with pytest.raises(ValidationError):
        _rec(created_at=math.inf)
    with pytest.raises(ValidationError):
        _rec(expiration_policy="forever")


def test_memory_lifecycle_methods_reject_non_finite_now():
    import math
    import pytest

    store = _store()
    store.write(_rec())
    with pytest.raises(ValueError, match="finite"):
        store.retrieve(query_text="sum", now=math.nan)
    with pytest.raises(ValueError, match="finite"):
        store.prune(now=math.inf)
    rec = store.all_records()[0]
    with pytest.raises(ValueError, match="finite"):
        store.retention_score(rec, now=math.nan)

def test_persistent_memory_store_refreshes_vector_index_after_external_commit(tmp_path):
    """Two already-open stores must observe each other's committed memories."""
    db = tmp_path / "shared.sqlite3"
    a = MemoryStore(MockEmbedding(dim=16), db_path=str(db))
    b = MemoryStore(MockEmbedding(dim=16), db_path=str(db))
    try:
        record = MemoryRecord(
            source_agent="writer-a",
            task_topic="zero division python",
            summary="guard denominator before divide",
            memory_type=MemoryType.PROCEDURAL,
            keywords=["divide", "zero"],
        )
        a.write(record)
        assert b.count() == 1  # SQLite sees the commit even though b opened earlier.
        hits = b.retrieve("zero division python", top_k=5)
        assert [hit.record.memory_id for hit in hits] == [record.memory_id]
    finally:
        a.close()
        b.close()


def test_memory_record_rejects_non_json_or_non_finite_payloads() -> None:
    import pytest
    from agent_runtime.memory.models import MemoryRecord

    base = dict(source_agent="a", task_topic="t", summary="s")
    with pytest.raises(ValueError, match="JSON-native"):
        MemoryRecord(**base, payload={"x": object()})
    with pytest.raises(ValueError, match="finite"):
        MemoryRecord(**base, payload={"x": float("nan")})
    with pytest.raises(ValueError, match="JSON-native"):
        MemoryRecord(**base, provenance={"x": object()})
