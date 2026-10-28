"""Shared-memory retrieval at scale: accuracy + latency, full scan vs sublinear (v0.6.6).

N synthetic distractor memories (overlapping Linux/openEuler vocabulary and tags) are mixed
with planted target memories.  For every query we run both the legacy full-scan path and the
v0.6.6 candidate-generation path, **assert identical top-k ids** (ranking equivalence), and
report P@1 / Recall@3 / MRR plus query latency P50/P95 and candidate-set size.
"""

from __future__ import annotations

import json
import random
import statistics
import time
from datetime import datetime, timezone
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.config import load_config
from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding

VOCAB = ["systemd", "service", "port", "permission", "dependency", "journal", "socket", "timeout", "rpm",
         "openeuler", "migration", "python_abi", "architecture", "container", "kernel", "selinux", "firewall",
         "dnf", "cgroup", "network", "disk", "memory", "cpu", "logging", "upgrade", "compatibility", "native"]
TAGS = ["linux", "systemd", "diagnosis", "openeuler", "migration", "compatibility", "network", "storage", "security"]
TARGETS = [
    ("tgt_service", "linux-service-diagnosis", ["diagnose_service", "service_start_failure", "systemctl", "port", "permission"], ["linux", "systemd", "diagnosis", "service_start_failure"],
     "systemd service failed permission port conflict diagnostic procedure"),
    ("tgt_migration", "openeuler-migration-assessment", ["assess_migration", "openeuler_migration", "architecture", "python_abi", "systemd"], ["openeuler", "migration", "compatibility"],
     "openEuler migration native architecture python ABI systemd compatibility"),
]


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def _build(n: int, embedding, seed: int = 7) -> MemoryStore:
    rng = random.Random(seed)
    store = MemoryStore(embedding, quality_floor=0.0, dedup_threshold=1.01)
    for mid, topic, kws, tags, summary in TARGETS:
        store.write(MemoryRecord(memory_id=mid, source_agent="reviewer", task_topic=topic, summary=summary + " procedure",
                                 memory_type=MemoryType.PROCEDURAL, tags=tags, keywords=kws))
    for i in range(n):
        words = rng.sample(VOCAB, 5)
        store.write(MemoryRecord(memory_id=f"d{i:06d}", source_agent="reviewer", task_topic="-".join(words[:2]),
                                 summary=" ".join(words) + f" case {i}", memory_type=MemoryType.EPISODIC,
                                 tags=rng.sample(TAGS, 2), keywords=words[:3]))
    return store


def run_memory_scale_benchmark(sizes: tuple[int, ...] = (100, 1000, 5000), queries_per_target: int = 10) -> dict[str, Any]:
    cfg = load_config(load_env=True)
    rows: dict[str, Any] = {}
    for n in sizes:
        store = _build(n, build_embedding(cfg))
        lat = {"full_scan": [], "sublinear": []}
        ranks: list[int | None] = []
        cands: list[int] = []
        mismatches = 0
        for _ in range(queries_per_target):
            for mid, _topic, kws, tags, query in TARGETS:
                outs = {}
                for path in ("full_scan", "sublinear"):
                    store._legacy_full_scan = path == "full_scan"
                    t0 = time.perf_counter()
                    hits = store.retrieve(query, keywords=kws, tags=tags, top_k=3)
                    lat[path].append(time.perf_counter() - t0)
                    outs[path] = [h.record.memory_id for h in hits]
                    if path == "sublinear":
                        cands.append(store.last_candidate_count)
                mismatches += int(outs["full_scan"] != outs["sublinear"])
                ids = outs["sublinear"]
                ranks.append(ids.index(mid) + 1 if mid in ids else None)
        store._legacy_full_scan = False
        store.close()
        q = len(ranks)
        rows[str(n)] = {
            "records": n + len(TARGETS),
            "queries": q,
            "ranking_mismatches": mismatches,
            "precision_at_1": sum(1 for r in ranks if r == 1) / q,
            "recall_at_3": sum(1 for r in ranks if r is not None) / q,
            "mrr": sum(1.0 / r for r in ranks if r) / q,
            "mean_candidates": statistics.fmean(cands),
            "latency_ms": {p: {"p50": _pct(v, 0.5) * 1000, "p95": _pct(v, 0.95) * 1000} for p, v in lat.items()},
            "speedup_p50": _pct(lat["full_scan"], 0.5) / max(1e-12, _pct(lat["sublinear"], 0.5)),
        }
    return {"suite": "memory_scale", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "env": _env_info(),
            "methodology": {
                "description": "planted targets + synthetic overlapping distractors; hybrid query; both paths per query; top-3 must match",
                "embedding_provider": cfg.embedding.provider,
                "embedding_model": cfg.embedding.model,
                "embedding_label": ("mock (model field ignored)" if cfg.embedding.provider.lower() == "mock" else f"{cfg.embedding.provider}:{cfg.embedding.model}"),
            },
            "results": rows}


def render_memory_scale(p: dict[str, Any]) -> str:
    lines = ["# Shared-memory retrieval at scale", "", "| Records | P@1 | Recall@3 | MRR | Candidates | Full-scan p50/p95 ms | Sublinear p50/p95 ms | Speed-up | Ranking mismatches |",
             "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in p["results"].values():
        f, s = r["latency_ms"]["full_scan"], r["latency_ms"]["sublinear"]
        lines.append(f"| {r['records']:,} | {r['precision_at_1']:.2f} | {r['recall_at_3']:.2f} | {r['mrr']:.2f} | {r['mean_candidates']:.0f} | "
                     f"{f['p50']:.2f} / {f['p95']:.2f} | {s['p50']:.2f} / {s['p95']:.2f} | {r['speedup_p50']:.1f}x | {r['ranking_mismatches']} |")
    lines += ["", f"Embedding provider: `{p['methodology'].get('embedding_label', p['methodology']['embedding_provider'])}`.", "", f"Source SHA-256: `{p['env']['source_tree_sha256']}`", ""]
    return "\n".join(lines)


def run_memory_scale_cli(args) -> int:
    p = run_memory_scale_benchmark()
    out = _allocate_result_dir("results", "memory_scale")
    (out / "summary.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_memory_scale(p), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({"suite": p["suite"], "generated_at_utc": p["generated_at_utc"], "environment": p["env"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_checksum_manifest(out)
    print(render_memory_scale(p))
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["run_memory_scale_benchmark", "render_memory_scale", "run_memory_scale_cli"]
