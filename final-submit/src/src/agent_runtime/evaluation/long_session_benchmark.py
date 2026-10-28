"""OS-SOTA1.1: long collaboration session — real multi-round bill + large-batch group commit.

``rounds`` rounds × the 4 contest tasks, with one arm per row:

* ``T``      Lean Text, direct calls (the traditional baseline: 4 requests per task)
* ``T+G``    Lean Text, one group-commit gateway for the whole session (fairness twin)
* ``SS+G``   Structured+State, one gateway for the session → 16·rounds items → windows of 16:
             this is the *large-batch* regime where batch prompting may drop items, so miss
             repair + the AIMD window are exercised on the real model
* ``FULL+G`` SS + shared memory + concise output + group commit (later rounds reuse memory)

Works with the real provider (``AGENT_LLM_PROVIDER=openai``) or, for offline checks, with
``--allow-mock`` (``mock`` or the calibrated ``mock-gateway`` projection).  Answers are compared
against the direct-call arm; every logical request must be answered (repairs included).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from agent_runtime.config import load_config
from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest

ARMS: dict[str, dict[str, Any]] = {
    "T": dict(mode="text", state=False, memory=False, coalesce="off", contract="none"),
    "T+G": dict(mode="text", state=False, memory=False, coalesce="session", contract="none"),
    "SS+G": dict(mode="structured", state=True, memory=False, coalesce="session", contract="none"),
    # OS-SOTA1.6 capacity probe: the whole session in one request when the model can take it
    # (init window 64; real 1.5 data: 16-item batches had 0 misses).  Text twin for fairness.
    "T+G64": dict(mode="text", state=False, memory=False, coalesce="session", contract="none", init_window=64),
    "SS+G64": dict(mode="structured", state=True, memory=False, coalesce="session", contract="none", init_window=64),
    "FULL": dict(mode="structured", state=True, memory=True, coalesce="off", contract="concise"),
    "FULL+G": dict(mode="structured", state=True, memory=True, coalesce="session", contract="concise"),
}
KEYS = ("llm_calls", "llm_logical_requests", "llm_prompt_tokens", "llm_completion_tokens",
        "llm_reasoning_tokens", "llm_coalesce_repairs", "llm_coalesce_parse_misses", "text_tokens",
        "llm_projected_latency_sec", "memory_fast_path_hits")


def _run_arm(arm: dict[str, Any], rounds: int, seed: int, base_cfg) -> dict[str, Any]:
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.observability.metrics import MetricsCollector
    from agent_runtime.protocol.routine import reset_routine_registry
    from agent_runtime.providers.factory import build_embedding, build_llm
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.context import _COALESCED_HINTS
    from agent_runtime.runtime.llm_gateway import LLMGateway, flush_session
    from agent_runtime.runtime.orchestrator import Orchestrator

    from agent_runtime.runtime.llm_gateway import reset_batch_windows

    reset_routine_registry()
    reset_batch_windows()  # every arm starts from the same window state (no cross-arm learning)
    cfg = base_cfg.with_overrides(
        random_seed=seed, text_style="lean", llm_context_mode="inbound", llm_coalesce=arm["coalesce"],
        llm=replace(base_cfg.llm, output_contract=arm["contract"],
                    max_tokens=96 if arm["contract"] == "concise" else base_cfg.llm.max_tokens),
    )
    memory = MemoryStore(embedding=build_embedding(cfg), vector_backend=cfg.vector_backend) if arm["memory"] else None
    gateway = (LLMGateway(build_llm(cfg), mode="session", contract_hint=_COALESCED_HINTS.get(cfg.llm.output_contract, ""),
                          init_window=arm.get("init_window"), max_batch=max(64, arm.get("init_window") or 0))
               if arm["coalesce"] == "session" else None)
    totals = {k: 0.0 for k in KEYS}
    answers, success, n = [], 0, 0
    t0 = time.perf_counter()
    try:
        for r in range(rounds):
            for task in _self_contained_tasks():
                task = replace(task, task_id=f"{task.task_id}-r{r}")
                ctx = build_context(cfg, arm["mode"], task_id=task.task_id, use_memory=arm["memory"],
                                    use_state_exchange=arm["state"], use_sandbox=True, memory=memory, llm_gateway=gateway)
                res = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
                n += 1
                success += int(bool(res.success))
                answers.append(ctx.blackboard.get("answer"))
                for k in KEYS:
                    totals[k] += float(getattr(res.metrics, k, 0) or 0)
        summary = None
        if gateway is not None:
            session = MetricsCollector()
            flush_session(gateway, session)
            for k in KEYS:
                totals[k] += float(getattr(session.snapshot, k, 0) or 0)
            summary = gateway.summary()
            unanswered = sum(1 for t in range(1, gateway._next_ticket) if not gateway.results.get(t))
        else:
            unanswered = 0
    finally:
        if memory is not None:
            memory.close()
    totals["billed_total_tokens"] = totals["llm_prompt_tokens"] + totals["llm_completion_tokens"]
    # LLM wall time: direct arms are sequential (sum); group-committed arms overlap batches.
    llm_wall = (summary or {}).get("projected_wall_sec", totals["llm_projected_latency_sec"]) if summary else totals["llm_projected_latency_sec"]
    llm_wall_measured = (summary or {}).get("wall_sec")
    return {"tasks": n, "success": success, "answers": answers, "wall_sec": time.perf_counter() - t0,
            "llm_wall_projected_sec": llm_wall, "llm_wall_measured_sec": llm_wall_measured,
            "unanswered_logical_requests": unanswered, "gateway": summary, **totals}


def run_long_session(*, rounds: int = 3, repeat: int = 1, allow_mock: bool = False, arms: list[str] | None = None) -> dict[str, Any]:
    from agent_runtime.runtime.llm_gateway import reset_batch_windows

    base = load_config(load_env=True)
    if base.llm.provider.lower().startswith("mock") and not allow_mock:
        raise SystemExit("long-session needs a real provider (or --allow-mock for scaffolding checks)")
    reset_batch_windows()
    selected = arms or list(ARMS)
    rows: dict[str, list[dict[str, Any]]] = {a: [] for a in selected}
    for i in range(repeat):
        order = selected[i % len(selected):] + selected[: i % len(selected)]
        for aid in order:
            rows[aid].append(_run_arm(ARMS[aid], rounds, base.random_seed + i, base))
    summary = {}
    for aid, rs in rows.items():
        summary[aid] = {"tasks": rs[0]["tasks"], "success_rate": sum(r["success"] for r in rs) / sum(r["tasks"] for r in rs),
                        "unanswered_logical_requests": sum(r["unanswered_logical_requests"] for r in rs),
                        "wall_sec_mean": statistics.fmean(r["wall_sec"] for r in rs),
                        "llm_wall_projected_sec_mean": statistics.fmean(float(r["llm_wall_projected_sec"] or 0) for r in rs),
                        "gateway_last": rs[-1]["gateway"],
                        **{f"{k}_mean": statistics.fmean(r[k] for r in rs) for k in (*KEYS, "billed_total_tokens")}}
    base_t = summary.get("T")
    for aid, x in summary.items():
        if base_t and aid != "T":
            x["billed_saving_vs_T"] = 1 - x["billed_total_tokens_mean"] / max(1e-9, base_t["billed_total_tokens_mean"])
    for ss, tt in (("SS+G", "T+G"), ("SS+G64", "T+G64")):
        if ss in summary and tt in summary:
            summary[ss][f"billed_saving_vs_{tt}"] = 1 - summary[ss]["billed_total_tokens_mean"] / max(1e-9, summary[tt]["billed_total_tokens_mean"])
    checks = {}
    if "T" in rows:
        for aid in rows:
            if aid in ARMS and aid != "T" and not ARMS[aid]["memory"]:
                checks[f"answers_identical_{aid}"] = all(a["answers"] == b["answers"] for a, b in zip(rows["T"], rows[aid]))
    checks["no_unanswered_logical_requests"] = all(x["unanswered_logical_requests"] == 0 for x in summary.values())
    return {
        "suite": "long_session",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "env": _env_info(),
        "provider": {"llm_provider": base.llm.provider, "llm_model": base.llm.model},
        "rounds": rounds, "repeat": repeat,
        "evidence_class": "provider-billed" if not base.llm.provider.lower().startswith("mock")
        else ("PROJECTION (calibrated gateway)" if base.llm.provider.lower() == "mock-gateway" else "scaffold (mock)"),
        "summary": summary,
        "checks": checks,
    }


def render_long_session(p: dict[str, Any]) -> str:
    lines = [
        f"# Long collaboration session — {p['rounds']} rounds × 4 tasks ({p['evidence_class']})",
        "",
        f"> provider={p['provider']['llm_provider']}; model={p['provider']['llm_model']}; repeat={p['repeat']}",
        "",
        "| Arm | Tasks | Success | Provider requests | Logical requests | Repairs | Comm. tokens | Billed prompt | Completion (reasoning) | Billed total | Saving vs T | Final window | LLM wall s (proj.) | Run wall s |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for aid, x in p["summary"].items():
        g = x.get("gateway_last") or {}
        lines.append(
            f"| {aid} | {x['tasks']} | {x['success_rate']:.0%} | {x['llm_calls_mean']:.1f} | {x['llm_logical_requests_mean']:.1f} | "
            f"{x['llm_coalesce_repairs_mean']:.1f} | {x['text_tokens_mean']:,.0f} | {x['llm_prompt_tokens_mean']:,.0f} | "
            f"{x['llm_completion_tokens_mean']:,.0f} ({x['llm_reasoning_tokens_mean']:,.0f}) | {x['billed_total_tokens_mean']:,.0f} | "
            f"{(x.get('billed_saving_vs_T') or 0):.1%} | {g.get('window', '-')} | {x['llm_wall_projected_sec_mean']:.1f} | {x['wall_sec_mean']:.1f} |"
        )
    lines.append("")
    for ss, tt in (("SS+G", "T+G"), ("SS+G64", "T+G64")):
        x = p["summary"].get(ss, {})
        if f"billed_saving_vs_{tt}" in x:
            lines.append(f"- Fairness: {ss} vs equally group-committed text {tt}: **{x[f'billed_saving_vs_{tt}']:.1%}**.")
    lines += [f"- Checks: {p['checks']}", "", f"Source SHA-256: `{p['env']['source_tree_sha256']}`", ""]
    return "\n".join(lines)


def run_long_session_cli(args) -> int:
    arms = [a.strip() for a in (getattr(args, "arms", "") or "").split(",") if a.strip()] or None
    p = run_long_session(rounds=args.rounds, repeat=args.repeat, allow_mock=args.allow_mock, arms=arms)
    out = _allocate_result_dir("results", "long_session")
    (out / "summary.json").write_text(json.dumps(p, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "report.md").write_text(render_long_session(p), encoding="utf-8")
    _write_checksum_manifest(out)
    print(render_long_session(p))
    print(f"\n结果已保存到: {out}")
    return 0 if all(p["checks"].values()) and all(x["success_rate"] == 1.0 for x in p["summary"].values()) else 1


__all__ = ["run_long_session", "render_long_session", "run_long_session_cli"]
