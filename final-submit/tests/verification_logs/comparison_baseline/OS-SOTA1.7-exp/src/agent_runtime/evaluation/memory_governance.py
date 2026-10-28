"""Controlled validation of memory lifecycle/governance semantics.

This is deliberately a *targeted adversarial suite*, not a claim of general retrieval
quality.  It compares the v0.3 ranking rule (semantic/keyword/tag x quality, no
expiration/task-scope/recency/importance governance) with the current governed store on
cases where lifecycle metadata is the deciding signal.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.mock import MockEmbedding


def _legacy_v03_top1(
    store: MemoryStore,
    query_text: str,
    *,
    tags: list[str] | None = None,
    keywords: list[str] | None = None,
) -> str | None:
    """Reproduce v0.3's documented retrieval score for a controlled comparison."""
    qvec = store.embedding.embed([query_text])[0]
    sem_scores = dict(store.index.search(qvec, top_k=max(store.count(), 16)))
    kw_set = {k.lower() for k in (keywords or [])}
    tag_set = {t.lower() for t in (tags or [])}
    scored: list[tuple[float, str]] = []
    for rec in store.all_records():
        if rec.quality_score < store.quality_floor:
            continue
        sem = max(0.0, float(sem_scores.get(rec.memory_id, 0.0)))
        kw = len(kw_set & {k.lower() for k in rec.keywords}) / len(kw_set) if kw_set else 0.0
        tag = len(tag_set & {t.lower() for t in rec.tags}) / len(tag_set) if tag_set else 0.0
        base = 0.60 * sem + 0.25 * kw + 0.15 * tag
        if base <= 0.0:
            continue
        score = base * (0.5 + 0.5 * rec.quality_score)
        scored.append((score, rec.memory_id))
    # Python sort is stable; equal old scores retain insertion/database order, just as v0.3.
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored else None


def _record(
    memory_id: str,
    *,
    created_at: float,
    expiration_policy: str = "none",
    expires_at: float | None = None,
    task_id: str = "",
    importance: float = 0.5,
    quality: float = 0.9,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        source_agent="reviewer",
        created_at=created_at,
        task_topic="python safe divide repair",
        summary="repair zero division with denominator guard and regression test",
        memory_type=MemoryType.PROCEDURAL,
        tags=["python", "repair"],
        keywords=["divide", "guard", "test"],
        confidence=0.9,
        quality_score=quality,
        importance_score=importance,
        expiration_policy=expiration_policy,
        expires_at=expires_at,
        task_id=task_id,
        payload={"strategy": memory_id},
    )


def run_memory_governance_benchmark() -> dict[str, Any]:
    now = 1_000_000.0
    query = "python safe divide repair denominator guard regression test"
    tags = ["python", "repair"]
    keywords = ["divide", "guard", "test"]
    cases: list[dict[str, Any]] = []

    # 1) Expired record is intentionally inserted first and has identical retrieval content.
    store = MemoryStore(MockEmbedding(dim=256), quality_floor=0.0)
    expired = _record(
        "mem_expired",
        created_at=now - 1000,
        expiration_policy="ttl",
        expires_at=now - 1,
        importance=1.0,
    )
    live = _record("mem_live", created_at=now - 10, importance=0.7)
    store.write(expired)
    # Avoid exact-content dedup while preserving equivalent query relevance.
    live.summary += " maintained"
    store.write(live)
    legacy = _legacy_v03_top1(store, query, tags=tags, keywords=keywords)
    governed_hits = store.retrieve(query, tags=tags, keywords=keywords, top_k=1, now=now)
    governed = governed_hits[0].record.memory_id if governed_hits else None
    cases.append({
        "case": "ttl_expiration",
        "expected": "mem_live",
        "legacy_top1": legacy,
        "governed_top1": governed,
        "legacy_correct": legacy == "mem_live",
        "governed_correct": governed == "mem_live",
        "governed_components": governed_hits[0].components if governed_hits else {},
    })
    store.close()

    # 2) Task-local memory from another task must not leak into the current task.
    store = MemoryStore(MockEmbedding(dim=256), quality_floor=0.0)
    foreign = _record(
        "mem_foreign_task", created_at=now - 5, expiration_policy="task", task_id="task-other"
    )
    current = _record(
        "mem_current_task", created_at=now - 5, expiration_policy="task", task_id="task-current"
    )
    store.write(foreign)
    current.summary += " current"
    store.write(current)
    legacy = _legacy_v03_top1(store, query, tags=tags, keywords=keywords)
    governed_hits = store.retrieve(
        query,
        tags=tags,
        keywords=keywords,
        top_k=1,
        now=now,
        current_task_id="task-current",
    )
    governed = governed_hits[0].record.memory_id if governed_hits else None
    cases.append({
        "case": "task_scope",
        "expected": "mem_current_task",
        "legacy_top1": legacy,
        "governed_top1": governed,
        "legacy_correct": legacy == "mem_current_task",
        "governed_correct": governed == "mem_current_task",
        "governed_components": governed_hits[0].components if governed_hits else {},
    })
    store.close()

    # 3) Equal-purpose memories: governance favors fresh/important experience rather than
    # insertion-order tie breaking. This is a controlled tie, not a general accuracy claim.
    store = MemoryStore(MockEmbedding(dim=256), quality_floor=0.0, recency_half_life_sec=1000.0)
    stale = _record("mem_stale", created_at=now - 10_000, importance=0.1)
    fresh = _record("mem_fresh", created_at=now - 10, importance=1.0)
    store.write(stale)
    fresh.summary += " fresh"
    store.write(fresh)
    legacy = _legacy_v03_top1(store, query, tags=tags, keywords=keywords)
    governed_hits = store.retrieve(query, tags=tags, keywords=keywords, top_k=2, now=now)
    governed = governed_hits[0].record.memory_id if governed_hits else None
    cases.append({
        "case": "recency_importance",
        "expected": "mem_fresh",
        "legacy_top1": legacy,
        "governed_top1": governed,
        "legacy_correct": legacy == "mem_fresh",
        "governed_correct": governed == "mem_fresh",
        "governed_ranked": [
            {"memory_id": hit.record.memory_id, "score": hit.score, "components": hit.components}
            for hit in governed_hits
        ],
    })
    store.close()

    # Lifecycle pruning evidence: expiry first, then low-retention eviction to capacity.
    prune_store = MemoryStore(MockEmbedding(dim=256), quality_floor=0.0)
    doomed = _record(
        "mem_prune_expired",
        created_at=now - 100,
        expiration_policy="ttl",
        expires_at=now - 1,
        quality=1.0,
        importance=1.0,
    )
    low = _record("mem_prune_low", created_at=now - 10000, quality=0.35, importance=0.1)
    high1 = _record("mem_prune_high1", created_at=now - 10, quality=0.95, importance=0.95)
    high2 = _record("mem_prune_high2", created_at=now - 20, quality=0.9, importance=0.9)
    for i, rec in enumerate((doomed, low, high1, high2)):
        if i:
            rec.summary += f" variant-{i}"
        prune_store.write(rec)
    prune_stats = prune_store.prune(now=now, max_records=2)
    remaining = sorted(rec.memory_id for rec in prune_store.all_records())
    prune_store.close()

    legacy_correct = sum(bool(case["legacy_correct"]) for case in cases)
    governed_correct = sum(bool(case["governed_correct"]) for case in cases)
    return {
        "suite": "memory_governance",
        "env": _env_info(),
        "methodology": {
            "scope": "targeted adversarial lifecycle cases; not a general retrieval benchmark",
            "legacy": "v0.3 semantic/keyword/tag ranking multiplied by quality; no TTL/task scope/recency/importance",
            "governed": "v0.4 lifecycle filtering plus explainable relevance/quality/recency/importance/usage ranking",
            "clock": now,
        },
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "legacy_correct": legacy_correct,
            "governed_correct": governed_correct,
            "legacy_accuracy": legacy_correct / len(cases),
            "governed_accuracy": governed_correct / len(cases),
            "prune": {
                "expired": prune_stats.expired,
                "evicted": prune_stats.evicted,
                "remaining": prune_stats.remaining,
                "removed_ids": list(prune_stats.removed_ids),
                "remaining_ids": remaining,
            },
        },
    }


def render_memory_governance_report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        "# Memory Governance Targeted Validation",
        "",
        "> This is a controlled adversarial lifecycle suite, not a claim of general retrieval quality.",
        "",
        f"- Legacy v0.3 correct: {s['legacy_correct']}/{s['cases']} ({s['legacy_accuracy']:.1%})",
        f"- Governed v0.4 correct: {s['governed_correct']}/{s['cases']} ({s['governed_accuracy']:.1%})",
        f"- Source SHA-256: {payload['env']['source_tree_sha256']}",
        "",
        "| Case | Expected | v0.3 top-1 | governed top-1 | v0.3 | governed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in payload["cases"]:
        lines.append(
            f"| {case['case']} | {case['expected']} | {case['legacy_top1']} | "
            f"{case['governed_top1']} | {'PASS' if case['legacy_correct'] else 'FAIL'} | "
            f"{'PASS' if case['governed_correct'] else 'FAIL'} |"
        )
    p = s["prune"]
    lines += [
        "",
        "## Lifecycle pruning",
        "",
        f"- expired removed: {p['expired']}",
        f"- capacity evicted: {p['evicted']}",
        f"- remaining: {p['remaining_ids']}",
        "",
    ]
    return "\n".join(lines)


def write_memory_governance_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "memory_governance")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_memory_governance_report(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "suite": payload["suite"],
                "environment": payload["env"],
                "methodology": payload["methodology"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    return out


def run_memory_governance_cli(args: Any) -> int:
    payload = run_memory_governance_benchmark()
    out = write_memory_governance_results(payload)
    print(render_memory_governance_report(payload))
    print(f"\nResults saved to: {out}")
    return 0


__all__ = [
    "run_memory_governance_benchmark",
    "render_memory_governance_report",
    "write_memory_governance_results",
    "run_memory_governance_cli",
]
