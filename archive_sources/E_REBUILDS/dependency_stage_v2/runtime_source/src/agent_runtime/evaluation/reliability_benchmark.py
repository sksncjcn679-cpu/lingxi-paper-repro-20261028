"""Durability overhead benchmark for the runtime event journal.

The benchmark measures end-to-end wall-clock task latency for identical deterministic
mock tasks with journaling disabled, buffered, task-boundary fsync, and per-event fsync.
It reports the reliability/performance tradeoff rather than treating the fastest mode as
universally best.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import tempfile
import time
from typing import Any

from agent_runtime.config import load_config
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.runtime.builder import run_task
from agent_runtime.runtime.event_log import EventJournal, journal_path_for
from agent_runtime.scenarios.demo import make_demo_task


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def _stats(values: list[float]) -> dict[str, float]:
    return {
        "mean_ms": statistics.fmean(values),
        "median_ms": statistics.median(values),
        "p95_ms": _percentile(values, 0.95),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def run_reliability_benchmark(*, repeat: int = 20, warmup: int = 3) -> dict[str, Any]:
    if repeat <= 0 or warmup < 0:
        raise ValueError("repeat must be > 0 and warmup >= 0")
    modes: list[tuple[str, bool, str]] = [
        ("off", False, "buffered"),
        ("buffered", True, "buffered"),
        ("task", True, "task"),
        ("sync", True, "sync"),
    ]
    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="agent-runtime-journal-bench-") as td:
        root = Path(td)
        for label, enabled, durability in modes:
            samples: list[float] = []
            event_counts: list[int] = []
            journal_bytes: list[int] = []
            total_runs = repeat + warmup
            for i in range(total_runs):
                task = make_demo_task()
                data_dir = root / label / f"run-{i:04d}"
                cfg = load_config(
                    load_env=False,
                    data_dir=str(data_dir),
                    event_journal_enabled=enabled,
                    event_journal_durable=True,
                    event_journal_durability=durability,
                )
                start = time.perf_counter_ns()
                result = asyncio.run(
                    run_task(task, config=cfg, mode="structured", use_state_exchange=True)
                )
                elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000.0
                if not result.success:
                    raise AssertionError(f"reliability benchmark task failed in {label}")
                if i < warmup:
                    continue
                samples.append(elapsed_ms)
                if enabled:
                    path = journal_path_for(data_dir / "events", task.task_id)
                    journal = EventJournal(path, durability="buffered")
                    event_counts.append(len(journal.verify()))
                    journal_bytes.append(path.stat().st_size)
                else:
                    event_counts.append(0)
                    journal_bytes.append(0)
            results[label] = {
                "enabled": enabled,
                "durability": durability if enabled else "off",
                "latency": _stats(samples),
                "events_mean": statistics.fmean(event_counts),
                "journal_bytes_mean": statistics.fmean(journal_bytes),
            }

    base = results["off"]["latency"]["median_ms"]
    for label, result in results.items():
        med = result["latency"]["median_ms"]
        result["overhead_vs_off"] = (med / base - 1.0) if base else None
    return {
        "suite": "reliability",
        "repeat": repeat,
        "warmup": warmup,
        "env": _env_info(),
        "methodology": {
            "workload": "deterministic demo task; structured mode + state exchange",
            "latency_scope": "external end-to-end wall clock including journal I/O",
            "off": "no event journal",
            "buffered": "append-only hash chain, no implicit fsync",
            "task": "fsync only on task.completed/task.failed/task.paused",
            "sync": "fsync every event before append returns",
            "caution": "filesystem/fsync latency is host-specific; compare modes on the same host",
        },
        "results": results,
    }


def render_reliability_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Runtime Journal Durability Trade-off",
        "",
        f"- repeat: {payload['repeat']} / warmup: {payload['warmup']}",
        f"- source SHA-256: {payload['env']['source_tree_sha256']}",
        "",
        "| Mode | Median ms | P95 ms | Overhead vs off | Events/run | Journal B/run | Durability boundary |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for label in ("off", "buffered", "task", "sync"):
        r = payload["results"][label]
        overhead = r["overhead_vs_off"]
        overhead_text = f"{overhead:.1%}" if overhead is not None else "N/A"
        lines.append(
            f"| {label} | {r['latency']['median_ms']:.4f} | {r['latency']['p95_ms']:.4f} | "
            f"{overhead_text} | {r['events_mean']:.1f} | {r['journal_bytes_mean']:.0f} | "
            f"{r['durability']} |"
        )
    lines += [
        "",
        "> `buffered` is not crash-durable. `task` fsyncs at task boundaries; `sync` fsyncs every event. Relative latency ordering is host/filesystem dependent, especially for millisecond-scale mock workloads, so no mode is labeled universally fastest.",
        "",
    ]
    return "\n".join(lines)


def write_reliability_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "reliability")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_reliability_report(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "suite": payload["suite"],
                "repeat": payload["repeat"],
                "warmup": payload["warmup"],
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


def run_reliability_benchmark_cli(args: Any) -> int:
    payload = run_reliability_benchmark(repeat=args.repeat, warmup=args.warmup)
    out = write_reliability_results(payload)
    print(render_reliability_report(payload))
    print(f"\nResults saved to: {out}")
    return 0


__all__ = [
    "run_reliability_benchmark",
    "render_reliability_report",
    "write_reliability_results",
    "run_reliability_benchmark_cli",
]
