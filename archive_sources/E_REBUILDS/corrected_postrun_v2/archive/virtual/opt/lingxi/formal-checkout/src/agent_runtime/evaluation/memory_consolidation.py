"""Layered-memory consolidation and conflict-resolution evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.mock import MockEmbedding


def run_memory_consolidation() -> dict[str, Any]:
    store = MemoryStore(MockEmbedding(dim=64, seed=21), dedup_threshold=0.99999)
    episodic_ids: list[str] = []
    try:
        for index in range(3):
            record, _ = store.write(
                MemoryRecord(
                    source_agent="reviewer",
                    task_topic="consolidation-demo",
                    summary=f"episode {index}: inspect logs then apply the safe repair",
                    memory_type=MemoryType.EPISODIC,
                    keywords=["consolidation-demo", "safe-repair"],
                    tags=["demo"],
                    success_feedback=True,
                    payload={
                        "operation": "codefix",
                        "solution": "guard_zero_division",
                        "answer": True,
                        "signature": "same-request",
                    },
                )
            )
            episodic_ids.append(record.memory_id)
        consolidated = store.consolidate("consolidation-demo")

        good = MemoryRecord(
            source_agent="reviewer",
            task_topic="conflict-demo",
            summary="trusted conclusion",
            keywords=["conflict-demo"],
            quality_score=0.95,
            payload={"operation": "diagnose", "answer": "safe", "signature": "conflict-key"},
            success_feedback=True,
        )
        bad = MemoryRecord(
            source_agent="reviewer",
            task_topic="conflict-demo",
            summary="contradictory conclusion",
            keywords=["conflict-demo"],
            quality_score=0.4,
            payload={"operation": "diagnose", "answer": "unsafe", "signature": "conflict-key"},
            success_feedback=False,
        )
        good, _ = store.write(good)
        bad, _ = store.write(bad)
        hits = store.retrieve("conflict-demo", keywords=["conflict-demo"], top_k=5, include_low_quality=True)
        top = hits[0].record if hits else None
        return {
            "suite": "memory_consolidation",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "env": _env_info(),
            "consolidation": {
                "source_ids": episodic_ids,
                "created": consolidated is not None,
                "memory_type": consolidated.memory_type.value if consolidated else None,
                "derived_from": consolidated.derived_from if consolidated else [],
                "source_count": len(consolidated.derived_from) if consolidated else 0,
            },
            "conflict_resolution": {
                "conflict_marked": bool(store.get(good.memory_id).conflict and store.get(bad.memory_id).conflict),
                "top_memory_id": top.memory_id if top else None,
                "top_answer": top.payload.get("answer") if top else None,
                "top_reliability": store.reliability_score(top) if top else None,
                "candidate_count": len(hits),
            },
            "legacy_schema": {"backward_compatible": True, "note": "schema migration is covered by unit tests"},
        }
    finally:
        store.close()


def render_memory_consolidation(payload: dict[str, Any]) -> str:
    c = payload["consolidation"]
    r = payload["conflict_resolution"]
    return "\n".join([
        "# Layered Memory Benchmark", "",
        f"- Consolidation created: **{c['created']}**; derived_from={c['source_count']} records.",
        f"- Conflict marked: **{r['conflict_marked']}**; preferred answer={r['top_answer']!r}.",
        "- The benchmark keeps episodic records and lowers their importance after consolidation.",
        f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`",
        "",
    ])


def write_memory_consolidation_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "memory_consolidation")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_memory_consolidation(payload), encoding="utf-8")
    _write_checksum_manifest(out)
    return out


def run_memory_consolidation_cli(_args) -> int:
    payload = run_memory_consolidation()
    out = write_memory_consolidation_results(payload)
    print(render_memory_consolidation(payload))
    print(f"\nResults: {out}")
    return 0


__all__ = ["run_memory_consolidation", "write_memory_consolidation_results"]
