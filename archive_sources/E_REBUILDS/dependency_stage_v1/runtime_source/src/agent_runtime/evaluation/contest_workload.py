"""Competition-primary Linux/openEuler workload evidence.

This suite is deliberately close to the official wording:
1. same four self-contained OS tasks compare lean text / structured / structured+state;
2. the two follow-up tasks then omit their procedure so M0 vs M1 isolates cross-task reuse;
3. message/token/UTF-8 wire/state/latency and memory adoption are reported together.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
from typing import Any

from agent_runtime import PROTOCOL_VERSION
from agent_runtime.config import load_config
from agent_runtime.evaluation.runner import (
    ExperimentConfig,
    _aggregate,
    _allocate_result_dir,
    _env_info,
    _write_checksum_manifest,
    run_once,
)
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.linux_service import make_group as make_service_group
from agent_runtime.scenarios.openeuler_migration import make_group as make_migration_group
from agent_runtime.scenarios.openeuler_kernel_tuning import (
    make_adversarial_group,
    make_kernel_group,
)


def _self_contained_tasks():
    return [*make_service_group(self_contained=True), *make_migration_group(self_contained=True)]


def _linked_tasks():
    return [*make_service_group(), *make_migration_group()]


def _extended_linked_tasks():
    return [*make_kernel_group(), *make_adversarial_group()]


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


def _memory_phase(use_memory: bool, *, extended: bool = False, mode: str = "structured", use_state: bool = True) -> dict[str, Any]:
    cfg = load_config(
        load_env=False,
        control_transport="uds" if hasattr(socket, "AF_UNIX") else "inproc",
        structured_wire_codec="session_msgpack",
        state_shared_memory=hasattr(socket, "AF_UNIX"),
        state_precision="adaptive",
        text_style="lean",
    )
    memory = MemoryStore(build_embedding(cfg)) if use_memory else None
    snapshots = []
    tasks = []
    try:
        for task in (_extended_linked_tasks() if extended else _linked_tasks()):
            ctx = build_context(
                cfg,
                mode,
                task_id=task.task_id,
                use_memory=use_memory,
                use_state_exchange=use_state,
                use_sandbox=True,
                memory=memory,
            )
            result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
            snapshots.append(result.metrics)
            tasks.append(
                {
                    "topic": task.topic,
                    "group": task.topic,
                    "description": task.description,
                    "is_followup": task.parent_task_id is not None,
                    "success": result.success,
                    "answer": result.outputs.get("answer"),
                    "messages": result.metrics.message_count,
                    "wire_bytes": result.metrics.wire_bytes,
                    "latency_sec": result.metrics.latency_sec,
                    "memory_queries": result.metrics.memory_queries,
                    "memory_hits": result.metrics.memory_query_hits,
                    "memory_used": result.metrics.memory_used,
                    "memory_effective": result.metrics.memory_effective,
                    "memory_harmful": result.metrics.memory_harmful,
                    "memory_fast_path_hits": result.metrics.memory_fast_path_hits,
                    "ipc_frames": result.metrics.ipc_frames,
                }
            )
    finally:
        if memory is not None:
            memory.close()
    agg = _aggregate(snapshots)
    followups = [t for t in tasks if t["is_followup"]]
    followup_used = sum(float(t["memory_used"]) for t in followups)
    followup_effective = sum(float(t["memory_effective"]) for t in followups)
    followup_harmful = sum(float(t["memory_harmful"]) for t in followups)
    memory_summary = {
        # The official "hit rate" is exposed explicitly, but named candidate-hit here to avoid
        # conflating retrieval with downstream adoption/effectiveness.
        "query_hit_rate": _ratio(agg["memory_query_hits"], agg["memory_queries"]),
        "adoption_precision": _ratio(agg["memory_effective"], agg["memory_used"]),
        "negative_transfer_rate": _ratio(agg["memory_harmful"], agg["memory_used"]),
        "followup_task_count": len(followups),
        "followup_success_count": sum(int(t["success"]) for t in followups),
        "followup_adoption_rate": _ratio(followup_used, len(followups)),
        "followup_effective_reuse_rate": _ratio(followup_effective, len(followups)),
        "followup_negative_transfer_rate": _ratio(followup_harmful, len(followups)),
        "fast_path_hit_rate": _ratio(
            agg["memory_fast_path_hits"], agg["memory_fast_path_probes"]
        ),
    }
    return {
        "success_count": sum(int(t["success"]) for t in tasks),
        "task_count": len(tasks),
        "tasks": tasks,
        "aggregate": agg,
        "memory_summary": memory_summary,
    }



def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(float(x) for x in values)
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _sandbox_latency(repeat: int = 30) -> dict[str, Any]:
    """Cold-vs-warm CodeAct latency with byte-identical output and equal-work E2E runs."""
    import time as _time
    from agent_runtime.config import SandboxConfig
    from agent_runtime.sandbox.executor import CodeActExecutor

    code = "import json\nprint(json.dumps({'value': sum(range(100))}))\n"
    rows: dict[str, Any] = {}
    outputs: dict[str, str] = {}
    for label, warm in (("cold", False), ("warm", True)):
        ex = CodeActExecutor(SandboxConfig(warm_start=warm))
        ex.run(code)
        samples: list[float] = []
        last = None
        for _ in range(repeat):
            t0 = _time.perf_counter()
            last = ex.run(code)
            samples.append(_time.perf_counter() - t0)
            outputs[label] = last.stdout
        rows[label] = {
            "backend": last.backend if last is not None else "unknown",
            "p50_ms": _percentile(samples, 0.5) * 1000,
            "p95_ms": _percentile(samples, 0.95) * 1000,
            "mean_ms": sum(samples) / max(1, len(samples)) * 1000,
            "fallbacks": getattr(ex, "warm_fallbacks", 0),
        }
    identical = outputs.get("cold") == outputs.get("warm")
    speedup = rows["cold"]["p50_ms"] / max(1e-9, rows["warm"]["p50_ms"])

    kernel_transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    e2e: dict[str, Any] = {}
    for label, warm in (("cold", False), ("warm", True)):
        cfg = ExperimentConfig(
            f"SS-{label}", f"Structured+State {label}", "structured", True, False, True,
            structured_wire_codec="session_msgpack", control_transport=kernel_transport,
            state_shared_memory=hasattr(socket, "AF_UNIX"), state_precision="adaptive",
            sandbox_warm_start=warm,
        )
        run_once(cfg, _self_contained_tasks, 42)  # warm imports/router/fork-server
        row = run_once(cfg, _self_contained_tasks, 42)
        e2e[label] = {
            "task_success": row["task_success"],
            "mean_task_latency_sec": row["latency_sec"] / 4.0,
            "wire_bytes": row["wire_bytes"],
            "artifact_bytes": row["artifact_bytes"],
        }
    same_work = (
        e2e["cold"]["task_success"] == e2e["warm"]["task_success"] == 1.0
        and e2e["cold"]["wire_bytes"] == e2e["warm"]["wire_bytes"]
        and e2e["cold"]["artifact_bytes"] == e2e["warm"]["artifact_bytes"]
    )
    reduction = 1 - e2e["warm"]["mean_task_latency_sec"] / max(1e-12, e2e["cold"]["mean_task_latency_sec"])
    return {
        "repeat": repeat, "micro": rows, "outputs_identical": identical,
        "p50_speedup": speedup, "end_to_end": e2e, "end_to_end_same_work": same_work,
        "end_to_end_latency_reduction": reduction if same_work else None,
    }

def _compression_fairness() -> dict[str, Any]:
    """fable7 honesty check: what if the TEXT baseline were also compressed?

    Byte savings of a structured protocol partly come from removing repetition that a generic
    compressor would also remove.  We therefore replay the exact messages of one lean-text run and
    one Structured+State run and report their control-plane bytes raw, per-frame zlib and
    session-stream zlib (one zlib context per task connection, SYNC_FLUSH per frame; the strongest
    realistic baseline).  Tokens are unaffected by compression: an LLM consumes tokens, not gzip.
    """
    import asyncio
    import zlib

    from agent_runtime.protocol.serialization import render_text_lean
    from agent_runtime.protocol.session_codec import SessionWireCodec
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator

    from agent_runtime.protocol.schema_atoms import encode_value
    from agent_runtime.providers.tokenizer import get_tokenizer

    tok = get_tokenizer()
    out: dict[str, Any] = {}
    for label, mode, use_state in (("T", "text", False), ("SS", "structured", True)):
        cfg = load_config(load_env=False, text_style="lean", control_transport="inproc", header_prediction=False)
        raw = per_frame = stream = 0
        for task in _self_contained_tasks():
            ctx = build_context(cfg, mode, task_id=task.task_id, use_state_exchange=use_state, use_sandbox=True)
            result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
            comp = zlib.compressobj(9)
            codec = SessionWireCodec()
            if mode == "text":
                # Token fairness: give the TEXT baseline the same columnar record layout the
                # structured protocol uses (tables are equally readable as text), without atoms.
                for message in result.messages:
                    tab = message.model_copy(update={
                        "input_parameters": encode_value(message.input_parameters, atoms=False),
                        "result": encode_value(message.result, atoms=False) if message.result is not None else None,
                    })
                    out.setdefault("T_columnar_tokens", 0)
                    out["T_columnar_tokens"] += tok.count(render_text_lean(tab))
            for message in result.messages:
                data = render_text_lean(message).encode("utf-8") if mode == "text" else codec.encode_dual(message)[1]
                raw += len(data)
                per_frame += len(zlib.compress(data, 9))
                stream += len(comp.compress(data) + comp.flush(zlib.Z_SYNC_FLUSH))
        out[label] = {"raw_control_payload_bytes": raw, "per_frame_zlib_bytes": per_frame, "session_stream_zlib_bytes": stream}
    return out


def run_contest_workload() -> dict[str, Any]:
    runtime_cfg = load_config(load_env=False)
    kernel_transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    comm_cfgs = [
        ExperimentConfig(
            "T", "Lean Text + UDS + Sandbox", "text", False, False, True,
            text_style="lean", control_transport=kernel_transport,
        ),
        ExperimentConfig(
            "S", "Session Structured + UDS + Sandbox", "structured", False, False, True,
            structured_wire_codec="session_msgpack", control_transport=kernel_transport,
        ),
        ExperimentConfig(
            "SS", "Session Structured + SHM State + UDS + Sandbox", "structured", True, False, True,
            structured_wire_codec="session_msgpack", control_transport=kernel_transport,
            state_shared_memory=hasattr(socket, "AF_UNIX"), state_precision="adaptive",
        ),
    ]
    communication = {cfg.id: run_once(cfg, _self_contained_tasks, 42) for cfg in comm_cfgs}
    for row in communication.values():
        row["task_count"] = 4
        row["mean_task_latency_sec"] = row["latency_sec"] / 4.0
    # Report both control-plane wire and total physical transport.  State bytes are deliberately
    # separate in the raw metrics, so adding them here prevents an inflated "communication
    # saving" headline that silently ignores the non-text data plane.
    for row in communication.values():
        row["control_physical_bytes"] = (
            (row["ipc_framed_bytes"] + row["ipc_ack_bytes"]) if row["ipc_frames"] else row["wire_bytes"]
        )
        row["control_state_transport_bytes"] = row["control_physical_bytes"] + row["state_bytes"]
        # v0.8 strict headline includes all three physical planes.
        row["full_transport_bytes"] = row["control_state_transport_bytes"] + row["artifact_bytes"]
        row["transport_bytes"] = row["full_transport_bytes"]  # compatibility alias, now truly full-plane
    base = communication["T"]
    for cid in ("S", "SS"):
        row = communication[cid]
        row["token_delta_vs_text"] = 1 - row["text_tokens"] / max(1.0, base["text_tokens"])
        row["wire_delta_vs_text"] = 1 - row["wire_bytes"] / max(1.0, base["wire_bytes"])
        row["control_physical_delta_vs_text"] = 1 - row["control_physical_bytes"] / max(1.0, base["control_physical_bytes"])
        row["control_state_delta_vs_text"] = 1 - row["control_state_transport_bytes"] / max(1.0, base["control_state_transport_bytes"])
        row["transport_delta_vs_text"] = 1 - row["full_transport_bytes"] / max(1.0, base["full_transport_bytes"])
        row["latency_delta_vs_text"] = 1 - row["latency_sec"] / max(1e-12, base["latency_sec"])

    ss = communication["SS"]
    ss["state_physical_saving_vs_logical"] = 1 - ss["state_bytes"] / max(1.0, ss["state_logical_bytes"])
    ss["versioned_state_physical_saving_vs_logical"] = 1 - ss["versioned_state_bytes"] / max(1.0, ss["versioned_state_logical_bytes"])
    ss["versioned_delta_ratio"] = _ratio(ss["versioned_state_delta_updates"], ss["versioned_state_transfers"])
    ss["state_dedup_hit_rate"] = _ratio(ss["state_dedup_hits"], ss["state_transfers"])
    ss["reference_placement_rate"] = _ratio(ss["placement_reference"], ss["placement_decisions"])

    memory = {
        "M0": _memory_phase(False),
        "M1": _memory_phase(True),
        "M0_extended": _memory_phase(False, extended=True),
        "M1_extended": _memory_phase(True, extended=True),
        "T_memory_off": _memory_phase(False, mode="text", use_state=False),
        "E_text_memory": _memory_phase(True, mode="text", use_state=False),
    }
    sandbox_latency = _sandbox_latency()
    fairness = _compression_fairness()
    return {
        "suite": "contest_workload",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "env": _env_info(),
        "methodology": {
            "communication_tasks": 4,
            "memory_groups": [
                "linux-service-diagnosis",
                "openeuler-migration-assessment",
                "openeuler-kernel-tuning (3-step)",
                "openeuler-abi-compatibility (adversarial)",
            ],
            "protocol_version": PROTOCOL_VERSION,
            "structured_codec": "Protocol 1.3 stateful session JSON token frame + MessagePack physical wire",
            "control_transport": kernel_transport,
            "state_precision": "adaptive int8→float16 fallback",
            "state_transport": "shared-memory plan embedding/vector on AF_UNIX hosts + versioned JSON delta state; physical bytes reported separately",
            "strict_transport_accounting": "outbound UDS framing + verified SHA-256 ACK framing + materialized state bytes + content-addressed artifact bytes",
            "note": "communication phase keeps procedures inline for equal-success comparison; memory phase removes only follow-up procedures",
        },
        "communication": communication,
        "memory": memory,
        "sandbox_latency": sandbox_latency,
        "compression_fairness": fairness,
    }


def render_contest_workload(payload: dict[str, Any]) -> str:
    comm = payload["communication"]
    mem = payload["memory"]
    lines = [
        "# Competition Workload — Linux/openEuler Continuous Tasks",
        "",
        "> Four OS-oriented tasks traverse Planner → Retriever → Executor sandbox → Reviewer.",
        f"> Protocol {payload['methodology']['protocol_version']}; state precision={payload['methodology']['state_precision']}; unreferenced JSON payloads remain complete.",
        "",
        "## Communication / state comparison (same successful tasks)",
        "",
        "> Strict transport in v0.8 means verified UDS control + state + artifact bytes.",
        "",
        "| Mode | Success | Messages | Tokens | Codec payload B | Verified UDS control B | State B | Artifact B | Control+State B | Full physical B | Token Δ | Full Δ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cid, label in (("T", "Lean Text"), ("S", "Structured"), ("SS", "Structured+State")):
        r = comm[cid]
        td = 0.0 if cid == "T" else r["token_delta_vs_text"]
        trd = 0.0 if cid == "T" else r["transport_delta_vs_text"]
        lines.append(
            f"| {label} | {r['task_success']:.0%} | {r['message_count']:.0f} | {r['text_tokens']:.0f} | "
            f"{r['wire_bytes']:.0f} | {r['control_physical_bytes']:.0f} | {r['state_bytes']:.0f} | {r.get('artifact_bytes', 0.0):.0f} | "
            f"{r.get('control_state_transport_bytes', r['control_physical_bytes'] + r['state_bytes']):.0f} | {r.get('full_transport_bytes', r.get('transport_bytes', r['control_physical_bytes'] + r['state_bytes'])):.0f} | {td:+.1%} | {trd:+.1%} |"
        )
    ss = comm["SS"]
    lines += [
        "",
        "### State/data-plane efficiency",
        "",
        f"- Physical state bytes vs logical materialization: **{ss['state_bytes']:.0f}/{ss['state_logical_bytes']:.0f} B**, saving **{ss['state_physical_saving_vs_logical']:.1%}**.",
        f"- Numeric state precision chosen: int4(decision-certified)={ss.get('state_int4_transfers', 0):.0f}, int8={ss['state_int8_transfers']:.0f}, float16={ss['state_float16_transfers']:.0f}, float32={ss['state_float32_transfers']:.0f}; SHM handoffs={ss['state_shm_transfers']:.0f}.",
        f"- Real verified UDS control plane: **{ss.get('ipc_verified', ss.get('ipc_frames', 0)):.0f}/{ss['message_count']:.0f}** frames losslessly reconstructed in the router subprocess; socket API calls={ss.get('ipc_socket_calls', 0):.0f}; outbound framed={ss['ipc_framed_bytes']:.0f} B, SHA-256 ACK framed={ss['ipc_ack_bytes']:.0f} B.",
        f"- Versioned state: snapshots={ss['versioned_state_snapshots']:.0f}, deltas={ss['versioned_state_delta_updates']:.0f}, delta ratio=**{ss['versioned_delta_ratio']:.1%}**.",
        f"- Reference placement: **{ss['placement_reference']:.0f}/{ss['placement_decisions']:.0f}**; estimated avoided inline bytes={ss['placement_estimated_savings_bytes']:.0f}.",
        f"- Full-plane physical accounting: control+state saving **{ss.get('control_state_delta_vs_text', ss['transport_delta_vs_text']):.1%}**; after adding artifact bytes, full physical saving **{ss['transport_delta_vs_text']:.1%}**.",
        "",
        "## Cross-task shared-memory reuse",
        "",
        "| Mode | Successful tasks | Memory queries | Query hits | Adopted | Effective | Harmful |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cid, label in (("M0", "Structured+State / no Memory"), ("M1", "Structured+State / Memory"),
                       ("T_memory_off", "Lean Text / no Memory"), ("E_text_memory", "E: Lean Text / same Memory")):
        if cid not in mem:  # Historical result payloads predate the new factorial arms.
            continue
        row = mem[cid]
        a = row["aggregate"]
        lines.append(
            f"| {label} | {row['success_count']}/{row['task_count']} | {a['memory_queries']:.0f} | "
            f"{a['memory_query_hits']:.0f} | {a['memory_used']:.0f} | {a['memory_effective']:.0f} | {a['memory_harmful']:.0f} |"
        )
    cf = payload.get("compression_fairness")
    if cf:
        idx = lines.index("## Cross-task shared-memory reuse")
        t, ss = cf["T"], cf["SS"]
        lines[idx:idx] = [
            "## Fairness: compressed-text baseline (control-plane payload bytes, same tasks)", "",
            "| Mode | Raw | Per-frame zlib | Session-stream zlib |",
            "| --- | ---: | ---: | ---: |",
            f"| Lean Text | {t['raw_control_payload_bytes']} | {t['per_frame_zlib_bytes']} | {t['session_stream_zlib_bytes']} |",
            f"| Structured+State (Protocol 1.3 frames) | {ss['raw_control_payload_bytes']} | {ss['per_frame_zlib_bytes']} | {ss['session_stream_zlib_bytes']} |",
            "",
            f"- Token fairness: Lean Text rendered with the same columnar record layout (no key atoms) = "
            f"**{cf.get('T_columnar_tokens', 'n/a')}** tokens vs {payload['communication']['T']['text_tokens']:.0f} plain; "
            f"Structured+State uses {payload['communication']['SS']['text_tokens']:.0f}.",
            "- A generic compressor removes most of the *byte* redundancy of lean text as well; physical-byte "
            "savings are therefore reported against uncompressed lean text (the contest baseline) **and** "
            "disclosed here.  Token savings are unaffected by compression: an LLM consumes tokens, not zlib bytes.",
            "",
        ]
    sl = payload.get("sandbox_latency")
    if sl:
        idx = lines.index("## Cross-task shared-memory reuse")
        sandbox_lines = [
            "## Warm CodeAct fork-server latency", "",
            f"- Cold/warm sandbox p50: **{sl['micro']['cold']['p50_ms']:.2f}/{sl['micro']['warm']['p50_ms']:.2f} ms**; speed-up **{sl['p50_speedup']:.1f}x**; outputs identical={sl['outputs_identical']}.",
            f"- Equal-work SS mean task latency cold/warm: **{sl['end_to_end']['cold']['mean_task_latency_sec']*1000:.2f}/{sl['end_to_end']['warm']['mean_task_latency_sec']*1000:.2f} ms**; reduction **{(sl['end_to_end_latency_reduction'] or 0):.1%}**.",
            "",
        ]
        lines[idx:idx] = sandbox_lines

    m1s = mem["M1"]["memory_summary"]
    lines += [
        "",
        f"- Shared-memory candidate query hit rate: **{m1s['query_hit_rate']:.1%}**.",
        f"- Exact-input fast-path hit rate in this *related-but-not-identical* workload: **{m1s['fast_path_hit_rate']:.1%}** (exact-repeat efficiency is measured separately).",
        f"- Related follow-up effective reuse: **{m1s['followup_effective_reuse_rate']:.1%}**; adoption precision: **{m1s['adoption_precision']:.1%}**; negative transfer: **{m1s['negative_transfer_rate']:.1%}**.",
        "",
        "> M0/M1 have different success counts, so their latency/token totals are intentionally not interpreted as efficiency ratios.",
        "",
        f"Source SHA-256: `{payload['env']['source_tree_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def write_contest_workload(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "contest_workload")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_contest_workload(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": payload["generated_at_utc"],
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


def run_contest_workload_cli(_args) -> int:
    payload = run_contest_workload()
    out = write_contest_workload(payload)
    print(render_contest_workload(payload))
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["run_contest_workload", "write_contest_workload", "run_contest_workload_cli"]
