"""Controlled scheduler benchmark for the bounded async mailbox router.

The suite separates two questions:
1) what fixed request-routing overhead does a mailbox add to a no-op handler?
2) how does target worker parallelism scale a synthetic I/O-bound service time?
It also validates explicit reject-style backpressure under saturation.

This is a scheduler microbenchmark, not an LLM throughput claim.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.runner import (
    _allocate_result_dir,
    _env_info,
    _write_checksum_manifest,
)
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.scheduler.mailbox import AsyncMailboxRouter, MailboxFullError


def _msg(step: str) -> AgentMessage:
    return AgentMessage(
        trace_id="trace-mailbox-bench",
        task_id="task-mailbox-bench",
        step_id=step,
        source_agent="bench",
        target_agent="worker",
        action_type=ActionType.EXECUTE,
    )


def _reply(message: AgentMessage) -> AgentMessage:
    return AgentMessage(
        trace_id=message.trace_id,
        task_id=message.task_id,
        step_id=message.step_id,
        parent_message_id=message.message_id,
        causation_id=message.message_id,
        source_agent="worker",
        target_agent="bench",
        action_type=ActionType.RESULT,
        status=MessageStatus.OK,
    )


def _summary_us(samples_ns: list[int]) -> dict[str, float]:
    us = [sample / 1000.0 for sample in samples_ns]
    ordered = sorted(us)
    p95 = ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))]
    return {
        "mean_us": statistics.fmean(us),
        "median_us": statistics.median(us),
        "p95_us": p95,
    }


async def _echo_overhead(repeat: int) -> dict[str, Any]:
    async def handler(message: AgentMessage) -> AgentMessage:
        return _reply(message)

    direct: list[int] = []
    for i in range(repeat):
        message = _msg(f"direct-{i}")
        start = time.perf_counter_ns()
        reply = await handler(message)
        direct.append(time.perf_counter_ns() - start)
        if reply.status != MessageStatus.OK:
            raise AssertionError("direct echo failed")

    router = AsyncMailboxRouter(queue_size=max(16, min(repeat, 4096)), overflow="block")
    router.register("worker", handler, workers=1)
    await router.start()
    routed: list[int] = []
    try:
        for i in range(repeat):
            message = _msg(f"router-{i}")
            start = time.perf_counter_ns()
            reply = await router.request(message)
            routed.append(time.perf_counter_ns() - start)
            if reply.status != MessageStatus.OK:
                raise AssertionError("router echo failed")
        stats = router.stats("worker")
    finally:
        await router.close()

    direct_stats = _summary_us(direct)
    routed_stats = _summary_us(routed)
    return {
        "direct": direct_stats,
        "router": routed_stats,
        "router_fixed_overhead_median_us": routed_stats["median_us"] - direct_stats["median_us"],
        "processed": stats.processed,
    }


async def _parallel_service(total: int, workers: int, service_ms: float) -> dict[str, Any]:
    async def handler(message: AgentMessage) -> AgentMessage:
        await asyncio.sleep(service_ms / 1000.0)
        return _reply(message)

    router = AsyncMailboxRouter(queue_size=max(total, 16), overflow="block")
    router.register("worker", handler, workers=workers)
    await router.start()
    start = time.perf_counter()
    try:
        replies = await asyncio.gather(
            *(router.request(_msg(f"w{workers}-{i}"), timeout=10.0) for i in range(total))
        )
        elapsed = time.perf_counter() - start
        if not all(reply.status == MessageStatus.OK for reply in replies):
            raise AssertionError("mailbox parallel service failure")
        stats = router.stats("worker")
    finally:
        await router.close()
    return {
        "workers": workers,
        "requests": total,
        "service_ms": service_ms,
        "elapsed_ms": elapsed * 1000.0,
        "throughput_req_sec": total / elapsed if elapsed else 0.0,
        "max_depth": stats.max_depth,
        "processed": stats.processed,
    }


async def _backpressure_probe(queue_size: int, rejected_attempts: int) -> dict[str, Any]:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(message: AgentMessage) -> AgentMessage:
        entered.set()
        await release.wait()
        return _reply(message)

    router = AsyncMailboxRouter(queue_size=queue_size, overflow="reject")
    router.register("worker", handler, workers=1)
    await router.start()
    accepted: list[asyncio.Task[AgentMessage]] = []
    rejected = 0
    try:
        accepted.append(asyncio.create_task(router.request(_msg("active"))))
        await entered.wait()
        for i in range(queue_size):
            accepted.append(asyncio.create_task(router.request(_msg(f"queued-{i}"))))
        # Let the queued request coroutines execute their put_nowait calls.
        await asyncio.sleep(0)
        for i in range(rejected_attempts):
            try:
                await router.request(_msg(f"reject-{i}"))
            except MailboxFullError:
                rejected += 1
        release.set()
        await asyncio.gather(*accepted)
        stats = router.stats("worker")
    finally:
        release.set()
        await router.close()
    return {
        "queue_size": queue_size,
        "probe_attempts": rejected_attempts,
        "rejected": rejected,
        "router_rejected_metric": stats.rejected,
        "max_depth": stats.max_depth,
    }


def run_mailbox_benchmark(
    *,
    echo_repeat: int = 1000,
    parallel_requests: int = 200,
    service_ms: float = 2.0,
) -> dict[str, Any]:
    if echo_repeat <= 0 or parallel_requests <= 0 or service_ms < 0:
        raise ValueError("invalid mailbox benchmark parameters")

    async def run_all() -> dict[str, Any]:
        echo = await _echo_overhead(echo_repeat)
        parallel = {
            workers: await _parallel_service(parallel_requests, workers, service_ms)
            for workers in (1, 4, 8, 16)
        }
        pressure = await _backpressure_probe(queue_size=8, rejected_attempts=16)
        return {
            "echo": echo,
            **{f"worker_{workers}": row for workers, row in parallel.items()},
            "backpressure": pressure,
        }

    results = asyncio.run(run_all())
    one_tput = results["worker_1"]["throughput_req_sec"]
    four_tput = results["worker_4"]["throughput_req_sec"]
    results["parallel_speedup_4_vs_1"] = four_tput / one_tput if one_tput else None
    for workers in (8, 16):
        throughput = results[f"worker_{workers}"]["throughput_req_sec"]
        results[f"parallel_speedup_{workers}_vs_1"] = throughput / one_tput if one_tput else None
    return {
        "suite": "mailbox",
        "env": _env_info(),
        "methodology": {
            "scope": "local asyncio scheduler microbenchmark; no LLM/network/model latency",
            "echo": "sequential no-op reply; direct await versus one-worker mailbox request",
            "parallel": f"synthetic {service_ms} ms asyncio sleep per request; 1 versus 4 workers",
            "backpressure": "one active worker + full bounded queue; reject overflow policy",
            "caution": "worker speedup applies to I/O-waiting handlers and is not a CPU/LLM scaling guarantee",
        },
        "results": results,
    }


def render_mailbox_report(payload: dict[str, Any]) -> str:
    r = payload["results"]
    lines = [
        "# Bounded Mailbox Scheduler Benchmark",
        "",
        f"- source SHA-256: {payload['env']['source_tree_sha256']}",
        "- Scope: scheduler microbenchmark, not an LLM throughput claim.",
        "",
        "## Fixed routing overhead",
        "",
        f"- direct median: {r['echo']['direct']['median_us']:.2f} us",
        f"- mailbox median: {r['echo']['router']['median_us']:.2f} us",
        f"- mailbox fixed overhead: {r['echo']['router_fixed_overhead_median_us']:.2f} us/request",
        "",
        "## I/O-bound concurrency",
        "",
        "| Workers | Requests | Service ms | Elapsed ms | Throughput req/s | Max queue depth |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key in ("worker_1", "worker_4", "worker_8", "worker_16"):
        case = r[key]
        lines.append(
            f"| {case['workers']} | {case['requests']} | {case['service_ms']:.2f} | "
            f"{case['elapsed_ms']:.2f} | {case['throughput_req_sec']:.1f} | {case['max_depth']} |"
        )
    lines += [
        "",
        f"- 4/8/16-worker throughput / 1-worker throughput: "
        f"{r['parallel_speedup_4_vs_1']:.2f}x / {r['parallel_speedup_8_vs_1']:.2f}x / "
        f"{r['parallel_speedup_16_vs_1']:.2f}x",
        f"- Saturation probe rejected: {r['backpressure']['rejected']}/{r['backpressure']['probe_attempts']} explicit overflow requests",
        "",
        "> The router deliberately adds bookkeeping/queue overhead. Its purpose is bounded concurrency, backpressure, deadlines and failure isolation; parallel speedup is workload-dependent.",
        "",
    ]
    return "\n".join(lines)


def write_mailbox_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "mailbox")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_mailbox_report(payload), encoding="utf-8")
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


def run_mailbox_benchmark_cli(args: Any) -> int:
    payload = run_mailbox_benchmark(
        echo_repeat=args.echo_repeat,
        parallel_requests=args.parallel_requests,
        service_ms=args.service_ms,
    )
    out = write_mailbox_results(payload)
    print(render_mailbox_report(payload))
    print(f"\nResults saved to: {out}")
    return 0


__all__ = [
    "run_mailbox_benchmark",
    "render_mailbox_report",
    "write_mailbox_results",
    "run_mailbox_benchmark_cli",
]
