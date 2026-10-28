"""Concurrent independent task throughput benchmark."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import asyncio
import json
from pathlib import Path
import statistics
import time
from typing import Any

from agent_runtime.config import load_config
from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator


def _run_one(index: int, cfg) -> dict[str, Any]:
    template = _self_contained_tasks()[index % len(_self_contained_tasks())]
    task = replace(template, task_id=f"concurrency-{index}-{template.task_id}")
    started = time.perf_counter()
    ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True, use_sandbox=True)
    result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
    elapsed = time.perf_counter() - started
    return {
        "success": result.success,
        "latency_sec": elapsed,
        "messages": result.metrics.message_count,
        "ipc_roundtrip_sec": result.metrics.ipc_roundtrip_sec,
        "error": result.error,
    }


def _ci95(values: list[float]) -> dict[str, float]:
    if not values:
        return {"median": 0.0, "ci95_low": 0.0, "ci95_high": 0.0}
    ordered = sorted(values)
    median = statistics.median(ordered)
    if len(values) < 2:
        return {"median": median, "ci95_low": median, "ci95_high": median}
    mean = statistics.fmean(values)
    half = 1.96 * statistics.stdev(values) / (len(values) ** 0.5)
    return {"median": median, "ci95_low": mean - half, "ci95_high": mean + half}


def run_concurrency_benchmark(*, repeat: int = 5, levels: tuple[int, ...] = (1, 4, 8, 16)) -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    cfg = load_config(load_env=False, control_transport="uds", state_shared_memory=True, structured_wire_codec="session_msgpack")
    # Warm the router, fork-server, imports, and shared-memory paths with a complete
    # K=4 batch. Its observations are deliberately excluded from the reported samples.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_run_one, range(4), [cfg] * 4))
    results: dict[str, Any] = {}
    for level in levels:
        rows: list[dict[str, Any]] = []
        round_summaries: list[dict[str, float]] = []
        for round_index in range(repeat):
            with ThreadPoolExecutor(max_workers=level) as pool:
                round_rows = list(pool.map(lambda i: _run_one(i + round_index * level, cfg), range(level)))
            rows.extend(round_rows)
            round_elapsed = sum(float(row["latency_sec"]) for row in round_rows)
            round_summaries.append({
                "throughput_tasks_per_sec": level / max(1e-9, max(float(row["latency_sec"]) for row in round_rows)),
                "mean_latency_sec": statistics.fmean(float(row["latency_sec"]) for row in round_rows),
                "p50_latency_sec": statistics.median(float(row["latency_sec"]) for row in round_rows),
                "p95_latency_sec": sorted(float(row["latency_sec"]) for row in round_rows)[min(level - 1, max(0, int(0.95 * level)))],
                "ipc_roundtrip_sec": statistics.fmean(float(row["ipc_roundtrip_sec"]) for row in round_rows),
                "serial_work_sec": round_elapsed,
            })
        latencies = [float(row["latency_sec"]) for row in rows]
        throughput_stats = _ci95([row["throughput_tasks_per_sec"] for row in round_summaries])
        p50_stats = _ci95([row["p50_latency_sec"] for row in round_summaries])
        p95_stats = _ci95([row["p95_latency_sec"] for row in round_summaries])
        results[str(level)] = {
            "tasks": len(rows),
            "success_count": sum(int(row["success"]) for row in rows),
            "error_count": sum(int(not row["success"]) for row in rows),
            "throughput_tasks_per_sec": len(rows) / max(1e-9, sum(latencies) / max(1, repeat)),
            "p50_latency_sec": statistics.median(latencies) if latencies else 0.0,
            "p95_latency_sec": sorted(latencies)[min(len(latencies) - 1, max(0, int(0.95 * len(latencies))))] if latencies else 0.0,
            "throughput_stats": throughput_stats,
            "p50_stats": p50_stats,
            "p95_stats": p95_stats,
            "round_summaries": round_summaries,
            "bottleneck_breakdown": {
                "router_rtt_mean_sec": statistics.fmean(float(row["ipc_roundtrip_sec"]) for row in rows) if rows else 0.0,
                "task_wall_mean_sec": statistics.fmean(latencies) if latencies else 0.0,
                "unmeasured": "fork-server lock wait and SQLite write time are not exposed by this runtime path",
            },
            "rows": rows,
        }
    return {
        "suite": "concurrency",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "env": _env_info(),
        "methodology": {"levels": list(levels), "repeat": repeat, "warmup": "one complete K=4 batch discarded", "forkserver_note": "router RTT is measured; fork-server lock and SQLite write timings remain explicitly uninstrumented"},
        "results": results,
    }


def render_concurrency_report(payload: dict[str, Any]) -> str:
    lines = ["# Concurrent Task Throughput", "", "| K | Tasks | Success | Errors | Throughput median [95% CI] | P50 median [95% CI] s | P95 median [95% CI] s | Router RTT mean ms |", "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for key, row in payload["results"].items():
        t, p50, p95 = row["throughput_stats"], row["p50_stats"], row["p95_stats"]
        rtt = row["bottleneck_breakdown"]["router_rtt_mean_sec"] * 1000
        lines.append(f"| {key} | {row['tasks']} | {row['success_count']} | {row['error_count']} | {t['median']:.3f} [{t['ci95_low']:.3f}, {t['ci95_high']:.3f}] | {p50['median']:.3f} [{p50['ci95_low']:.3f}, {p50['ci95_high']:.3f}] | {p95['median']:.3f} [{p95['ci95_low']:.3f}, {p95['ci95_high']:.3f}] | {rtt:.3f} |")
    lines.extend(["", "K=16 success is the acceptance gate. Router RTT is measured per task; fork-server lock wait and SQLite write time are explicitly not instrumented in this release.", f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`", ""])
    return "\n".join(lines)


def write_concurrency_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "concurrency")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_concurrency_report(payload), encoding="utf-8")
    _write_checksum_manifest(out)
    return out


def run_concurrency_cli(args) -> int:
    payload = run_concurrency_benchmark(repeat=args.repeat)
    out = write_concurrency_results(payload)
    print(render_concurrency_report(payload))
    print(f"\nResults: {out}")
    return 0


__all__ = ["run_concurrency_benchmark", "write_concurrency_results"]
