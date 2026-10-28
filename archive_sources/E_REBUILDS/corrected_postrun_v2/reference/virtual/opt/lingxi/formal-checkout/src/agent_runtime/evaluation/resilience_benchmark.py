"""Controlled failure-amplification benchmark for retry budget and circuit breaker."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from agent_runtime.config import load_config
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.runtime.agent import BaseAgent, RetryPolicy
from agent_runtime.runtime.builder import build_context
from agent_runtime.runtime.resilience import CircuitBreaker


class _FailingProbeAgent(BaseAgent):
    def __init__(self, *, breaker: CircuitBreaker | None = None) -> None:
        super().__init__(
            "probe",
            ["failure_probe"],
            actions=[ActionType.EXECUTE],
            retry=RetryPolicy(max_retries=5),
            circuit_breaker=breaker,
        )
        self.handle_calls = 0

    async def handle(self, message, ctx):
        self.handle_calls += 1
        raise RuntimeError("synthetic provider failure")


def _message() -> AgentMessage:
    return AgentMessage(
        trace_id="trace-resilience",
        task_id="task-resilience",
        step_id="s1",
        source_agent="orchestrator",
        target_agent="probe",
        action_type=ActionType.EXECUTE,
        status=MessageStatus.PENDING,
    )


def _run_case(case: str) -> dict[str, Any]:
    if case == "R0":
        cfg = load_config(load_env=False, task_retry_budget=None)
        breaker = None
    elif case == "R1":
        cfg = load_config(load_env=False, task_retry_budget=1)
        breaker = None
    elif case == "R2":
        cfg = load_config(load_env=False, task_retry_budget=None)
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_sec=60.0)
    else:
        raise ValueError(case)
    ctx = build_context(cfg, "structured", task_id="task-resilience")
    agent = _FailingProbeAgent(breaker=breaker)
    result = asyncio.run(agent.run(_message(), ctx))
    return {
        "handle_calls": agent.handle_calls,
        "retry_attempts": ctx.metrics.snapshot.retry_attempts,
        "retry_budget_exhausted": ctx.metrics.snapshot.retry_budget_exhausted,
        "circuit_rejections": ctx.metrics.snapshot.circuit_rejections,
        "error_count": ctx.metrics.snapshot.error_count,
        "degradation_count": ctx.metrics.snapshot.degradation_count,
        "terminal_error": result.error,
    }


def run_resilience_benchmark() -> dict[str, Any]:
    results = {case: _run_case(case) for case in ("R0", "R1", "R2")}
    base = results["R0"]["handle_calls"]
    for case in ("R1", "R2"):
        results[case]["failed_work_reduction_vs_R0"] = 1.0 - results[case]["handle_calls"] / base
    results["R0"]["failed_work_reduction_vs_R0"] = 0.0
    return {
        "suite": "resilience",
        "env": _env_info(),
        "methodology": {
            "scope": "synthetic always-failing agent; measures failure amplification only",
            "R0": "local RetryPolicy(max_retries=5), no shared budget/circuit",
            "R1": "same local policy + task retry budget=1",
            "R2": "same local policy + circuit opens after first failure",
            "boundary": "not a real-provider availability or recovery benchmark",
        },
        "results": results,
    }


def render_resilience_report(payload: dict[str, Any]) -> str:
    r = payload["results"]
    lines = [
        "# Retry Budget / Circuit Breaker Controlled Validation",
        "",
        "> Synthetic always-failing agent; this measures failure amplification, not provider availability.",
        "",
        f"- Source SHA-256: {payload['env']['source_tree_sha256']}",
        "",
        "| Case | handle() calls | retries | budget exhausted | reduction vs R0 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for case in ("R0", "R1", "R2"):
        x = r[case]
        lines.append(
            f"| {case} | {x['handle_calls']} | {x['retry_attempts']} | "
            f"{x['retry_budget_exhausted']} | {x['failed_work_reduction_vs_R0']:.1%} |"
        )
    lines += [
        "",
        "R2 opens the circuit on the first failure, so the failed handler is not retried. "
        "R1 permits exactly one task-budgeted retry even though the agent-local policy permits five.",
        "",
    ]
    return "\n".join(lines)


def write_resilience_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "resilience")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_resilience_report(payload), encoding="utf-8")
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


def run_resilience_benchmark_cli(args) -> int:
    payload = run_resilience_benchmark()
    out = write_resilience_results(payload)
    print(out)
    return 0


__all__ = ["run_resilience_benchmark", "write_resilience_results", "run_resilience_benchmark_cli"]
