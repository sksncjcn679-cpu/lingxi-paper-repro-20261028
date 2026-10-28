"""Feedback-calibrated memory reliability benchmark."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.mock import MockEmbedding


def _memory(memory_id: str, *, good: bool, quality: float) -> MemoryRecord:
    # Nearly identical relevance on purpose: this suite isolates downstream feedback governance.
    return MemoryRecord(
        memory_id=memory_id,
        source_agent="reviewer",
        task_topic="python zero division repair",
        summary=(
            "repair zero division denominator guard reusable strategy "
            + ("validated" if good else "risky")
        ),
        memory_type=MemoryType.PROCEDURAL,
        tags=["python", "repair"],
        keywords=["divide", "zero", "guard"],
        quality_score=quality,
        importance_score=0.8,
        payload={"strategy": "good" if good else "bad"},
    )


def _legacy_quality_only_top1(store: MemoryStore, query: str) -> str | None:
    """v0.5-style governed score without reliability/quarantine."""
    qvec = store.embedding.embed([query])[0]
    sem = dict(store.index.search(qvec, top_k=max(16, store.count())))
    ranked: list[tuple[float, str]] = []
    now = max((r.created_at for r in store.all_records()), default=0.0) + 1.0
    for rec in store.all_records():
        relevance = 0.65 * max(0.0, float(sem.get(rec.memory_id, 0.0))) + 0.20 + 0.15
        score = (
            0.60 * relevance
            + 0.20 * rec.quality_score
            + 0.08 * store._recency_score(rec, now)  # noqa: SLF001 - exact legacy comparator
            + 0.08 * rec.importance_score
            + 0.04 * store._usage_score(rec.reuse_count)  # noqa: SLF001
        )
        ranked.append((float(score), rec.memory_id))
    ranked.sort(reverse=True)
    return ranked[0][1] if ranked else None


def _run_policy(*, reliability_governed: bool, rounds: int = 6) -> dict[str, Any]:
    store = MemoryStore(MockEmbedding(dim=256), dedup_threshold=1.1, quality_floor=0.0)
    bad, _ = store.write(_memory("mem_bad", good=False, quality=1.0))
    good, _ = store.write(_memory("mem_good", good=True, quality=0.45))
    query = "python zero division denominator guard repair"
    trace: list[dict[str, Any]] = []
    harmful = 0
    effective = 0

    for turn in range(1, rounds + 1):
        if reliability_governed:
            hits = store.retrieve(
                query,
                tags=["python", "repair"],
                keywords=["divide", "zero", "guard"],
                top_k=1,
            )
            chosen = hits[0].record.memory_id if hits else None
        else:
            chosen = _legacy_quality_only_top1(store, query)
        if chosen is None:
            break
        is_good = chosen == good.memory_id
        store.mark_used(chosen)
        store.record_feedback(chosen, effective=is_good)
        harmful += int(not is_good)
        effective += int(is_good)
        bad_now = store.get(bad.memory_id)
        good_now = store.get(good.memory_id)
        assert bad_now is not None and good_now is not None
        trace.append(
            {
                "turn": turn,
                "chosen": chosen,
                "effective": is_good,
                "bad_quality": bad_now.quality_score,
                "bad_reliability": store.reliability_score(bad_now),
                "bad_quarantined": bad_now.quarantined,
                "good_quality": good_now.quality_score,
                "good_reliability": store.reliability_score(good_now),
            }
        )
    final_bad = store.get(bad.memory_id)
    assert final_bad is not None
    store.close()
    return {
        "rounds": rounds,
        "harmful_adoptions": harmful,
        "effective_adoptions": effective,
        "bad_quarantined": final_bad.quarantined,
        "trace": trace,
    }


def run_memory_reliability_benchmark(*, rounds: int = 6) -> dict[str, Any]:
    if rounds < 2:
        raise ValueError("rounds must be >= 2")
    quality_only = _run_policy(reliability_governed=False, rounds=rounds)
    governed = _run_policy(reliability_governed=True, rounds=rounds)
    return {
        "suite": "memory_reliability",
        "env": _env_info(),
        "methodology": {
            "scope": "controlled repeated-adoption benchmark with one useful and one harmful but initially high-ranked memory",
            "baseline": "v0.5-style quality/recency/importance/usage ranking; no reliability signal or quarantine filtering",
            "governed": "Beta(1,1)-smoothed adopted-outcome reliability + quarantine after repeated harmful reuse",
            "rounds": rounds,
            "caution": "controlled governance benchmark; not a claim of general semantic retrieval accuracy",
        },
        "quality_only": quality_only,
        "reliability_governed": governed,
        "derived": {
            "harmful_reuse_reduction": (
                1.0 - governed["harmful_adoptions"] / quality_only["harmful_adoptions"]
                if quality_only["harmful_adoptions"]
                else 0.0
            )
        },
    }


def render_memory_reliability_report(payload: dict[str, Any]) -> str:
    old = payload["quality_only"]
    new = payload["reliability_governed"]
    return "\n".join(
        [
            "# Feedback-Calibrated Memory Reliability",
            "",
            "> Controlled repeated-adoption benchmark; not a general retrieval-accuracy claim.",
            "",
            f"- Source SHA-256: {payload['env']['source_tree_sha256']}",
            f"- Quality-only harmful adoptions: {old['harmful_adoptions']}",
            f"- Reliability-governed harmful adoptions: {new['harmful_adoptions']}",
            f"- Harmful-reuse reduction: {payload['derived']['harmful_reuse_reduction']:.1%}",
            f"- Harmful memory quarantined: {new['bad_quarantined']}",
            "",
            "The governed policy changes only after observed downstream adoption outcomes. Retrieval hits alone do not increase trust.",
            "",
        ]
    )


def write_memory_reliability_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "memory_reliability")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_memory_reliability_report(payload), encoding="utf-8")
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


def run_memory_reliability_benchmark_cli(args: Any) -> int:
    payload = run_memory_reliability_benchmark(rounds=args.rounds)
    out = write_memory_reliability_results(payload)
    print(render_memory_reliability_report(payload))
    print(f"\nResults saved to: {out}")
    return 0


__all__ = [
    "run_memory_reliability_benchmark",
    "render_memory_reliability_report",
    "write_memory_reliability_results",
    "run_memory_reliability_benchmark_cli",
]
