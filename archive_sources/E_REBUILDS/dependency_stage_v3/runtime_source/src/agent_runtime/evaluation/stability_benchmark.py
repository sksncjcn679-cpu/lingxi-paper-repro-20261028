"""Formal >=10-round stability evidence required by the competition task."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import statistics
from typing import Any

import psutil

from agent_runtime.config import load_config
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task


def run_stability_benchmark(rounds: int = 10) -> dict[str, Any]:
    if rounds < 10:
        raise ValueError("competition stability evidence requires rounds >= 10")
    cfg = load_config(load_env=False)
    memory = MemoryStore(embedding=build_embedding(cfg))
    proc = psutil.Process()
    runs: list[dict[str, Any]] = []
    for i in range(rounds):
        ctx = build_context(
            cfg,
            "structured",
            task_id=f"competition-stability-{i:02d}",
            use_memory=True,
            use_state_exchange=True,
            use_sandbox=True,
            memory=memory,
        )
        result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(make_demo_task(), ctx))
        gc.collect()
        rss = proc.memory_info().rss / (1024 * 1024)
        runs.append(
            {
                "round": i + 1,
                "success": result.success,
                "latency_sec": result.metrics.latency_sec,
                "rss_mb": rss,
                "messages": result.metrics.message_count,
                "state_transfers": result.metrics.state_transfers,
                "memory_retrieved": result.metrics.memory_retrieved,
                "memory_used": result.metrics.memory_used,
                "memory_effective": result.metrics.memory_effective,
                "errors": result.metrics.error_count,
            }
        )
    memory.close()
    lat = [r["latency_sec"] for r in runs]
    rss = [r["rss_mb"] for r in runs]
    success_count = sum(int(r["success"]) for r in runs)
    return {
        "suite": "stability",
        "env": _env_info(),
        "rounds": rounds,
        "runs": runs,
        "summary": {
            "success_count": success_count,
            "success_rate": success_count / rounds,
            "latency_mean_sec": statistics.fmean(lat),
            "latency_p95_sec": statistics.quantiles(lat, n=100, method="inclusive")[94],
            "rss_first_mb": rss[0],
            "rss_last_mb": rss[-1],
            "rss_growth_mb": rss[-1] - rss[0],
            "error_count": sum(r["errors"] for r in runs),
            "total_state_transfers": sum(r["state_transfers"] for r in runs),
            "total_memory_used": sum(r["memory_used"] for r in runs),
            "total_memory_effective": sum(r["memory_effective"] for r in runs),
        },
    }


def render_stability_report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        "# >=10 Round Stability Evidence",
        "",
        f"- Rounds: **{payload['rounds']}**",
        f"- Success: **{s['success_count']}/{payload['rounds']} ({s['success_rate']:.0%})**",
        f"- Errors: **{s['error_count']}**",
        f"- Mean latency: **{s['latency_mean_sec']:.4f}s**; P95: **{s['latency_p95_sec']:.4f}s**",
        f"- RSS growth first→last: **{s['rss_growth_mb']:+.2f} MB**",
        f"- Non-text state transfers: **{s['total_state_transfers']}**",
        f"- Memory used/effective: **{s['total_memory_used']}/{s['total_memory_effective']}**",
        "",
        "| Round | Success | Latency(s) | RSS(MB) | Messages | State | Memory used/effective | Errors |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in payload["runs"]:
        lines.append(
            f"| {r['round']} | {'PASS' if r['success'] else 'FAIL'} | {r['latency_sec']:.4f} | {r['rss_mb']:.1f} | "
            f"{r['messages']} | {r['state_transfers']} | {r['memory_used']}/{r['memory_effective']} | {r['errors']} |"
        )
    lines += ["", f"Source SHA-256: `{payload['env']['source_tree_sha256']}`", ""]
    return "\n".join(lines)


def write_stability_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "stability")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_stability_report(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "suite": payload["suite"],
                "rounds": payload["rounds"],
                "environment": payload["env"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    return out


def run_stability_cli(args) -> int:
    payload = run_stability_benchmark(rounds=args.rounds)
    out = write_stability_results(payload)
    print(render_stability_report(payload))
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["run_stability_benchmark", "write_stability_results", "run_stability_cli"]
