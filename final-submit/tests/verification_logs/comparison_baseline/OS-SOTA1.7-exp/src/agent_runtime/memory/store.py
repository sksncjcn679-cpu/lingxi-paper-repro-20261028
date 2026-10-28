"""Shared memory store with governed retrieval and lifecycle management.

Design goals:
- SQLite metadata persistence + pluggable vector index;
- hybrid semantic/keyword/tag retrieval;
- explainable multi-factor ranking (relevance, quality, recency, importance, usage);
- task/TTL expiration and bounded-capacity pruning;
- feedback-driven quality updates and negative-transfer suppression;
- backward-compatible schema migration for v0.3 databases.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sqlite3
import time
from dataclasses import dataclass, field

import numpy as np

from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.vector_backend import build_vector_index
from agent_runtime.providers.base import EmbeddingProvider, validate_embedding_matrix


@dataclass
class MemoryHit:
    """One retrieval hit with an explainable score breakdown."""

    record: MemoryRecord
    score: float
    match_type: str
    components: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryPruneStats:
    """Lifecycle pruning result."""

    expired: int
    evicted: int
    remaining: int
    removed_ids: tuple[str, ...]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    source_agent TEXT,
    created_at REAL,
    task_topic TEXT,
    summary TEXT,
    memory_type TEXT,
    tags TEXT,
    keywords TEXT,
    embedding TEXT,
    embedding_ref TEXT,
    artifact_refs TEXT,
    evidence_refs TEXT,
    confidence REAL,
    quality_score REAL,
    importance_score REAL DEFAULT 0.5,
    reuse_count INTEGER,
    effective_reuse_count INTEGER DEFAULT 0,
    harmful_reuse_count INTEGER DEFAULT 0,
    quarantined INTEGER DEFAULT 0,
    last_accessed_at REAL,
    version INTEGER,
    parent_version TEXT,
    expiration_policy TEXT,
    expires_at REAL,
    provenance TEXT,
    task_id TEXT,
    success_feedback INTEGER,
    payload TEXT,
    derived_from TEXT DEFAULT '[]',
    conflict INTEGER DEFAULT 0
);
"""


class MemoryStore:
    """Shared memory with retrieval, feedback, expiration and capacity governance."""

    def __init__(
        self,
        embedding: EmbeddingProvider,
        db_path: str = ":memory:",
        vector_backend: str = "numpy",
        dedup_threshold: float = 0.97,
        quality_floor: float = 0.3,
        *,
        recency_half_life_sec: float = 7 * 24 * 3600,
        max_records: int | None = None,
        bm25_rerank: bool = False,
    ) -> None:
        if not math.isfinite(recency_half_life_sec) or recency_half_life_sec <= 0:
            raise ValueError("recency_half_life_sec must be a finite positive number")
        if max_records is not None and max_records <= 0:
            raise ValueError("max_records must be positive when provided")
        if not math.isfinite(dedup_threshold) or dedup_threshold < -1.0:
            raise ValueError("dedup_threshold must be finite and >= -1")
        if not math.isfinite(quality_floor) or not 0.0 <= quality_floor <= 1.0:
            raise ValueError("quality_floor must be finite and within [0, 1]")
        self.embedding = embedding
        self.vector_backend_requested = vector_backend.lower()
        self.dedup_threshold = float(dedup_threshold)
        self.quality_floor = float(quality_floor)
        self.recency_half_life_sec = float(recency_half_life_sec)
        self.max_records = max_records
        self.bm25_rerank = bool(bm25_rerank)
        if db_path != ":memory:":
            db_file = Path(db_path)
            db_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(db_file.parent, 0o700)
            except OSError:
                pass
        self.conn = sqlite3.connect(db_path, timeout=5.0)
        self.conn.execute("PRAGMA busy_timeout=5000")
        if db_path != ":memory:":
            # WAL improves reader/writer coexistence when multiple Runtime processes
            # share the same durable memory DB.  The local vector index is synchronized
            # from committed SQLite rows before retrieval (see _sync_index_from_db).
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            try:
                os.chmod(Path(db_path), 0o600)
            except OSError:
                pass
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate_schema()
        self.index = build_vector_index(
            self.vector_backend_requested,
            getattr(embedding, "dim", 256),
        )
        self.vector_backend_actual = self.index.name
        self._legacy_full_scan = False
        self._postings = None
        self._postings_key = (-1, -1)
        self._last_sem_scores: dict[str, float] | None = None
        self.last_candidate_count = 0
        self._indexed_ids: set[str] = set()
        self._indexed_embeddings: dict[str, str] = {}
        self._rebuild_index()

    # ------------------------------------------------------------------ write
    def write(self, record: MemoryRecord) -> tuple[MemoryRecord, bool]:
        """Write memory; deduplicate exact content and version semantic near-duplicates."""
        emb = self._embed(record)
        hits = self.index.search(emb, top_k=1)
        if hits and hits[0][1] >= self.dedup_threshold:
            existing = self.get(hits[0][0])
            if existing is not None:
                if existing.content_signature() == record.content_signature():
                    self._merge_duplicate(existing, record)
                    return existing, False
                record.parent_version = existing.memory_id
                record.version = existing.version + 1

        if record.success_feedback is False:
            record.quality_score = min(record.quality_score, 0.2)
        if record.expiration_policy == "ttl" and record.expires_at is None:
            raise ValueError("ttl memory requires expires_at")
        if record.expires_at is not None and record.expires_at <= record.created_at:
            raise ValueError("expires_at must be later than created_at")

        self._mark_conflicts(record)

        record.embedding_ref = f"vector://memory/{record.memory_id}"
        self._insert(record, emb)
        self.index.add(record.memory_id, emb)
        self._indexed_ids.add(record.memory_id)
        self._indexed_embeddings[record.memory_id] = json.dumps(emb.tolist())
        if self.max_records is not None and self.count() > self.max_records:
            self.prune(max_records=self.max_records)
        return record, True

    # ------------------------------------------------------------------ retrieval
    def retrieve(
        self,
        query_text: str | None = None,
        *,
        tags: list[str] | None = None,
        keywords: list[str] | None = None,
        top_k: int = 5,
        memory_type: MemoryType | None = None,
        include_low_quality: bool = False,
        include_quarantined: bool = False,
        current_task_id: str | None = None,
        now: float | None = None,
    ) -> list[MemoryHit]:
        """Hybrid retrieval with explainable relevance/quality/recency/importance signals."""
        if top_k <= 0:
            return []
        now = time.time() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError("memory retrieval 'now' must be finite")
        # Another process may have committed memories after this store instance was
        # opened. SQLite metadata is authoritative; keep the process-local vector index
        # coherent before semantic search instead of requiring a restart.
        rows = self._candidate_records(query_text, tags, keywords, top_k) if not self._legacy_full_scan else None
        if rows is None:
            self._last_sem_scores = None
            self._sync_index_from_db()
            rows = self._all_records()
        if not rows:
            return []

        sem_scores: dict[str, float] = dict(self._last_sem_scores) if self._last_sem_scores is not None else {}
        if query_text and self._last_sem_scores is None:
            qvec = validate_embedding_matrix(
                self.embedding.embed([query_text]),
                expected_rows=1,
                expected_dim=getattr(self.embedding, "dim", None),
                context="memory query embedding provider",
            )[0]
            for mid, score in self.index.search(qvec, top_k=max(top_k * 4, 16)):
                sem_scores[mid] = max(0.0, float(score))

        scored: list[MemoryHit] = []
        kw_set = {k.lower() for k in (keywords or [])}
        tag_set = {t.lower() for t in (tags or [])}
        for rec in rows:
            if self._is_expired(rec, now=now, current_task_id=current_task_id):
                continue
            if memory_type is not None and rec.memory_type != memory_type:
                continue
            if rec.quarantined and not include_quarantined:
                continue
            if not include_low_quality and rec.quality_score < self.quality_floor:
                continue

            sem = sem_scores.get(rec.memory_id, 0.0)
            kw_overlap = (
                len(kw_set & {k.lower() for k in rec.keywords}) / len(kw_set) if kw_set else 0.0
            )
            tag_overlap = (
                len(tag_set & {t.lower() for t in rec.tags}) / len(tag_set) if tag_set else 0.0
            )
            relevance = 0.65 * sem + 0.20 * kw_overlap + 0.15 * tag_overlap
            if relevance <= 0.0:
                continue

            recency = self._recency_score(rec, now)
            quality = self._clamp01(rec.quality_score)
            importance = self._clamp01(rec.importance_score)
            usage = self._usage_score(rec.reuse_count)
            reliability = self.reliability_score(rec)
            score = (
                0.56 * relevance
                + 0.16 * quality
                + 0.08 * recency
                + 0.08 * importance
                + 0.04 * usage
                + 0.08 * reliability
            )
            match_type = self._match_type(sem, kw_overlap, tag_overlap)
            scored.append(
                MemoryHit(
                    record=rec,
                    score=float(score),
                    match_type=match_type,
                    components={
                        "semantic": float(sem),
                        "keyword": float(kw_overlap),
                        "tag": float(tag_overlap),
                        "relevance": float(relevance),
                        "quality": float(quality),
                        "recency": float(recency),
                        "importance": float(importance),
                        "usage": float(usage),
                        "reliability": float(reliability),
                    },
                )
            )

        scored.sort(key=lambda hit: (hit.score, hit.record.created_at), reverse=True)
        if self.bm25_rerank and query_text and scored:
            from agent_runtime.memory.bm25 import scores, fused_order
            pool = scored[:max(20, top_k)]
            lexical = scores(query_text, [h.record.summary for h in pool])
            order = fused_order([h.score for h in pool], lexical)
            for i, h in enumerate(pool):
                h.components["bm25"] = lexical[i]
            scored = [pool[i] for i in order]
        return scored[:top_k]

    def get_lineage(self, memory_id: str, *, max_depth: int = 8, max_nodes: int = 32) -> dict:
        """Bounded source traversal. Cycles and pruned parents are reported, never fabricated."""
        if not 0 <= max_depth <= 64 or not 1 <= max_nodes <= 256:
            raise ValueError("lineage bounds out of range")
        queue = [(memory_id, 0)]
        seen = set()
        nodes, missing, edges = [], [], []
        truncated = False
        while queue:
            mid, depth = queue.pop(0)
            if mid in seen:
                continue
            if len(seen) >= max_nodes:
                truncated = True
                break
            seen.add(mid)
            rec = self.get(mid)
            if rec is None:
                missing.append(mid)
                continue
            nodes.append({"memory_id": mid, "summary": rec.summary, "task_id": rec.task_id,
                          "success_feedback": rec.success_feedback, "quarantined": rec.quarantined})
            if depth >= max_depth:
                truncated = truncated or bool(rec.derived_from)
                continue
            for parent in rec.derived_from:
                edges.append({"from": mid, "to": parent})
                queue.append((parent, depth + 1))
        return {"root": memory_id, "nodes": nodes, "edges": edges, "missing": missing, "truncated": truncated}

    def find_plan_template(
        self,
        *,
        operation: str,
        operands: list[str],
        topic: str | None,
        current_task_id: str | None = None,
        now: float | None = None,
    ) -> MemoryRecord | None:
        """fable8 agentic plan caching: a *verified* plan with the same signature.

        Inspired by test-time plan caching for LLM agents (Zhang et al., 2025, "Cost-Efficient
        Serving of LLM Agents via Test-Time Plan Caching"): when a previous task with the same
        plan signature -- operation + operand schema, same topic -- was confirmed successful by
        the Reviewer, the planner need not ask the LLM to decompose the task again.  Only
        records with positive success feedback, not quarantined and above the quality floor are
        eligible; the topic must match exactly, so cross-group candidates can never qualify.
        """
        if not operation:
            return None
        now = time.time() if now is None else float(now)
        want = sorted(str(x) for x in operands)
        best: MemoryRecord | None = None
        for rec in self._all_records():
            if self._is_expired(rec, now=now, current_task_id=current_task_id):
                continue
            if rec.quarantined or rec.quality_score < self.quality_floor or rec.success_feedback is not True:
                continue
            if topic is not None and rec.task_topic != topic:
                continue
            payload = rec.payload
            if payload.get("operation") != operation:
                continue
            if sorted(str(x) for x in (payload.get("operands") or [])) != want:
                continue
            if best is None or (self.reliability_score(rec), rec.quality_score, rec.created_at) > (
                self.reliability_score(best), best.quality_score, best.created_at
            ):
                best = rec
        return best

    def find_exact_request(
        self,
        fingerprint: str,
        *,
        operation: str | None = None,
        current_task_id: str | None = None,
        now: float | None = None,
    ) -> MemoryRecord | None:
        """Return the safest verified memory for an exact request fingerprint.

        This is an O(n) metadata lookup by design: it is a conservative cache layer, not a
        substitute for semantic retrieval.  Typical Runtime memory sets are small and bounded;
        durable deployments can later promote the fingerprint to an indexed SQLite column
        without changing the API.
        """
        if not fingerprint:
            return None
        now = time.time() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError("memory exact lookup 'now' must be finite")
        candidates: list[MemoryRecord] = []
        for rec in self._all_records():
            if self._is_expired(rec, now=now, current_task_id=current_task_id):
                continue
            if rec.quarantined or rec.quality_score < self.quality_floor:
                continue
            if rec.success_feedback is not True:
                continue
            payload = rec.payload
            if payload.get("request_fingerprint") != fingerprint:
                continue
            if operation is not None and payload.get("operation") != operation:
                continue
            if payload.get("answer") is None:
                continue
            candidates.append(rec)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda rec: (
                self.reliability_score(rec),
                rec.quality_score,
                rec.created_at,
            ),
        )

    # ------------------------------------------------------------------ feedback
    def mark_used(self, memory_id: str, effective: bool | None = None) -> None:
        rec = self.get(memory_id)
        if rec is None:
            return
        rec.reuse_count += 1
        rec.last_accessed_at = time.time()
        if effective is True:
            rec.quality_score = min(1.0, rec.quality_score + 0.05)
        elif effective is False:
            rec.quality_score = max(0.0, rec.quality_score - 0.2)
        self._update_dynamic(rec)

    def record_feedback(self, memory_id: str, effective: bool) -> None:
        """Record downstream reuse feedback and update reliability governance.

        Retrieval hits alone never affect these counters. A memory is quarantined only after
        repeated harmful *adoptions* and low posterior reliability, avoiding one-off failures from
        permanently poisoning a useful strategy.
        """
        rec = self.get(memory_id)
        if rec is None:
            return
        if effective:
            rec.effective_reuse_count += 1
            rec.quality_score = min(1.0, rec.quality_score + 0.05)
        else:
            rec.harmful_reuse_count += 1
            rec.quality_score = max(0.0, rec.quality_score - 0.2)

        observations = rec.effective_reuse_count + rec.harmful_reuse_count
        if observations >= 2 and rec.harmful_reuse_count >= 2 and self.reliability_score(rec) < 0.45:
            rec.quarantined = True
        self._update_dynamic(rec)

    def reliability_score(self, rec: MemoryRecord) -> float:
        """Smoothed probability that adopting a memory is useful.

        A Beta(1, 1) prior keeps unseen memories neutral at 0.5; only observed downstream
        effective/harmful reuse changes the score. This is deliberately separate from relevance
        and from retention value.
        """
        good = max(0, int(rec.effective_reuse_count))
        bad = max(0, int(rec.harmful_reuse_count))
        return float((good + 1.0) / (good + bad + 2.0))

    def release_quarantine(self, memory_id: str) -> bool:
        """Administrative recovery hook; does not erase historical feedback."""
        rec = self.get(memory_id)
        if rec is None:
            return False
        rec.quarantined = False
        self._update_dynamic(rec)
        return True

    # ------------------------------------------------------------------ lifecycle
    def prune(
        self,
        *,
        now: float | None = None,
        max_records: int | None = None,
        current_task_id: str | None = None,
    ) -> MemoryPruneStats:
        """Delete expired memories, then evict lowest retention-value records to a budget.

        Retrieval relevance is intentionally *not* used here because a future query is
        unknown at consolidation/forgetting time.  Retention instead uses quality,
        importance, recency and observed utility (reuse count).
        """
        now = time.time() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError("memory prune 'now' must be finite")
        limit = self.max_records if max_records is None else max_records
        if limit is not None and limit <= 0:
            raise ValueError("max_records must be positive")

        records = self._all_records()
        expired_ids = [
            rec.memory_id
            for rec in records
            if self._is_expired(rec, now=now, current_task_id=current_task_id)
        ]
        for memory_id in expired_ids:
            self._delete(memory_id)

        evicted_ids: list[str] = []
        if limit is not None:
            remaining = self._all_records()
            overflow = max(0, len(remaining) - limit)
            if overflow:
                ranked = sorted(
                    remaining,
                    key=lambda rec: (
                        self.retention_score(rec, now=now),
                        rec.created_at,
                        rec.memory_id,
                    ),
                )
                for rec in ranked[:overflow]:
                    evicted_ids.append(rec.memory_id)
                    self._delete(rec.memory_id)

        removed = tuple(expired_ids + evicted_ids)
        return MemoryPruneStats(
            expired=len(expired_ids),
            evicted=len(evicted_ids),
            remaining=self.count(),
            removed_ids=removed,
        )

    def retention_score(self, rec: MemoryRecord, *, now: float | None = None) -> float:
        now = time.time() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError("memory retention 'now' must be finite")
        return float(
            0.35 * self._clamp01(rec.quality_score)
            + 0.20 * self._clamp01(rec.importance_score)
            + 0.15 * self._recency_score(rec, now)
            + 0.15 * self._usage_score(rec.reuse_count)
            + 0.15 * self.reliability_score(rec)
        )

    # ------------------------------------------------------------------ query/admin
    def get(self, memory_id: str) -> MemoryRecord | None:
        cur = self.conn.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,))
        row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    def all_records(self) -> list[MemoryRecord]:
        return self._all_records()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ internals
    def _migrate_schema(self) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(memories)").fetchall()}
        migrations = {
            "importance_score": "ALTER TABLE memories ADD COLUMN importance_score REAL DEFAULT 0.5",
            "expires_at": "ALTER TABLE memories ADD COLUMN expires_at REAL",
            "effective_reuse_count": "ALTER TABLE memories ADD COLUMN effective_reuse_count INTEGER DEFAULT 0",
            "harmful_reuse_count": "ALTER TABLE memories ADD COLUMN harmful_reuse_count INTEGER DEFAULT 0",
            "quarantined": "ALTER TABLE memories ADD COLUMN quarantined INTEGER DEFAULT 0",
            "derived_from": "ALTER TABLE memories ADD COLUMN derived_from TEXT DEFAULT '[]'",
            "conflict": "ALTER TABLE memories ADD COLUMN conflict INTEGER DEFAULT 0",
        }
        for column, ddl in migrations.items():
            if column not in columns:
                self.conn.execute(ddl)
        self.conn.commit()

    def _embed(self, record: MemoryRecord) -> np.ndarray:
        return validate_embedding_matrix(
            self.embedding.embed([self._embed_text(record)]),
            expected_rows=1,
            expected_dim=getattr(self.embedding, "dim", None),
            context="memory embedding provider",
        )[0]

    @staticmethod
    def _embed_text(record: MemoryRecord) -> str:
        return " ".join(
            [record.task_topic, record.summary, " ".join(record.keywords), " ".join(record.tags)]
        )

    @staticmethod
    def _match_type(sem: float, kw: float, tag: float) -> str:
        active = [name for name, value in (("semantic", sem), ("keyword", kw), ("tag", tag)) if value > 0]
        return "hybrid" if len(active) > 1 else (active[0] if active else "semantic")

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _usage_score(reuse_count: int) -> float:
        # Saturates gradually: 0 -> 0, 1 -> .39, 5 -> 1.0.
        return min(1.0, math.log1p(max(0, int(reuse_count))) / math.log(6.0))

    def _recency_score(self, rec: MemoryRecord, now: float) -> float:
        anchor = rec.last_accessed_at if rec.last_accessed_at is not None else rec.created_at
        age = max(0.0, now - float(anchor))
        return math.exp(-math.log(2.0) * age / self.recency_half_life_sec)

    @staticmethod
    def _is_expired(
        rec: MemoryRecord,
        *,
        now: float,
        current_task_id: str | None,
    ) -> bool:
        if rec.expiration_policy == "ttl" and rec.expires_at is not None and now >= rec.expires_at:
            return True
        if rec.expiration_policy == "task":
            # Without a current task context, task-local memories are conservatively hidden.
            return not current_task_id or rec.task_id != current_task_id
        return False

    def _merge_duplicate(self, existing: MemoryRecord, incoming: MemoryRecord) -> None:
        existing.quality_score = max(existing.quality_score, incoming.quality_score)
        existing.importance_score = max(existing.importance_score, incoming.importance_score)
        if incoming.expires_at is not None:
            existing.expires_at = max(existing.expires_at or 0.0, incoming.expires_at)
        self.conn.execute(
            """UPDATE memories SET quality_score=?, importance_score=?, expires_at=?
            WHERE memory_id=?""",
            (
                existing.quality_score,
                existing.importance_score,
                existing.expires_at,
                existing.memory_id,
            ),
        )
        self.conn.commit()

    def _insert(self, rec: MemoryRecord, emb: np.ndarray) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO memories (
                memory_id,source_agent,created_at,task_topic,summary,memory_type,tags,keywords,
                embedding,embedding_ref,artifact_refs,evidence_refs,confidence,quality_score,
                importance_score,reuse_count,effective_reuse_count,harmful_reuse_count,quarantined,
                last_accessed_at,version,parent_version,expiration_policy,expires_at,provenance,task_id,
                success_feedback,payload,derived_from,conflict
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.memory_id,
                rec.source_agent,
                rec.created_at,
                rec.task_topic,
                rec.summary,
                rec.memory_type.value,
                json.dumps(rec.tags, ensure_ascii=False),
                json.dumps(rec.keywords, ensure_ascii=False),
                json.dumps(emb.tolist()),
                rec.embedding_ref,
                json.dumps(rec.artifact_refs, ensure_ascii=False),
                json.dumps(rec.evidence_refs, ensure_ascii=False),
                rec.confidence,
                rec.quality_score,
                rec.importance_score,
                rec.reuse_count,
                rec.effective_reuse_count,
                rec.harmful_reuse_count,
                int(rec.quarantined),
                rec.last_accessed_at,
                rec.version,
                rec.parent_version,
                rec.expiration_policy,
                rec.expires_at,
                json.dumps(rec.provenance, ensure_ascii=False),
                rec.task_id,
                None if rec.success_feedback is None else int(rec.success_feedback),
                json.dumps(rec.payload, ensure_ascii=False),
                json.dumps(rec.derived_from, ensure_ascii=False),
                int(rec.conflict),
            ),
        )
        self.conn.commit()

    def _conflict_key(self, record: MemoryRecord) -> str:
        return str(record.payload.get("signature") or record.content_signature())

    def _mark_conflicts(self, incoming: MemoryRecord) -> None:
        """Flag opposite conclusions for the same request signature.

        Conflict marking is conservative: only records with a non-empty answer or solution
        are compared, and an identical conclusion is left untouched.
        """
        key = self._conflict_key(incoming)
        incoming_answer = incoming.payload.get("answer", incoming.payload.get("solution"))
        if incoming_answer is None:
            return
        rows = self.conn.execute("SELECT * FROM memories").fetchall()
        for row in rows:
            existing = self._row_to_record(row)
            if self._conflict_key(existing) != key:
                continue
            existing_answer = existing.payload.get("answer", existing.payload.get("solution"))
            if existing_answer is None or existing_answer == incoming_answer:
                continue
            existing.conflict = True
            incoming.conflict = True
            self.conn.execute("UPDATE memories SET conflict=1 WHERE memory_id=?", (existing.memory_id,))
        if incoming.conflict:
            self.conn.commit()

    def consolidate(self, topic: str) -> MemoryRecord | None:
        """Create one procedural memory from three successful episodic records."""
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE task_topic=? AND memory_type=? "
            "AND success_feedback=1 ORDER BY created_at ASC LIMIT 3",
            (str(topic), MemoryType.EPISODIC.value),
        ).fetchall()
        if len(rows) < 3:
            return None
        records = [self._row_to_record(row) for row in rows]
        derived = [record.memory_id for record in records]
        steps = [record.summary for record in records]
        source = records[-1]
        consolidated = MemoryRecord(
            source_agent="memory-consolidator",
            task_topic=str(topic),
            summary="Consolidated procedure: " + " | ".join(steps),
            memory_type=MemoryType.PROCEDURAL,
            tags=sorted({tag for record in records for tag in record.tags}),
            keywords=sorted({key for record in records for key in record.keywords}),
            confidence=min(record.confidence for record in records),
            quality_score=max(record.quality_score for record in records),
            importance_score=0.95,
            artifact_refs=sorted({ref for record in records for ref in record.artifact_refs}),
            evidence_refs=sorted({ref for record in records for ref in record.evidence_refs}),
            provenance={"derived_from": derived, "consolidation": "three successful episodic records"},
            task_id=source.task_id,
            success_feedback=True,
            derived_from=derived,
            payload={
                "operation": source.payload.get("operation"),
                "solution": source.payload.get("solution"),
                "answer": source.payload.get("answer"),
                "signature": source.payload.get("signature"),
                "consolidated": True,
            },
        )
        for record in records:
            record.importance_score = min(record.importance_score, 0.35)
            self.conn.execute(
                "UPDATE memories SET importance_score=? WHERE memory_id=?",
                (record.importance_score, record.memory_id),
            )
        self.conn.commit()
        self.write(consolidated)
        return consolidated

    def _update_dynamic(self, rec: MemoryRecord) -> None:
        self.conn.execute(
            """UPDATE memories SET reuse_count=?, effective_reuse_count=?, harmful_reuse_count=?,
            quarantined=?, last_accessed_at=?, quality_score=? WHERE memory_id=?""",
            (
                rec.reuse_count,
                rec.effective_reuse_count,
                rec.harmful_reuse_count,
                int(rec.quarantined),
                rec.last_accessed_at,
                rec.quality_score,
                rec.memory_id,
            ),
        )
        self.conn.commit()

    def _delete(self, memory_id: str) -> None:
        self.conn.execute("DELETE FROM memories WHERE memory_id=?", (memory_id,))
        self.conn.commit()
        try:
            self.index.remove(memory_id)
            self._indexed_ids.discard(memory_id)
            self._indexed_embeddings.pop(memory_id, None)
        except Exception:  # noqa: BLE001 - optional backends may not support deletion perfectly
            # Rebuild keeps correctness for fallback/backends with partial remove support.
            self.index = build_vector_index(
                self.vector_backend_actual,
                getattr(self.embedding, "dim", 256),
                strict=True,
            )
            self._rebuild_index()

    # ------------------------------------------------------------------ opus1 sublinear retrieval
    def _change_key(self) -> tuple[int, int]:
        """Cheap change detector: local writes (total_changes) + other connections (data_version)."""
        dv = self.conn.execute("PRAGMA data_version").fetchone()[0]
        return (self.conn.total_changes, int(dv))

    def _refresh_postings(self) -> None:
        key = self._change_key()
        if key == self._postings_key and self._postings is not None:
            return
        self._sync_index_from_db()
        kw: dict[str, set[str]] = {}
        tg: dict[str, set[str]] = {}
        for row in self.conn.execute("SELECT memory_id, keywords, tags FROM memories").fetchall():
            for k in json.loads(row["keywords"]):
                kw.setdefault(str(k).lower(), set()).add(row["memory_id"])
            for t in json.loads(row["tags"]):
                tg.setdefault(str(t).lower(), set()).add(row["memory_id"])
        self._postings = (kw, tg)
        self._postings_key = self._change_key()

    def _candidate_records(self, query_text, tags, keywords, top_k) -> list[MemoryRecord]:
        """Candidate generation that is provably ranking-equivalent to a full scan.

        A record can only get relevance > 0 through a semantic hit (only the vector-index top
        results receive a semantic score) or a keyword/tag overlap.  So the union of those three
        sets contains every record the full scan could return; rows are fetched in rowid order
        to preserve the full scan's tie-breaking.
        """
        self._refresh_postings()
        assert self._postings is not None
        kw_post, tag_post = self._postings
        ids: set[str] = set()
        self._last_sem_scores = None
        if query_text:
            qvec = validate_embedding_matrix(
                self.embedding.embed([query_text]),
                expected_rows=1,
                expected_dim=getattr(self.embedding, "dim", None),
                context="memory query embedding provider",
            )[0]
            sem: dict[str, float] = {}
            for mid, score in self.index.search(qvec, top_k=max(top_k * 4, 16)):
                sem[mid] = max(0.0, float(score))
            self._last_sem_scores = sem
            ids.update(m for m, v in sem.items() if v > 0.0)
        else:
            self._last_sem_scores = {}
        for k in keywords or []:
            ids.update(kw_post.get(str(k).lower(), ()))
        for t in tags or []:
            ids.update(tag_post.get(str(t).lower(), ()))
        if not ids:
            return []
        out: list[MemoryRecord] = []
        id_list = sorted(ids)
        rows = []
        for i in range(0, len(id_list), 800):
            chunk = id_list[i : i + 800]
            rows.extend(self.conn.execute(
                f"SELECT rowid AS _rid, * FROM memories WHERE memory_id IN ({','.join('?' * len(chunk))})", chunk
            ).fetchall())
        rows.sort(key=lambda r: r["_rid"])
        out = [self._row_to_record(r) for r in rows]
        self.last_candidate_count = len(out)
        return out

    def _all_records(self) -> list[MemoryRecord]:
        cur = self.conn.execute("SELECT * FROM memories")
        return [self._row_to_record(row) for row in cur.fetchall()]

    def _rebuild_index(self) -> None:
        self._indexed_ids.clear()
        self._indexed_embeddings.clear()
        cur = self.conn.execute("SELECT memory_id, embedding FROM memories")
        for row in cur.fetchall():
            emb = np.array(json.loads(row["embedding"]), dtype=np.float32)
            self.index.add(row["memory_id"], emb)
            self._indexed_ids.add(row["memory_id"])
            self._indexed_embeddings[row["memory_id"]] = row["embedding"]

    def _sync_index_from_db(self) -> None:
        """Refresh the local vector index after commits made by another process.

        SQLite is the durable source of truth while FAISS/HNSW/numpy indexes are
        process-local accelerators.  Comparing the committed ID set is intentionally
        simple and deterministic for the competition-scale workloads; if it changes,
        rebuild from SQLite so both external inserts and deletions become visible.
        """
        rows = self.conn.execute("SELECT memory_id, embedding FROM memories").fetchall()
        db_embeddings = {row["memory_id"]: row["embedding"] for row in rows}
        db_ids = set(db_embeddings)
        if db_ids == self._indexed_ids and db_embeddings == self._indexed_embeddings:
            return

        self.index = build_vector_index(
            self.vector_backend_actual,
            getattr(self.embedding, "dim", 256),
            strict=True,
        )
        self._indexed_ids.clear()
        self._indexed_embeddings.clear()
        for row in rows:
            emb = np.array(json.loads(row["embedding"]), dtype=np.float32)
            self.index.add(row["memory_id"], emb)
            self._indexed_ids.add(row["memory_id"])
            self._indexed_embeddings[row["memory_id"]] = row["embedding"]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
        sf = row["success_feedback"]
        keys = set(row.keys())
        return MemoryRecord(
            memory_id=row["memory_id"],
            source_agent=row["source_agent"],
            created_at=row["created_at"],
            task_topic=row["task_topic"],
            summary=row["summary"],
            memory_type=MemoryType(row["memory_type"]),
            tags=json.loads(row["tags"]),
            keywords=json.loads(row["keywords"]),
            embedding_ref=row["embedding_ref"],
            artifact_refs=json.loads(row["artifact_refs"]),
            evidence_refs=json.loads(row["evidence_refs"]),
            confidence=row["confidence"],
            quality_score=row["quality_score"],
            importance_score=(row["importance_score"] if "importance_score" in keys else 0.5),
            reuse_count=row["reuse_count"],
            effective_reuse_count=(row["effective_reuse_count"] if "effective_reuse_count" in keys else 0),
            harmful_reuse_count=(row["harmful_reuse_count"] if "harmful_reuse_count" in keys else 0),
            quarantined=bool(row["quarantined"]) if "quarantined" in keys else False,
            last_accessed_at=row["last_accessed_at"],
            version=row["version"],
            parent_version=row["parent_version"],
            expiration_policy=row["expiration_policy"],
            expires_at=(row["expires_at"] if "expires_at" in keys else None),
            provenance=json.loads(row["provenance"]),
            task_id=row["task_id"],
            success_feedback=None if sf is None else bool(sf),
            payload=json.loads(row["payload"]),
            derived_from=json.loads(row["derived_from"]) if "derived_from" in keys and row["derived_from"] else [],
            conflict=bool(row["conflict"]) if "conflict" in keys else False,
        )


__all__ = ["MemoryStore", "MemoryHit", "MemoryPruneStats"]
