"""Competition-aligned memory retrieval accuracy benchmark.

The official task asks for keyword/tag/semantic retrieval and cross-task reuse evidence.
This suite isolates *retrieval quality* from the end-to-end M0/M1 success experiment:
- two relevant memories represent the two required continuous task groups;
- six hard distractors share partial vocabulary/tags;
- semantic-only, keyword-only, tag-only, and hybrid retrieval are measured separately;
- precision@1, recall@3 and MRR are reported without claiming real-model generalization.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.config import load_config
from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding


def _record(mid: str, topic: str, summary: str, *, tags: list[str], keywords: list[str]) -> MemoryRecord:
    return MemoryRecord(
        memory_id=mid,
        source_agent="reviewer",
        task_topic=topic,
        summary=summary,
        memory_type=MemoryType.PROCEDURAL,
        tags=tags,
        keywords=keywords,
        quality_score=0.9,
        importance_score=0.8,
        success_feedback=True,
        payload={"operation": "codefix", "solution": mid},
    )


def _rank(hit_ids: list[str], relevant: str) -> int | None:
    try:
        return hit_ids.index(relevant) + 1
    except ValueError:
        return None


def _metrics(cases: list[dict[str, Any]], key: str) -> dict[str, float]:
    top1 = 0
    recall3 = 0
    rr = 0.0
    for case in cases:
        ids = case[key]
        relevant = case["relevant"]
        rank = _rank(ids, relevant)
        top1 += int(bool(ids) and ids[0] == relevant)
        recall3 += int(rank is not None and rank <= 3)
        rr += 0.0 if rank is None else 1.0 / rank
    n = max(1, len(cases))
    return {
        "precision_at_1": top1 / n,
        "recall_at_3": recall3 / n,
        "mrr": rr / n,
    }


def run_memory_accuracy_benchmark() -> dict[str, Any]:
    cfg = load_config(load_env=True)
    store = MemoryStore(build_embedding(cfg), quality_floor=0.0)
    records = [
        _record(
            "mem_linux_service",
            "linux-service-diagnosis",
            "systemd service startup diagnosis port conflict permission dependency procedure",
            tags=["linux", "systemd", "diagnosis", "service_start_failure"],
            keywords=["diagnose_service", "service_start_failure", "systemctl", "port", "permission"],
        ),
        _record(
            "mem_openeuler_migration",
            "openeuler-migration-assessment",
            "openEuler migration compatibility architecture python ABI native dependency systemd procedure",
            tags=["openeuler", "migration", "compatibility"],
            keywords=["assess_migration", "openeuler_migration", "architecture", "python_abi", "systemd"],
        ),
        _record("mem_d1", "linux-network", "linux socket timeout retry network diagnosis", tags=["linux", "diagnosis"], keywords=["network", "timeout", "service"]),
        _record("mem_d2", "systemd-logging", "systemd journal logging formatter investigation", tags=["linux", "systemd"], keywords=["journal", "logging", "systemctl"]),
        _record("mem_d3", "rpm-build", "openEuler rpm package rebuild signing workflow", tags=["openeuler", "migration"], keywords=["rpm", "build", "migration"]),
        _record("mem_d4", "python-upgrade", "python ABI virtual environment dependency upgrade", tags=["compatibility"], keywords=["python_abi", "dependency", "upgrade"]),
        _record("mem_d5", "container-port", "container port binding and health check", tags=["linux"], keywords=["port", "container", "health"]),
        _record("mem_d6", "generic-migration", "generic Linux application migration checklist", tags=["migration"], keywords=["migration", "compatibility", "service"]),
    ]
    for i, record in enumerate(records):
        # Prevent exact-content near-duplicate versioning while preserving deterministic order.
        record.summary += f" case-{i}"
        store.write(record)

    queries = [
        {
            "group": "Linux service diagnosis L1→L2",
            "relevant": "mem_linux_service",
            "query": "systemd service failed permission port conflict diagnostic procedure",
            "tags": ["linux", "systemd", "diagnosis", "service_start_failure"],
            "keywords": ["diagnose_service", "service_start_failure", "systemctl", "port", "permission"],
        },
        {
            "group": "openEuler migration M1→M2",
            "relevant": "mem_openeuler_migration",
            "query": "openEuler migration native architecture python ABI systemd compatibility",
            "tags": ["openeuler", "migration", "compatibility"],
            "keywords": ["assess_migration", "openeuler_migration", "architecture", "python_abi", "systemd"],
        },
    ]
    cases: list[dict[str, Any]] = []
    for q in queries:
        semantic = store.retrieve(q["query"], top_k=3)
        keyword = store.retrieve(None, keywords=q["keywords"], top_k=3)
        tag = store.retrieve(None, tags=q["tags"], top_k=3)
        hybrid = store.retrieve(q["query"], tags=q["tags"], keywords=q["keywords"], top_k=3)
        cases.append(
            {
                **q,
                "semantic_top3": [h.record.memory_id for h in semantic],
                "keyword_top3": [h.record.memory_id for h in keyword],
                "tag_top3": [h.record.memory_id for h in tag],
                "hybrid_top3": [h.record.memory_id for h in hybrid],
                "hybrid_score_breakdown": [
                    {"memory_id": h.record.memory_id, "score": h.score, "components": h.components}
                    for h in hybrid
                ],
            }
        )
    store.close()

    embedding_label = (
        "mock (model field ignored)" if cfg.embedding.provider.lower() == "mock"
        else f"{cfg.embedding.provider}:{cfg.embedding.model}"
    )
    return {
        "suite": "memory_accuracy",
        "env": _env_info(),
        "methodology": {
            "scope": "controlled two-group retrieval benchmark with six overlapping distractors",
            "embedding_provider": cfg.embedding.provider,
            "embedding_model": cfg.embedding.model,
            "embedding_label": embedding_label,
            "warning": "mock and lexical results are controlled workload evidence; OpenAI results require successful external API calls",
            "relevant_memories": 2,
            "distractors": 6,
            "top_k": 3,
        },
        "cases": cases,
        "summary": {
            "semantic": _metrics(cases, "semantic_top3"),
            "keyword": _metrics(cases, "keyword_top3"),
            "tag": _metrics(cases, "tag_top3"),
            "hybrid": _metrics(cases, "hybrid_top3"),
        },
    }


def render_memory_accuracy_report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        "# Memory Retrieval Accuracy — Competition Evidence",
        "",
        f"> Controlled two-group benchmark with six hard distractors. Provider: `{payload['methodology'].get('embedding_label', payload['methodology']['embedding_provider'])}`.",
        "",
        "| Retrieval mode | Precision@1 | Recall@3 | MRR |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key, label in (("semantic", "Semantic"), ("keyword", "Keyword"), ("tag", "Tag"), ("hybrid", "Hybrid")):
        m = s[key]
        lines.append(f"| {label} | {m['precision_at_1']:.1%} | {m['recall_at_3']:.1%} | {m['mrr']:.3f} |")
    lines += [
        "",
        "## Two required continuous-task groups",
        "",
        "| Group | Relevant memory | Semantic top-3 | Keyword top-3 | Tag top-3 | Hybrid top-3 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in payload["cases"]:
        lines.append(
            f"| {case['group']} | {case['relevant']} | {', '.join(case['semantic_top3'])} | "
            f"{', '.join(case['keyword_top3'])} | {', '.join(case['tag_top3'])} | "
            f"{', '.join(case['hybrid_top3'])} |"
        )
    lines += ["", f"Source SHA-256: `{payload['env']['source_tree_sha256']}`", ""]
    return "\n".join(lines)


def write_memory_accuracy_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "memory_accuracy")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_memory_accuracy_report(payload), encoding="utf-8")
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


def run_memory_accuracy_cli(_args) -> int:
    payload = run_memory_accuracy_benchmark()
    out = write_memory_accuracy_results(payload)
    print(render_memory_accuracy_report(payload))
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["run_memory_accuracy_benchmark", "write_memory_accuracy_results", "run_memory_accuracy_cli"]
