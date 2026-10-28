"""OS-SOTA1 controlled experiment: LLM call coalescing on the contest workload.

Deterministic and offline.  The provider is ``mock-gateway``: the mock LLM plus the provider
billing law measured on the archived real-LLM rerun (4,382 hidden prompt tokens per request +
1.311 x runtime-visible tokens; latency 7.03 s/request + 0.043 s/completion token).  Every
number derived from that law is labelled *projected*; the request counts, logical request
counts, answers, success and parse misses are exact.

Arms (same tasks, same seeds, inbound LLM context as in ``external-llm``):

* ``T`` / ``T+C``        Lean Text without / with coalescing (fairness twin)
* ``SS`` / ``SS+C``      Structured + State without / with coalescing
* ``SSM`` / ``SSM+C``    SS + shared memory over ``rounds`` continuous rounds (fast-path reuse)

Checks: coalescing must not change any task answer or success (answer equivalence), must not
lose any logical request, and must parse every item.
"""

from __future__ import annotations

import asyncio
import json
import statistics
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from agent_runtime.config import load_config
from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest

ARMS = {
    "T": dict(mode="text", state=False, memory=False, coalesce="off"),
    "T+C": dict(mode="text", state=False, memory=False, coalesce="task"),
    "SS": dict(mode="structured", state=True, memory=False, coalesce="off"),
    "SS+C": dict(mode="structured", state=True, memory=False, coalesce="task"),
    "SSM": dict(mode="structured", state=True, memory=True, coalesce="off"),
    "SSM+C": dict(mode="structured", state=True, memory=True, coalesce="task"),
    # group commit: one shared gateway per round of continuous tasks
    "T+G": dict(mode="text", state=False, memory=False, coalesce="session"),
    "SS+G": dict(mode="structured", state=True, memory=False, coalesce="session"),
    "SSM+G": dict(mode="structured", state=True, memory=True, coalesce="session"),
}
KEYS = (
    "llm_calls", "llm_logical_requests", "llm_prompt_tokens", "llm_visible_prompt_tokens",
    "llm_completion_tokens", "llm_projected_latency_sec", "text_tokens", "llm_coalesce_parse_misses",
    "memory_fast_path_hits",
)


def _run_arm(arm: dict[str, Any], rounds: int, seed: int) -> dict[str, Any]:
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.protocol.routine import reset_routine_registry
    from agent_runtime.providers.factory import build_embedding
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator
    from agent_runtime.observability.metrics import MetricsCollector
    from agent_runtime.providers.factory import build_llm
    from agent_runtime.runtime.context import _COALESCED_HINTS
    from agent_runtime.runtime.llm_gateway import LLMGateway, flush_session

    reset_routine_registry()
    base = load_config(load_env=False)
    cfg = base.with_overrides(
        random_seed=seed,
        text_style="lean",
        llm_context_mode="inbound",
        llm_coalesce=arm["coalesce"],
        llm=replace(base.llm, provider="mock-gateway"),
    )
    memory = MemoryStore(embedding=build_embedding(cfg), vector_backend=cfg.vector_backend) if arm["memory"] else None
    totals = {k: 0.0 for k in KEYS}
    answers: list[Any] = []
    success = 0
    tasks_run = 0
    try:
        for r in range(rounds):
            gateway = (
                LLMGateway(build_llm(cfg), mode="session", contract_hint=_COALESCED_HINTS.get(cfg.llm.output_contract, ""))
                if arm["coalesce"] == "session" else None
            )
            for task in _self_contained_tasks():
                task = replace(task, task_id=f"{task.task_id}-r{r}")
                ctx = build_context(cfg, arm["mode"], task_id=task.task_id, use_memory=arm["memory"],
                                    use_state_exchange=arm["state"], use_sandbox=True, memory=memory,
                                    llm_gateway=gateway)
                res = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
                tasks_run += 1
                success += int(bool(res.success))
                answers.append(ctx.blackboard.get("answer"))
                for k in KEYS:
                    totals[k] += float(getattr(res.metrics, k, 0) or 0)
            if gateway is not None:
                session = MetricsCollector()
                flush_session(gateway, session)
                for k in KEYS:
                    totals[k] += float(getattr(session.snapshot, k, 0) or 0)
    finally:
        if memory is not None:
            memory.close()
    totals["billed_total_tokens"] = totals["llm_prompt_tokens"] + totals["llm_completion_tokens"]
    return {"tasks": tasks_run, "success": success, "answers": answers, **totals}


def run_coalesce_benchmark(*, repeat: int = 3, rounds: int = 10) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {a: [] for a in ARMS}
    for i in range(repeat):
        order = list(ARMS)
        order = order[i % len(order):] + order[: i % len(order)]
        for aid in order:
            arm_rounds = rounds if ARMS[aid]["memory"] else 1
            rows[aid].append(_run_arm(ARMS[aid], arm_rounds, 42 + i))
    summary: dict[str, Any] = {}
    for aid, rs in rows.items():
        summary[aid] = {
            "tasks": rs[0]["tasks"],
            "success_rate": sum(r["success"] for r in rs) / sum(r["tasks"] for r in rs),
            **{f"{k}_mean": statistics.fmean(r[k] for r in rs) for k in (*KEYS, "billed_total_tokens")},
        }
    checks = {
        "answers_identical_T": all(a["answers"] == b["answers"] for a, b in zip(rows["T"], rows["T+C"])),
        "answers_identical_SS": all(a["answers"] == b["answers"] for a, b in zip(rows["SS"], rows["SS+C"])),
        "answers_identical_SSM": all(a["answers"] == b["answers"] for a, b in zip(rows["SSM"], rows["SSM+C"])),
        "answers_identical_group": all(
            a["answers"] == b["answers"] for x, y in (("T", "T+G"), ("SS", "SS+G"), ("SSM", "SSM+G")) for a, b in zip(rows[x], rows[y])
        ),
        "logical_requests_preserved": all(
            summary[a]["llm_logical_requests_mean"] == summary[f"{a}+{m}"]["llm_logical_requests_mean"]
            for a in ("T", "SS", "SSM") for m in ("C", "G")
        ),
        "parse_misses": sum(summary[a]["llm_coalesce_parse_misses_mean"] for a in summary),
    }

    def saving(a: str, b: str, key: str = "billed_total_tokens_mean") -> float:
        return 1.0 - summary[a][key] / max(1e-9, summary[b][key])

    derived = {
        "SS_vs_T_billed": saving("SS", "T"),
        "SS+C_vs_T_billed": saving("SS+C", "T"),
        "SS+C_vs_T+C_billed": saving("SS+C", "T+C"),
        "T+C_vs_T_billed": saving("T+C", "T"),
        "SSM+C_vs_SSM_billed": saving("SSM+C", "SSM"),
        "SS+C_vs_T_latency": saving("SS+C", "T", "llm_projected_latency_sec_mean"),
        "SS+G_vs_T_billed": saving("SS+G", "T"),
        "SS+G_vs_T+G_billed": saving("SS+G", "T+G"),
        "SSM+G_vs_SSM_billed": saving("SSM+G", "SSM"),
        "SS+G_vs_T_latency": saving("SS+G", "T", "llm_projected_latency_sec_mean"),
        "SS_vs_T_communication_tokens": saving("SS", "T", "text_tokens_mean"),
    }
    return {
        "suite": "coalesce_benchmark",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "env": _env_info(),
        "repeat": repeat,
        "rounds_memory_arms": rounds,
        "evidence_class": "exact counts + PROJECTED billing/latency (calibrated on 72 real cells)",
        "summary": summary,
        "checks": checks,
        "derived": derived,
    }


def render_coalesce_benchmark(p: dict[str, Any]) -> str:
    s = p["summary"]
    lines = [
        "# OS-SOTA1 — LLM call coalescing on the contest workload",
        "",
        f"> repeat={p['repeat']}; T/SS arms = 4 contest tasks; SSM arms = {p['rounds_memory_arms']} continuous rounds × 4 tasks "
        "with shared memory. Provider = calibrated gateway projection (billing/latency are **projected**; "
        "request counts, answers and success are exact).",
        "",
        "| Arm | Tasks | Success | Provider requests | Logical requests | Comm. tokens | Billed prompt (proj.) | Completion | Billed total (proj.) | Latency s (proj.) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for aid, x in s.items():
        lines.append(
            f"| {aid} | {x['tasks']} | {x['success_rate']:.0%} | {x['llm_calls_mean']:.1f} | {x['llm_logical_requests_mean']:.1f} | "
            f"{x['text_tokens_mean']:,.0f} | {x['llm_prompt_tokens_mean']:,.0f} | {x['llm_completion_tokens_mean']:,.0f} | "
            f"{x['billed_total_tokens_mean']:,.0f} | {x['llm_projected_latency_sec_mean']:.1f} |"
        )
    d, c = p["derived"], p["checks"]
    lines += [
        "",
        f"- Communication-token saving SS vs T: **{d['SS_vs_T_communication_tokens']:.1%}** (unchanged by coalescing).",
        f"- Billed total-token saving: SS vs T **{d['SS_vs_T_billed']:.1%}** → SS+C vs T **{d['SS+C_vs_T_billed']:.1%}**; "
        f"fairness SS+C vs T+C **{d['SS+C_vs_T+C_billed']:.1%}**; coalescing alone on text T+C vs T {d['T+C_vs_T_billed']:.1%}.",
        f"- Continuous rounds with memory: SSM+C vs SSM **{d['SSM+C_vs_SSM_billed']:.1%}** billed tokens.",
        f"- Projected LLM latency SS+C vs T: **{d['SS+C_vs_T_latency']:.1%}**.",
        f"- **Group commit** (one shared gateway per round): SS+G vs T **{d['SS+G_vs_T_billed']:.1%}** billed tokens, "
        f"fairness SS+G vs T+G {d['SS+G_vs_T+G_billed']:.1%}; SSM+G vs SSM {d['SSM+G_vs_SSM_billed']:.1%}; "
        f"projected latency SS+G vs T {d['SS+G_vs_T_latency']:.1%}.",
        f"- Answer equivalence: group commit {c['answers_identical_group']}; T {c['answers_identical_T']}, SS {c['answers_identical_SS']}, SSM {c['answers_identical_SSM']}; "
        f"logical requests preserved: {c['logical_requests_preserved']}; parse misses: {c['parse_misses']:.0f}.",
        "",
        f"Source SHA-256: `{p['env']['source_tree_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def run_coalesce_benchmark_cli(args) -> int:
    p = run_coalesce_benchmark(repeat=getattr(args, "repeat", 3), rounds=getattr(args, "rounds", 10))
    out = _allocate_result_dir("results", "coalesce_benchmark")
    (out / "summary.json").write_text(json.dumps(p, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "report.md").write_text(render_coalesce_benchmark(p), encoding="utf-8")
    _write_checksum_manifest(out)
    print(render_coalesce_benchmark(p))
    print(f"\n结果已保存到: {out}")
    return 0 if all(v for k, v in p["checks"].items() if k.startswith("answers") or k == "logical_requests_preserved") else 1


__all__ = ["run_coalesce_benchmark", "render_coalesce_benchmark", "run_coalesce_benchmark_cli"]
