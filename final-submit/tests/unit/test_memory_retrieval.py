"""共享记忆检索单元测试：关键词 / 标签 / 语义 / 质量过滤。"""

from __future__ import annotations

from agent_runtime.memory.models import MemoryRecord
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.mock import MockEmbedding


def _store():
    store = MemoryStore(embedding=MockEmbedding(dim=128, seed=2))
    store.write(
        MemoryRecord(
            source_agent="reviewer",
            task_topic="async fix",
            summary="fix async resource leak by awaiting close",
            keywords=["async", "resource", "leak"],
            tags=["bugfix", "async"],
            success_feedback=True,
        )
    )
    store.write(
        MemoryRecord(
            source_agent="reviewer",
            task_topic="logging",
            summary="add structured logging to module",
            keywords=["logging", "module"],
            tags=["refactor"],
            success_feedback=True,
        )
    )
    return store


def test_keyword_retrieval():
    store = _store()
    hits = store.retrieve(keywords=["async", "leak"], top_k=5)
    assert hits
    assert "async" in hits[0].record.keywords


def test_tag_retrieval():
    store = _store()
    hits = store.retrieve(tags=["refactor"], top_k=5)
    assert hits
    assert "refactor" in hits[0].record.tags


def test_semantic_retrieval():
    store = _store()
    hits = store.retrieve("fix asynchronous resource leak", top_k=1)
    assert hits
    assert hits[0].record.task_topic == "async fix"


def test_low_quality_excluded_by_default():
    store = _store()
    bad, _ = store.write(
        MemoryRecord(
            source_agent="reviewer",
            task_topic="bad",
            summary="harmful wrong fix",
            keywords=["async"],
            success_feedback=False,
        )
    )
    hits = store.retrieve(keywords=["async"], top_k=10)
    ids = {h.record.memory_id for h in hits}
    assert bad.memory_id not in ids
    hits_all = store.retrieve(keywords=["async"], top_k=10, include_low_quality=True)
    ids_all = {h.record.memory_id for h in hits_all}
    assert bad.memory_id in ids_all


def test_memory_hit_exposes_multifactor_score(memory_store=None):
    from agent_runtime.memory.models import MemoryRecord
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.providers.mock import MockEmbedding

    store = MemoryStore(embedding=MockEmbedding(dim=128, seed=2))
    rec = MemoryRecord(
        source_agent="reviewer",
        task_topic="safe divide",
        summary="guard zero divisor before division",
        keywords=["codefix", "zero-division"],
        importance_score=0.9,
        quality_score=0.95,
    )
    store.write(rec)
    hits = store.retrieve("safe divide zero division", keywords=["codefix"], top_k=1)
    assert len(hits) == 1
    assert set(hits[0].components) >= {
        "relevance", "quality", "recency", "importance", "usage"
    }
    assert hits[0].components["importance"] == 0.9


def test_ttl_and_task_scoped_memories_are_hidden():
    import time
    from agent_runtime.memory.models import MemoryRecord
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.providers.mock import MockEmbedding

    now = time.time()
    store = MemoryStore(embedding=MockEmbedding(dim=128, seed=2))
    expired = MemoryRecord(
        source_agent="r",
        task_topic="ttl",
        summary="temporary strategy",
        keywords=["temporary"],
        expiration_policy="ttl",
        expires_at=now + 1,
        created_at=now,
    )
    task_local = MemoryRecord(
        source_agent="r",
        task_topic="task local",
        summary="local strategy",
        keywords=["local"],
        expiration_policy="task",
        task_id="task-a",
    )
    store.write(expired)
    store.write(task_local)
    assert store.retrieve("temporary", keywords=["temporary"], now=now + 2) == []
    assert store.retrieve(
        "local", keywords=["local"], current_task_id="task-b", now=now + 2
    ) == []
    assert store.retrieve(
        "local", keywords=["local"], current_task_id="task-a", now=now + 2
    )


def test_prune_expires_then_evicts_low_retention_value():
    import time
    from agent_runtime.memory.models import MemoryRecord
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.providers.mock import MockEmbedding

    now = time.time()
    store = MemoryStore(embedding=MockEmbedding(dim=128, seed=4), max_records=None)
    expired = MemoryRecord(
        source_agent="r",
        task_topic="expired",
        summary="old temporary",
        expiration_policy="ttl",
        created_at=now - 100,
        expires_at=now - 1,
        importance_score=1.0,
        quality_score=1.0,
    )
    low = MemoryRecord(
        source_agent="r",
        task_topic="low",
        summary="low value",
        created_at=now - 1000,
        importance_score=0.0,
        quality_score=0.31,
    )
    high = MemoryRecord(
        source_agent="r",
        task_topic="high",
        summary="high value",
        created_at=now,
        importance_score=1.0,
        quality_score=1.0,
    )
    for rec in (expired, low, high):
        store.write(rec)
    stats = store.prune(now=now, max_records=1)
    assert stats.expired == 1
    assert stats.evicted == 1
    assert stats.remaining == 1
    assert store.get(high.memory_id) is not None
    assert store.get(low.memory_id) is None


def test_old_sqlite_schema_is_migrated(tmp_path):
    import sqlite3
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.providers.mock import MockEmbedding

    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE memories (
        memory_id TEXT PRIMARY KEY, source_agent TEXT, created_at REAL, task_topic TEXT,
        summary TEXT, memory_type TEXT, tags TEXT, keywords TEXT, embedding TEXT,
        embedding_ref TEXT, artifact_refs TEXT, evidence_refs TEXT, confidence REAL,
        quality_score REAL, reuse_count INTEGER, last_accessed_at REAL, version INTEGER,
        parent_version TEXT, expiration_policy TEXT, provenance TEXT, task_id TEXT,
        success_feedback INTEGER, payload TEXT)"""
    )
    conn.commit(); conn.close()
    store = MemoryStore(embedding=MockEmbedding(dim=128, seed=2), db_path=str(db))
    columns = {row[1] for row in store.conn.execute("PRAGMA table_info(memories)")}
    assert {"importance_score", "expires_at"} <= columns
