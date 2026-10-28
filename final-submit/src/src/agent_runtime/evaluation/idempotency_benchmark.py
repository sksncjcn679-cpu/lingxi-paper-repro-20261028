"""Completed-request idempotency replay benchmark.

This benchmark measures one narrow reliability question: after a successful task has
stored completed stage results in the persistent idempotency ledger, how much work does a
fresh Runtime avoid when the *same logical delivery* is received again?

The default provider is deterministic mock, so latency is only a local microbenchmark.
The primary evidence is avoided LLM/tool execution and the explicit replay counter; no
real-model cost or exactly-once claim is inferred.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import tempfile
import time
from typing import Any

from agent_runtime.config import load_config
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(0.95 * len(ordered) + 0.999999) - 1))
    return float(ordered[idx])


def run_idempotency_benchmark(*, repeat: int = 20) -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    first_ms: list[float] = []
    replay_ms: list[float] = []
    first_calls: list[dict[str, int]] = []
    replay_calls: list[dict[str, int]] = []

    for i in range(repeat):
        with tempfile.TemporaryDirectory(prefix="agent-idem-bench-") as td:
            task = replace(make_demo_task(), task_id=f"task-idem-bench-{i:04d}")
            cfg = load_config(
                load_env=False,
                data_dir=td,
                idempotency_persistent=True,
                idempotency_ttl_sec=3600,
                event_journal_enabled=False,
            )

            ctx1 = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
            t0 = time.perf_counter_ns()
            first = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx1))
            t1 = time.perf_counter_ns()
            if not first.success:
                raise RuntimeError("first idempotency benchmark run failed")

            # New agents/context force a fresh Runtime object; the completed ledger is
            # reopened from disk by the next Orchestrator instance.
            ctx2 = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
            t2 = time.perf_counter_ns()
            replay = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx2))
            t3 = time.perf_counter_ns()
            if not replay.success or replay.outputs.get("answer") != first.outputs.get("answer"):
                raise RuntimeError("idempotency replay changed task semantics")

            first_ms.append((t1 - t0) / 1e6)
            replay_ms.append((t3 - t2) / 1e6)
            first_calls.append(
                {
                    "llm_calls": int(first.metrics.llm_calls),
                    "tool_calls": int(first.metrics.tool_calls),
                    "replays": int(first.metrics.idempotency_replays),
                }
            )
            replay_calls.append(
                {
                    "llm_calls": int(replay.metrics.llm_calls),
                    "tool_calls": int(replay.metrics.tool_calls),
                    "replays": int(replay.metrics.idempotency_replays),
                }
            )

    first_median = float(statistics.median(first_ms))
    replay_median = float(statistics.median(replay_ms))
    mean_first_llm = statistics.fmean(x["llm_calls"] for x in first_calls)
    mean_replay_llm = statistics.fmean(x["llm_calls"] for x in replay_calls)
    mean_first_tools = statistics.fmean(x["tool_calls"] for x in first_calls)
    mean_replay_tools = statistics.fmean(x["tool_calls"] for x in replay_calls)
    return {
        "suite": "idempotency",
        "repeat": repeat,
        "env": _env_info(),
        "methodology": {
            "scope": "completed-request replay after a prior successful run",
            "provider": "deterministic mock; latency is a local microbenchmark",
            "freshness": "new agents/context and reopened SQLite ledger for every replay",
            "boundary": (
                "does not claim exactly-once for two concurrent first deliveries; "
                "the ledger deduplicates only after a completed result exists"
            ),
        },
        "summary": {
            "first_latency_ms": {
                "median": first_median,
                "p95": _p95(first_ms),
            },
            "replay_latency_ms": {
                "median": replay_median,
                "p95": _p95(replay_ms),
            },
            "median_latency_reduction": (
                1.0 - replay_median / first_median if first_median else 0.0
            ),
            "first_mean_llm_calls": mean_first_llm,
            "replay_mean_llm_calls": mean_replay_llm,
            "llm_call_avoidance": (
                1.0 - mean_replay_llm / mean_first_llm if mean_first_llm else 0.0
            ),
            "first_mean_tool_calls": mean_first_tools,
            "replay_mean_tool_calls": mean_replay_tools,
            "tool_call_avoidance": (
                1.0 - mean_replay_tools / mean_first_tools if mean_first_tools else None
            ),
            "replay_events_per_run": statistics.fmean(x["replays"] for x in replay_calls),
        },
        "raw": {
            "first_latency_ms": first_ms,
            "replay_latency_ms": replay_ms,
            "first_calls": first_calls,
            "replay_calls": replay_calls,
        },
    }


def render_idempotency_report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    tool_avoid = s["tool_call_avoidance"]
    tool_text = "N/A" if tool_avoid is None else f"{tool_avoid:.1%}"
    return "\n".join(
        [
            "# Persistent Idempotency Replay Benchmark",
            "",
            "> Controlled local reliability benchmark. It does not claim distributed exactly-once semantics.",
            "",
            f"- Repeat: {payload['repeat']}",
            f"- Source SHA-256: {payload['env']['source_tree_sha256']}",
            f"- First-run median: {s['first_latency_ms']['median']:.4f} ms",
            f"- Replay median: {s['replay_latency_ms']['median']:.4f} ms",
            f"- Median latency reduction: {s['median_latency_reduction']:.1%}",
            f"- LLM calls: {s['first_mean_llm_calls']:.1f} -> {s['replay_mean_llm_calls']:.1f} "
            f"(avoidance {s['llm_call_avoidance']:.1%})",
            f"- Tool calls: {s['first_mean_tool_calls']:.1f} -> {s['replay_mean_tool_calls']:.1f} "
            f"(avoidance {tool_text})",
            f"- Completed-stage replay events per replay run: {s['replay_events_per_run']:.1f}",
            "",
            "## Interpretation boundary",
            "",
            "The second run is intentionally cheaper because it reuses *completed* stage results. "
            "This demonstrates duplicate-delivery suppression after completion, not a transactional "
            "guarantee for concurrent first execution. Latency uses the mock provider and must not be "
            "extrapolated to real-model wall-clock savings.",
            "",
        ]
    )


def write_idempotency_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "idempotency")
    (out / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "report.md").write_text(render_idempotency_report(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "suite": payload["suite"],
                "repeat": payload["repeat"],
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


def run_idempotency_benchmark_cli(args) -> int:
    payload = run_idempotency_benchmark(repeat=args.repeat)
    out = write_idempotency_results(payload)
    print(out)
    return 0


__all__ = [
    "run_idempotency_benchmark",
    "write_idempotency_results",
    "run_idempotency_benchmark_cli",
]
