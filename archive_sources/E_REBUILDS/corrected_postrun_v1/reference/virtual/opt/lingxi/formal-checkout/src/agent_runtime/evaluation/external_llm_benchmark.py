"""Optional real-LLM external-validity benchmark.

The deterministic mock-first suites remain the primary causal/runtime evidence.  This module is
an *additional* bridge to a real OpenAI-compatible LLM so a submission can demonstrate that the
same communication/state mechanisms execute with real provider accounting.  It deliberately
refuses the mock provider by default to prevent a mock run from being mislabeled as real-model
evidence.

Embedding may remain mock: this benchmark is specifically about the LLM boundary and runtime
communication path.  A team may set AGENT_EMBEDDING_PROVIDER=openai separately if desired.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import statistics
from typing import Any

from agent_runtime.config import load_config
from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.memory.store import MemoryStore
from agent_runtime.evaluation.stats import paired_analysis, paired_ci95, paired_effect_size
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.linux_service import make_group as make_service_group
from agent_runtime.scenarios.openeuler_migration import make_group as make_migration_group


def _tasks():
    # One task from each official-style OS group keeps API cost bounded while preserving
    # heterogeneous task semantics.
    return [
        make_service_group(self_contained=True)[0],
        make_migration_group(self_contained=True)[0],
    ]


def _memory_tasks():
    """Two equivalent self-contained tasks for a fair memory off/on pair."""
    first = make_service_group(self_contained=True)[0]
    return [first, replace(first, task_id=f"{first.task_id}-repeat", parent_task_id=first.task_id)]


def _related_tasks(linked: bool):
    """OS-SOTA1.1: a *related but different* follow-up (A1 -> A2, another unit, same fault
    family).  The text baseline gets the self-contained A2 (procedure inline, as any text
    agent would need); memory arms get the linked A2, which omits the procedure and must
    recover it from verified shared memory -- memory reuse on non-identical tasks."""
    group = make_service_group(self_contained=not linked)
    return [group[0], group[1]]


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    return float(statistics.fmean(float(row[key]) for row in rows)) if rows else 0.0


def _stats(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    # Rows from crashed/partial cells may lack metrics; never let one bad cell break the report.
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    if not values:
        return {"mean": 0.0, "std": 0.0, "ci95_low": 0.0, "ci95_high": 0.0}
    mean = float(statistics.fmean(values))
    if len(values) < 2:
        return {"mean": mean, "std": 0.0, "ci95_low": None, "ci95_high": None}
    std = float(statistics.stdev(values))
    # Student-t critical values for the small-repeat external benchmark.
    critical = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}.get(len(values) - 1, 1.96)
    half = critical * std / (len(values) ** 0.5)
    return {"mean": mean, "std": std, "ci95_low": mean - half, "ci95_high": mean + half}


def run_external_llm_benchmark(
    *,
    repeat: int = 1,
    allow_mock: bool = False,
    variants: list[str] | None = None,
    checkpoint: str | Path | None = None,
    budget_sec: float | None = None,
    order: str = "balanced",
    progress: Any = None,
) -> dict[str, Any]:
    import os as _os
    _prev_ctx = _os.environ.get("AGENT_LLM_CONTEXT")
    _os.environ["AGENT_LLM_CONTEXT"] = "inbound"
    try:
        return _run_external_llm_benchmark(
            repeat=repeat, allow_mock=allow_mock, only=variants, checkpoint=checkpoint,
            budget_sec=budget_sec, order=order, progress=progress,
        )
    finally:
        if _prev_ctx is None:
            _os.environ.pop("AGENT_LLM_CONTEXT", None)
        else:
            _os.environ["AGENT_LLM_CONTEXT"] = _prev_ctx


def _variant_table(base_cfg) -> tuple[dict[str, dict[str, Any]], int]:
    # Preserve the historical 1024-token real-LLM baseline when the user-level
    # setting is intentionally unbounded.  The benchmark must remain finite and
    # comparable; SS_concise is the only variant that changes this cap.
    benchmark_max_tokens = base_cfg.llm.max_tokens if base_cfg.llm.max_tokens is not None else 1024
    variants = {
        "T": dict(mode="text", use_state=False, use_memory=False, text_style="lean", structured_wire_codec=base_cfg.structured_wire_codec, max_tokens=benchmark_max_tokens),
        "T_memory_pair": dict(mode="text", use_state=False, use_memory=False, memory_pair=True, text_style="lean", structured_wire_codec=base_cfg.structured_wire_codec, max_tokens=benchmark_max_tokens),
        "S": dict(mode="structured", use_state=False, use_memory=False, text_style=base_cfg.text_style, structured_wire_codec="session_msgpack", max_tokens=benchmark_max_tokens),
        "SS": dict(mode="structured", use_state=True, use_memory=False, text_style=base_cfg.text_style, structured_wire_codec="session_msgpack", max_tokens=benchmark_max_tokens),
        "SS_memory_off": dict(mode="structured", use_state=True, use_memory=False, memory_pair=True, text_style=base_cfg.text_style, structured_wire_codec="session_msgpack", max_tokens=benchmark_max_tokens),
        "SS_memory_on": dict(mode="structured", use_state=True, use_memory=True, memory_pair=True, text_style=base_cfg.text_style, structured_wire_codec="session_msgpack", max_tokens=benchmark_max_tokens),
        "SS_concise": dict(
            mode="structured",
            use_state=True,
            use_memory=False,
            text_style=base_cfg.text_style,
            structured_wire_codec="session_msgpack",
            output_contract="concise",
            max_tokens=96,
        ),
        "FULL": dict(
            mode="structured",
            use_state=True,
            use_memory=True,
            memory_pair=True,
            pipeline_topology="forward",
            text_style=base_cfg.text_style,
            structured_wire_codec="session_msgpack",
            output_contract="concise",
            max_tokens=96,
        ),
    }
    for spec in variants.values():
        spec.setdefault("coalesce", "off")
    # sota1: LLM call coalescing arms.  Each coalesced arm has a coalesced *text* twin so the
    # effect of batching (orthogonal to the protocol) is reported separately from the protocol.
    variants["T_coalesce"] = {**variants["T"], "coalesce": "task"}
    variants["T_memory_pair_coalesce"] = {**variants["T_memory_pair"], "coalesce": "task"}
    variants["SS_coalesce"] = {**variants["SS"], "coalesce": "task"}
    variants["FULL_coalesce"] = {**variants["FULL"], "coalesce": "task"}
    # OS-SOTA1 group commit: one shared gateway for the variant's whole task sequence.
    variants["T_group"] = {**variants["T"], "coalesce": "session"}
    variants["SS_group"] = {**variants["SS"], "coalesce": "session"}
    variants["FULL_group"] = {**variants["FULL"], "coalesce": "session"}
    # OS-SOTA1.1 related-task memory arms (non-identical follow-up).
    variants["T_related"] = {**variants["T"], "task_set": "related_text"}
    variants["FULL_related"] = {**variants["FULL"], "task_set": "related_linked", "memory_pair": False}
    variants["T_related_group"] = {**variants["T_related"], "coalesce": "session"}
    variants["FULL_related_group"] = {**variants["FULL_related"], "coalesce": "session"}
    # OS-SOTA1.5 semantic LLM view (plumbing fields stripped from what the model sees), with
    # a text twin under the same projection.
    variants["T_group_sv"] = {**variants["T_group"], "llm_view": "semantic"}
    variants["SS_group_sv"] = {**variants["SS_group"], "llm_view": "semantic"}
    variants["FULL_group_sv"] = {**variants["FULL_group"], "llm_view": "semantic"}
    return variants, benchmark_max_tokens


def _run_variant_once(base_cfg, variant: dict[str, Any], i: int, kernel_transport: str) -> dict[str, Any]:
    from agent_runtime.protocol.routine import reset_routine_registry

    reset_routine_registry()  # cold start per cell: routine learning/upload cost is included
    cfg = base_cfg.with_overrides(
        random_seed=base_cfg.random_seed + i,
        text_style=variant["text_style"],
        structured_wire_codec=variant["structured_wire_codec"],
        control_transport=kernel_transport,
        pipeline_topology=variant.get("pipeline_topology", base_cfg.pipeline_topology),
        state_shared_memory=bool(variant["use_state"] and hasattr(socket, "AF_UNIX")),
        state_precision="adaptive",
        llm_coalesce=variant.get("coalesce", "off"),
        llm_view=variant.get("llm_view", "wire"),
        llm_judge=__import__("os").environ.get("AGENT_LLM_JUDGE", "").strip() == "1" or base_cfg.llm_judge,
        llm=replace(
            base_cfg.llm,
            output_contract=variant.get("output_contract", base_cfg.llm.output_contract),
            max_tokens=variant.get("max_tokens", base_cfg.llm.max_tokens),
        ),
    )
    task_set = variant.get("task_set")
    if task_set == "related_text":
        task_list = _related_tasks(linked=False)
    elif task_set == "related_linked":
        task_list = _related_tasks(linked=True)
    else:
        task_list = _memory_tasks() if variant.get("memory_pair") else _tasks()
    shared_memory = (
        MemoryStore(embedding=build_embedding(cfg), vector_backend=cfg.vector_backend)
        if variant.get("use_memory")
        else None
    )
    snapshots = []
    task_errors: list[dict[str, Any]] = []
    session_gateway = None
    if variant.get("coalesce") == "session":
        from agent_runtime.providers.factory import build_llm
        from agent_runtime.runtime.context import _COALESCED_HINTS
        from agent_runtime.runtime.llm_gateway import LLMGateway

        session_gateway = LLMGateway(
            build_llm(cfg), mode="session", contract_hint=_COALESCED_HINTS.get(cfg.llm.output_contract, "")
        )
    try:
        for task in task_list:
            ctx = build_context(
                cfg,
                variant["mode"],
                task_id=task.task_id,
                use_memory=bool(variant.get("use_memory")),
                use_state_exchange=bool(variant["use_state"]),
                use_sandbox=True,
                memory=shared_memory,
                llm_gateway=session_gateway,
            )
            result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
            snapshots.append(result.metrics)
            if not result.success:
                task_errors.append({"task_id": result.task_id, "error": result.error})
        if session_gateway is not None:
            from agent_runtime.observability.metrics import MetricsCollector
            from agent_runtime.runtime.llm_gateway import flush_session

            session = MetricsCollector()
            t0 = __import__("time").perf_counter()
            flush_session(session_gateway, session)
            session.snapshot.latency_sec = __import__("time").perf_counter() - t0
            session.snapshot.task_success = True  # carries only LLM usage, no task
            snapshots.append(session.snapshot)
    finally:
        if shared_memory is not None:
            shared_memory.close()
    row = {
        "success": all(s.task_success for s in snapshots),
        "messages": sum(s.message_count for s in snapshots),
        "communication_tokens": sum(s.text_tokens for s in snapshots),
        "wire_bytes": sum(s.wire_bytes for s in snapshots),
        "kernel_control_bytes": sum(
            (s.ipc_framed_bytes + s.ipc_ack_bytes) if s.ipc_frames else s.wire_bytes for s in snapshots
        ),
        "state_bytes": sum(s.state_bytes for s in snapshots),
        "state_logical_bytes": sum(s.state_logical_bytes for s in snapshots),
        "artifact_bytes": sum(s.artifact_bytes for s in snapshots),
        "llm_calls": sum(s.llm_calls for s in snapshots),
        "llm_prompt_tokens": sum(s.llm_prompt_tokens for s in snapshots),
        "llm_completion_tokens": sum(s.llm_completion_tokens for s in snapshots),
        "llm_cached_prompt_tokens": sum(s.llm_cached_prompt_tokens for s in snapshots),
        "llm_judgements": sum(s.llm_judgements for s in snapshots),
        "llm_judgements_correct": sum(s.llm_judgements_correct for s in snapshots),
        "total_llm_tokens": sum(s.llm_prompt_tokens + s.llm_completion_tokens for s in snapshots),
        "latency_sec": sum(s.latency_sec for s in snapshots),
        "ipc_frames": sum(s.ipc_frames for s in snapshots),
        "state_shm_transfers": sum(s.state_shm_transfers for s in snapshots),
        "state_int8_transfers": sum(s.state_int8_transfers for s in snapshots),
        "memory_queries": sum(s.memory_queries for s in snapshots),
        "memory_used": sum(s.memory_used for s in snapshots),
        "memory_effective": sum(s.memory_effective for s in snapshots),
        "memory_harmful": sum(s.memory_harmful for s in snapshots),
        "memory_fast_path_hits": sum(s.memory_fast_path_hits for s in snapshots),
        "llm_logical_requests": sum(s.llm_logical_requests for s in snapshots),
        "llm_visible_prompt_tokens": sum(s.llm_visible_prompt_tokens for s in snapshots),
        "llm_reasoning_tokens": sum(s.llm_reasoning_tokens for s in snapshots),
        "llm_cap_violations": sum(s.llm_cap_violations for s in snapshots),
        "llm_coalesced_batches": sum(s.llm_coalesced_batches for s in snapshots),
        "llm_coalesce_parse_misses": sum(s.llm_coalesce_parse_misses for s in snapshots),
        "llm_projected_latency_sec": sum(s.llm_projected_latency_sec for s in snapshots),
        "errors": task_errors,
    }
    row["strict_transport_bytes"] = row["kernel_control_bytes"] + row["state_bytes"] + row["artifact_bytes"]
    return row


def run_plan(variant_ids: list[str], repeat: int, order: str = "balanced") -> list[tuple[int, str]]:
    """Execution order of (repeat, variant) cells.

    ``fixed`` reproduces the historical order (T always first), which confounds variants with
    provider drift inside a repeat (warm caches, rate-limit windows, time-of-day load).
    ``balanced`` rotates the order each repeat (cyclic Latin square), so every variant
    occupies every position equally often once ``repeat`` is a multiple of the variant count.
    """
    if order not in {"balanced", "fixed"}:
        raise ValueError("order must be 'balanced' or 'fixed'")
    plan: list[tuple[int, str]] = []
    n = len(variant_ids)
    for i in range(repeat):
        shift = (i % n) if (order == "balanced" and n) else 0
        rotated = variant_ids[shift:] + variant_ids[:shift]
        plan.extend((i, cid) for cid in rotated)
    return plan


class _Checkpoint:
    """Append-only JSONL log of finished (repeat, variant) cells, fsync'ed per row.

    The first line is a header binding the log to provider/model/source; resuming a log produced
    by a different configuration is refused rather than silently mixing evidence.
    """

    def __init__(self, path: Path, header: dict[str, Any]) -> None:
        self.path = path
        self.rows: dict[tuple[int, str], dict[str, Any]] = {}
        if path.exists() and path.stat().st_size > 0:
            lines = path.read_text(encoding="utf-8").splitlines()
            old = json.loads(lines[0])
            if old.get("header") != header:
                raise RuntimeError(
                    f"checkpoint {path} was produced by a different provider/model/source; "
                    "use a new --checkpoint path"
                )
            for line in lines[1:]:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    break  # torn final line after a crash: ignore it, the cell reruns
                self.rows[(int(item["repeat"]), str(item["variant"]))] = item["row"]
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps({"header": header}, ensure_ascii=False) + "\n")

    def append(self, i: int, cid: str, row: dict[str, Any]) -> None:
        import os as _os

        self.rows[(i, cid)] = row
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"repeat": i, "variant": cid, "row": row}, ensure_ascii=False) + "\n")
            fh.flush()
            _os.fsync(fh.fileno())


def _run_external_llm_benchmark(
    *,
    repeat: int = 1,
    allow_mock: bool = False,
    only: list[str] | None = None,
    checkpoint: str | Path | None = None,
    budget_sec: float | None = None,
    order: str = "balanced",
    progress: Any = None,
) -> dict[str, Any]:
    import time as _time

    if repeat <= 0:
        raise ValueError("repeat must be positive")
    if budget_sec is not None and not (budget_sec > 0):
        raise ValueError("budget_sec must be positive")
    base_cfg = load_config(load_env=True)
    if base_cfg.llm.provider.lower().startswith("mock") and not allow_mock:
        raise RuntimeError(
            "external-llm benchmark requires AGENT_LLM_PROVIDER=openai (or another future real "
            "provider); pass --allow-mock only for a harness smoke test"
        )
    kernel_transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    all_variants, benchmark_max_tokens = _variant_table(base_cfg)
    if only:
        unknown = sorted(set(only) - set(all_variants))
        if unknown:
            raise ValueError(f"unknown variants: {unknown}; expected subset of {sorted(all_variants)}")
        variants = {k: v for k, v in all_variants.items() if k in set(only)}
    else:
        variants = all_variants
    env = _env_info(tokenizer_backend=base_cfg.tokenizer_backend)
    header = {
        "llm_provider": base_cfg.llm.provider,
        "llm_model": base_cfg.llm.model,
        "base_url": base_cfg.llm.base_url,
        "source_tree_sha256": env.get("source_tree_sha256"),
    }
    ckpt = _Checkpoint(Path(checkpoint), header) if checkpoint else None
    plan = run_plan(list(variants), repeat, order)
    raw: dict[str, list[dict[str, Any]]] = {key: [] for key in variants}
    say = progress or (lambda _msg: None)
    started = _time.monotonic()
    durations: dict[str, list[float]] = {}
    executed: list[dict[str, Any]] = []
    stopped_reason: str | None = None
    resumed = 0
    for pos, (i, cid) in enumerate(plan, 1):
        if ckpt is not None and (i, cid) in ckpt.rows:
            row = ckpt.rows[(i, cid)]
            resumed += 1
            say(f"[{pos}/{len(plan)}] repeat={i} {cid}: resumed from checkpoint")
        else:
            elapsed = _time.monotonic() - started
            history = durations.get(cid) or [d for v in durations.values() for d in v]
            expected = statistics.median(history) if history else 0.0
            if budget_sec is not None and elapsed + expected > budget_sec:
                stopped_reason = (
                    f"wall-clock budget {budget_sec:.0f}s reached after {elapsed:.0f}s "
                    f"(next cell expected {expected:.0f}s)"
                )
                say(f"STOP: {stopped_reason}")
                break
            t0 = _time.monotonic()
            try:
                row = _run_variant_once(base_cfg, variants[cid], i, kernel_transport)
            except Exception as exc:  # noqa: BLE001 - one failed cell must not lose the run
                row = {"success": False, "errors": [{"error": f"{type(exc).__name__}: {exc}"}], "crashed": True}
            took = _time.monotonic() - t0
            durations.setdefault(cid, []).append(took)
            row["wall_sec"] = round(took, 3)
            if ckpt is not None:
                ckpt.append(i, cid, row)
            done = sum(len(v) for v in durations.values())
            remaining = len(plan) - pos
            eta = statistics.median([d for v in durations.values() for d in v]) * remaining if done else 0.0
            say(
                f"[{pos}/{len(plan)}] repeat={i} {cid}: success={row.get('success')} "
                f"tokens={row.get('total_llm_tokens', 'n/a')} took={took:.1f}s eta~{eta/60:.1f}min"
            )
        row = dict(row)
        row["repeat"] = i
        raw[cid].append(row)
        executed.append({"repeat": i, "variant": cid})
    complete = stopped_reason is None
    summary = _summarize(raw)
    memory_comparison = None
    if "SS_memory_off" in summary and "SS_memory_on" in summary:
        memory_comparison = {
            "off_success_rate": summary["SS_memory_off"]["success_rate"],
            "on_success_rate": summary["SS_memory_on"]["success_rate"],
            "both_successful": summary["SS_memory_off"]["success_rate"] == 1.0
            and summary["SS_memory_on"]["success_rate"] == 1.0,
            "on_fast_path_hits_mean": summary["SS_memory_on"]["memory_fast_path_hits_mean"],
            "on_memory_effective_mean": summary["SS_memory_on"]["memory_effective_mean"],
        }
    from agent_runtime.evaluation.billing_model import billing_decomposition, probe_provider_overhead

    billing = billing_decomposition(raw)
    overhead_probe = probe_provider_overhead(base_cfg)
    return {
        "billing_model": billing,
        "overhead_probe": overhead_probe,
        "suite": "external_llm",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repeat": repeat,
        "env": env,
        "provider": {
            "llm_provider": base_cfg.llm.provider,
            "llm_model": base_cfg.llm.model,
            "embedding_provider": base_cfg.embedding.provider,
            "embedding_model": base_cfg.embedding.model,
            "temperature": base_cfg.llm.temperature,
            "timeout_sec": base_cfg.llm.timeout_sec,
            "max_retries": base_cfg.llm.max_retries,
        },
        "methodology": {
            "tasks_per_variant": 2,
            "task_groups": ["linux-service", "openeuler-migration"],
            "variants": variants,
            "control_transport": kernel_transport,
            "benchmark_default_max_tokens": benchmark_max_tokens,
            "order": order,
            "execution_order": executed,
            "claim_boundary": "external-validity runtime/provider evidence; deterministic mock-first suites remain primary mechanism attribution evidence",
            "semantic_judge": "enabled only when AGENT_LLM_JUDGE=1; inbound wire is the only protocol-visible evidence",
            "pairing": "paired statistics use only repeat indices present in both arms",
        },
        "completeness": {
            "complete": complete,
            "planned_cells": len(plan),
            "collected_cells": len(executed),
            "resumed_cells": resumed,
            "stopped_reason": stopped_reason,
            "checkpoint": str(checkpoint) if checkpoint else None,
            "wall_sec": round(_time.monotonic() - started, 3),
        },
        "summary": summary,
        "memory_comparison": memory_comparison,
        "raw": raw,
    }


_METRIC_KEYS = (
    "messages", "communication_tokens", "wire_bytes", "kernel_control_bytes",
    "state_bytes", "state_logical_bytes", "strict_transport_bytes", "llm_calls",
    "artifact_bytes",
    "llm_prompt_tokens", "llm_completion_tokens", "llm_cached_prompt_tokens", "total_llm_tokens", "latency_sec", "ipc_frames",
    "state_shm_transfers", "state_int8_transfers", "memory_queries", "memory_used",
    "memory_effective", "memory_harmful", "memory_fast_path_hits",
    "llm_judgements", "llm_judgements_correct",
    "llm_logical_requests", "llm_visible_prompt_tokens", "llm_reasoning_tokens", "llm_cap_violations",
    "llm_coalesced_batches", "llm_coalesce_parse_misses", "llm_projected_latency_sec",
)


def _paired(raw: dict[str, list[dict[str, Any]]], control: str, treatment: str, key: str) -> tuple[list[float], list[float]]:
    c = {r["repeat"]: r for r in raw.get(control, []) if key in r}
    t = {r["repeat"]: r for r in raw.get(treatment, []) if key in r}
    common = sorted(set(c) & set(t))
    return [float(c[i][key]) for i in common], [float(t[i][key]) for i in common]


def _summarize(raw: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for cid, rows in raw.items():
        if not rows:
            continue
        valid = [r for r in rows if not r.get("crashed")]
        summary[cid] = {
            "runs": len(rows),
            "success_rate": sum(int(bool(r.get("success"))) for r in rows) / len(rows),
            **{f"{key}_{stat_name}": value for key in _METRIC_KEYS for stat_name, value in _stats(valid, key).items()},
        }
    # Fairness fix (fable5): judge accuracy must be computed for EVERY arm, including the text
    # baselines.  Previously only structured arms got the field, so the text rows rendered 0.0%
    # although their raw counters showed correct judgements — a bias in our own favour.
    for variant_summary in summary.values():
        variant_summary["judge_accuracy_mean"] = (
            variant_summary["llm_judgements_correct_mean"] / max(1.0, variant_summary["llm_judgements_mean"])
        )
        variant_summary["judge_evaluated"] = variant_summary["llm_judgements_mean"] > 0
    t = summary.get("T")
    for cid in ("S", "SS", "SS_memory_off", "SS_memory_on", "SS_concise", "FULL", "SS_coalesce", "FULL_coalesce", "SS_group", "FULL_group",
                "FULL_related", "FULL_related_group", "SS_group_sv", "FULL_group_sv"):
        if cid not in summary:
            continue
        variant_summary = summary[cid]
        if t is not None:
            variant_summary["communication_token_saving_vs_text"] = 1.0 - variant_summary["communication_tokens_mean"] / max(1.0, t["communication_tokens_mean"])
            variant_summary["strict_transport_saving_vs_text"] = 1.0 - variant_summary["strict_transport_bytes_mean"] / max(1.0, t["strict_transport_bytes_mean"])
        if "SS" in summary:
            variant_summary["completion_token_saving_vs_ss"] = 1.0 - variant_summary["llm_completion_tokens_mean"] / max(1.0, summary["SS"]["llm_completion_tokens_mean"])
            ctl, trt = _paired(raw, "SS", cid, "llm_completion_tokens")
            if ctl:
                variant_summary["completion_effect_vs_ss"] = paired_effect_size(ctl, trt)
                variant_summary["completion_difference_ci95_vs_ss"] = paired_ci95(ctl, trt)
        comparison_id = ("T_memory_pair" if cid in ("FULL", "FULL_coalesce", "FULL_group", "FULL_group_sv")
                         else "T_related" if cid in ("FULL_related", "FULL_related_group") else "T")
        twin = {"SS_coalesce": "T_coalesce", "FULL_coalesce": "T_memory_pair_coalesce", "SS_group": "T_group",
                "FULL_related_group": "T_related_group", "SS_group_sv": "T_group_sv"}.get(cid)
        if twin in summary:
            variant_summary["total_token_saving_vs_coalesced_text"] = 1.0 - (
                variant_summary["total_llm_tokens_mean"] / max(1.0, summary[twin]["total_llm_tokens_mean"])
            )
        if comparison_id in summary:
            comparison = summary[comparison_id]
            variant_summary["total_token_saving_vs_text"] = 1.0 - (
                variant_summary["total_llm_tokens_mean"] / max(1.0, comparison["total_llm_tokens_mean"])
            )
            ctl, trt = _paired(raw, comparison_id, cid, "total_llm_tokens")
            if ctl:
                variant_summary["total_token_paired_vs_text"] = paired_analysis(ctl, trt)
            ctl, trt = _paired(raw, comparison_id, cid, "latency_sec")
            if ctl:
                variant_summary["latency_paired_vs_text"] = paired_analysis(ctl, trt)
    return summary


def _judge_cell(x: dict[str, Any]) -> str:
    """Judge accuracy cell: 'n/a' when the arm ran without AGENT_LLM_JUDGE=1."""
    if not x.get("judge_evaluated"):
        return "n/a"
    return f"{x.get('judge_accuracy_mean', 0.0):.1%}"


def _pct(value: Any) -> str:
    return f"{value:.1%}" if isinstance(value, (int, float)) else "n/a"


def render_external_llm(payload: dict[str, Any]) -> str:
    p = payload["provider"]
    s = payload["summary"]
    embedding_label = (
        "mock (model field ignored)" if p["embedding_provider"].lower() == "mock"
        else f"{p['embedding_provider']}:{p['embedding_model']}"
    )

    def row(cid: str, label: str, state: bool = True) -> str:
        x = s.get(cid)
        if x is None:
            return f"| {label} | not run | | | | | | | | | |"
        state_bytes = x["state_bytes_mean"] if state else 0.0
        def interval(key):
            low, high = x.get(key + "_ci95_low"), x.get(key + "_ci95_high")
            return "CI unavailable (n<2)" if low is None or high is None else f"[{low:.1f},{high:.1f}]"
        return (
            f"| {label} | {x['success_rate']:.0%} (n={x.get('runs', '?')}) | "
            f"{x['communication_tokens_mean']:.1f} {interval('communication_tokens')} | "
            f"{x['kernel_control_bytes_mean']:.1f} | {state_bytes:.1f} | {x['artifact_bytes_mean']:.1f} | "
            f"{x['strict_transport_bytes_mean']:.1f} | {x['llm_calls_mean']:.1f} | "
            f"{_judge_cell(x)} | "
            f"{x['llm_prompt_tokens_mean']:.1f}/{x['llm_completion_tokens_mean']:.1f} "
            f"{interval('llm_completion_tokens')} "
            f"(cached prompt {x['llm_cached_prompt_tokens_mean']:.1f}) | "
            f"{x['latency_sec_mean']:.3f} |"
        )

    comp = payload.get("completeness") or {"complete": True}
    is_projection = str(p.get("llm_provider", "")).lower() == "mock-gateway"
    lines = [
        "# PROJECTED bill (calibrated gateway model) — not provider-billed evidence" if is_projection else "# Real-LLM External-Validity Benchmark",
        "",
        f"> provider={p['llm_provider']}; model={p['llm_model']}; embedding={embedding_label}; temperature={p['temperature']}; "
        f"timeout={p.get('timeout_sec', 'sdk-default')}s; retries={p.get('max_retries', 'sdk-default')}; "
        f"order={payload.get('methodology', {}).get('order', 'fixed')}",
        "",
    ]
    if not comp.get("complete", True):
        lines += [
            f"> **INCOMPLETE RUN** — {comp.get('collected_cells')}/{comp.get('planned_cells')} cells collected; "
            f"reason: {comp.get('stopped_reason')}. Resume with the same `--checkpoint`.",
            "",
        ]
    lines += [
        "| Mode | Success | Communication tokens | Kernel control B | State B | Artifact B | Strict transport B (control+state+artifact) | LLM calls | Judge accuracy | Provider prompt/completion tokens | Latency s |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        row("T", "Lean Text"),
        row("T_memory_pair", "Lean Text / memory-paired baseline"),
        row("S", "Structured only", state=False),
        row("SS", "Structured+State"),
        row("SS_memory_off", "Memory pair / off"),
        row("SS_memory_on", "Memory pair / on"),
        row("SS_concise", "SS + concise output"),
        row("FULL", "FULL: SS + memory + forward + concise"),
        row("T_coalesce", "Lean Text + LLM coalescing (fairness twin)"),
        row("T_memory_pair_coalesce", "Lean Text / memory pair + LLM coalescing (fairness twin)"),
        row("SS_coalesce", "SS + LLM coalescing"),
        row("FULL_coalesce", "**SOTA1: FULL + LLM coalescing**"),
        row("T_group", "Lean Text + group commit (fairness twin)"),
        row("SS_group", "**SOTA1: SS + group commit**"),
        row("FULL_group", "**SOTA1: FULL + group commit**"),
        row("T_related", "Lean Text / related follow-up (self-contained)"),
        row("FULL_related", "FULL / related follow-up via shared memory"),
        row("T_related_group", "Lean Text / related + group commit (fairness twin)"),
        row("FULL_related_group", "**SOTA1.1: FULL / related + group commit**"),
        row("T_group_sv", "Lean Text + group commit + semantic view (twin)"),
        row("SS_group_sv", "**SOTA1.5: SS + group commit + semantic view**"),
        row("FULL_group_sv", "**SOTA1.5: FULL + group commit + semantic view**"),
        "",
    ]
    ss = s.get("SS", {})
    if "communication_token_saving_vs_text" in ss:
        lines.append(f"- Communication-token saving vs Lean Text: **{_pct(ss['communication_token_saving_vs_text'])}**.")
        lines.append(f"- Strict total transport saving vs Lean Text: **{_pct(ss['strict_transport_saving_vs_text'])}**.")
    if "completion_token_saving_vs_ss" in s.get("SS_concise", {}):
        lines.append(f"- Completion-token saving of SS + concise vs SS: **{_pct(s['SS_concise']['completion_token_saving_vs_ss'])}**.")
    full = s.get("FULL", {})
    if "total_token_saving_vs_text" in full:
        ci = full.get("total_token_paired_vs_text", {}).get("ci95")
        ci_txt = f"; paired 95% CI on FULL total-token difference=[{ci['low']:.1f}, {ci['high']:.1f}]" if ci else ""
        lines.append(f"- FULL total-token saving vs T_memory_pair: **{_pct(full['total_token_saving_vs_text'])}**{ci_txt}.")
    fc = s.get("FULL_coalesce", {})
    if "total_token_saving_vs_text" in fc:
        lines.append(
            f"- **SOTA1** FULL + LLM coalescing total-token saving vs T_memory_pair: **{_pct(fc['total_token_saving_vs_text'])}** "
            f"(provider requests {fc['llm_calls_mean']:.1f} for {fc['llm_logical_requests_mean']:.1f} logical agent requests)."
        )
    if "total_token_saving_vs_coalesced_text" in fc:
        lines.append(
            f"- Fairness: FULL + coalescing vs *equally coalesced* text: **{_pct(fc['total_token_saving_vs_coalesced_text'])}** "
            "(protocol/state/memory effect with batching held equal)."
        )
    for cid, label in (("FULL_group_sv", "FULL + group commit + semantic view (vs T_memory_pair)"),
                       ("SS_group_sv", "SS + group commit + semantic view"),
                       ("FULL_related", "FULL related follow-up via memory (vs T_related)"),
                       ("FULL_related_group", "FULL related + group commit (vs T_related)"),
                       ("FULL_group", "FULL + group commit (vs T_memory_pair)"), ("SS_group", "SS + group commit"),
                       ("SS_coalesce", "SS + per-task coalescing")):
        x = s.get(cid, {})
        if "total_token_saving_vs_text" in x:
            extra = (f"; vs equally batched text {_pct(x['total_token_saving_vs_coalesced_text'])}"
                     if "total_token_saving_vs_coalesced_text" in x else "")
            lines.append(f"- **SOTA1** {label} total-token saving: **{_pct(x['total_token_saving_vs_text'])}**{extra}.")
    billing = payload.get("billing_model")
    if billing and billing.get("fit"):
        f = billing["fit"]
        lines.append(
            f"- Billing decomposition (fit over this run's cells): provider prompt = "
            f"{f['overhead_per_request']:.0f}·requests + {f['visible_scale']:.3f}·runtime-visible tokens "
            f"(R²={f['r2']:.4f}, max rel. err {f['max_rel_err']:.1%}); fixed share of SS prompt bill: "
            f"{_pct(billing.get('fixed_share', {}).get('SS'))}."
        )
        if billing.get("net_prompt_saving"):
            lines.append(
                "- Net runtime-controllable prompt-token saving vs Lean Text: "
                + ", ".join(f"{k} {_pct(v)}" for k, v in billing["net_prompt_saving"].items())
                + "."
            )
    if any(x.get("llm_projected_latency_sec_mean", 0) for x in s.values()):
        lines.append(
            "- PROJECTED provider latency (calibrated law 7.03 s/request + 0.043 s/completion token, R²=0.62): "
            + ", ".join(f"{cid} {x['llm_projected_latency_sec_mean']:.1f}s" for cid, x in s.items()
                        if cid in ("T", "SS", "FULL", "SS_coalesce", "FULL_coalesce", "SS_group", "FULL_group"))
            + "."
        )
    probe = payload.get("overhead_probe")
    if probe:
        lines.append(f"- Provider overhead probe: {probe.get('overhead_tokens')} hidden prompt tokens per request ({probe.get('status')}).")
    judged = {cid: x for cid, x in s.items() if x.get("judge_evaluated")}
    if judged:
        def _acc(ids):
            xs = [judged[c] for c in ids if c in judged]
            n = sum(x["llm_judgements_mean"] * x.get("runs", 1) for x in xs)
            k = sum(x["llm_judgements_correct_mean"] * x.get("runs", 1) for x in xs)
            return (k / n, int(round(n))) if n else (None, 0)
        direct = _acc(["T", "T_memory_pair", "S", "SS", "SS_memory_off", "SS_memory_on", "SS_concise", "FULL"])
        batched = _acc(["T_coalesce", "T_memory_pair_coalesce", "SS_coalesce", "FULL_coalesce", "T_group", "SS_group", "FULL_group"])
        text_arms = _acc(["T", "T_memory_pair", "T_coalesce", "T_memory_pair_coalesce", "T_group"])
        struct_arms = _acc(["S", "SS", "SS_memory_off", "SS_memory_on", "SS_concise", "FULL", "SS_coalesce", "FULL_coalesce", "SS_group", "FULL_group"])
        lines.append(
            "- Semantic judge (reads only the inbound wire): "
            f"direct calls {_pct(direct[0])} (n={direct[1]}), coalesced/group-committed {_pct(batched[0])} (n={batched[1]}); "
            f"text arms {_pct(text_arms[0])} (n={text_arms[1]}), structured arms {_pct(struct_arms[0])} (n={struct_arms[1]})."
        )
    mc = payload.get("memory_comparison")
    if mc:
        lines.append(
            f"- Memory comparison: both successful={mc['both_successful']}; "
            f"fast-path hits/run={mc['on_fast_path_hits_mean']:.1f}; "
            f"effective reuse/run={mc['on_memory_effective_mean']:.1f}."
        )
    retries = sum(len(r.get("errors", [])) for rows in payload.get("raw", {}).values() for r in rows)
    lines += [
        f"- Cells collected: {comp.get('collected_cells', 'n/a')}/{comp.get('planned_cells', 'n/a')} "
        f"(resumed {comp.get('resumed_cells', 0)}); task-level errors recorded: {retries}.",
        "- This suite is supplemental external-validity evidence; it does not replace the controlled deterministic mechanism experiments.",
        "",
        f"Source SHA-256: `{payload['env']['source_tree_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def write_external_llm_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    suite = "external_llm_projection" if str(payload.get("provider", {}).get("llm_provider", "")).lower() == "mock-gateway" else "external_llm"
    payload = {**payload, "suite": suite}
    out = _allocate_result_dir(str(root), suite)
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_external_llm(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": payload["generated_at_utc"],
                "suite": payload["suite"],
                "repeat": payload["repeat"],
                "provider": payload["provider"],
                "environment": payload["env"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    return out


def run_external_llm_cli(args) -> int:
    import sys as _sys

    variants = [v.strip() for v in (getattr(args, "variants", None) or "").split(",") if v.strip()] or None
    if getattr(args, "judge", False):
        import os as _os

        _os.environ["AGENT_LLM_JUDGE"] = "1"
    budget_min = getattr(args, "budget_min", None)
    payload = run_external_llm_benchmark(
        repeat=args.repeat,
        allow_mock=args.allow_mock,
        variants=variants,
        checkpoint=getattr(args, "checkpoint", None),
        budget_sec=budget_min * 60.0 if budget_min else None,
        order=getattr(args, "order", "balanced"),
        progress=lambda msg: print(msg, file=_sys.stderr, flush=True),
    )
    out = write_external_llm_results(payload)
    print(render_external_llm(payload))
    print(f"\n结果已保存到: {out}")
    return 0 if payload.get("completeness", {}).get("complete", True) else 3


__all__ = ["run_plan", "run_external_llm_benchmark", "write_external_llm_results", "render_external_llm"]
