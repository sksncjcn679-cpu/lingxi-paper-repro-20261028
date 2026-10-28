"""Competition requirement verifier and evidence scorecard.

This module is intentionally aligned to the wording of the official task.  It does
not estimate a judge score; it verifies whether each mandatory requirement has
machine-checkable evidence and records the evidence in a reproducible artifact.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import platform
import socket
from typing import Any

from agent_runtime import RELEASE_NAME, __version__


def _kernel_guard_evidence(root_path) -> str:
    d = _kernel_guard_result(root_path)
    if d is None:
        return "UNVERIFIED: no same-source, same-host sandbox result; run sandbox-security"
    g = d.get("guard_report") or {}
    return (f"seccomp={g.get('seccomp')}; landlock_abi={g.get('landlock_abi')}; "
            f"attacks_blocked={d.get('attacks_blocked')}; legitimate_ran={d.get('legitimate_ran')}; "
            f"overhead_ms={(d.get('warm_overhead_ms') or {}).get('delta')}")

def _kernel_guard_result(root_path):
    current = _env_info()
    for path in sorted((root_path / "results").glob("*-sandbox_security/summary.json"), reverse=True):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        env = d.get("env") or {}
        if all(env.get(k) == current.get(k) for k in ("source_tree_sha256", "platform", "host_identity")):
            return d
    return None

def _kernel_guard_passed(root_path):
    d = _kernel_guard_result(root_path) or {}
    g = d.get("guard_report") or {}
    return bool(g.get("seccomp") and g.get("landlock_abi") and d.get("all_contained")
                and d.get("all_functional") and d.get("no_file_escaped"))
from agent_runtime.config import load_config
from agent_runtime.evaluation.contest_workload import run_contest_workload
from agent_runtime.evaluation.memory_accuracy import run_memory_accuracy_benchmark
from agent_runtime.evaluation.runner import (
    _allocate_result_dir,
    _env_info,
    _write_checksum_manifest,
)
from agent_runtime.evaluation.stability_benchmark import run_stability_benchmark
from agent_runtime.memory.models import MemoryRecord
from agent_runtime.memory.store import MemoryStore
from agent_runtime.observability.metrics import MetricsSnapshot
from agent_runtime.protocol.mapper import from_wire, semantically_equivalent, to_wire
from agent_runtime.protocol.message import ActionType, AgentMessage
from agent_runtime.providers.factory import build_embedding
from agent_runtime.registry.registry import AgentRegistry
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.linux_service import make_group as make_service_group
from agent_runtime.scenarios.openeuler_migration import make_group as make_migration_group
from agent_runtime.scenarios.openeuler_kernel_tuning import (
    make_adversarial_group,
    make_kernel_group,
)


@dataclass
class Check:
    id: str
    requirement: str
    passed: bool
    evidence: str
    mandatory: bool = True


def _environment_check_ids() -> set[str]:
    """Checks that can only be decided on the target kind of host.

    ``openeuler`` always.  On a non-Linux development host the native-Linux IPC gates cannot be
    evaluated either, so they are reported PENDING (not FAIL) and excluded from the
    non-environment count; on Linux they stay mandatory and a failure is a real failure.
    """
    import sys as _sys

    ids = {"openeuler"}
    if not _sys.platform.startswith("linux"):
        ids |= {"platform", "ipc_integrity"}
    return ids


def _is_target_openeuler() -> tuple[bool, str]:
    p = Path("/etc/os-release")
    if not p.exists():
        return False, "no /etc/os-release"
    text = p.read_text(encoding="utf-8", errors="replace")
    low = text.lower()
    # openEuler images commonly expose VERSION_ID=24.03 and VERSION=24.03 (LTS-SPx).
    is_oe = "openeuler" in low
    version_ok = "24.03" in low and ("lts-sp3" in low or "24.03-lts-sp3" in low)
    summary = "; ".join(
        line for line in text.splitlines() if line.startswith(("NAME=", "VERSION=", "VERSION_ID="))
    )
    os_ok = bool(is_oe and version_ok)
    if not os_ok:
        return False, summary or "unknown os-release"
    # opus3: the OS name alone is NOT a verification.  scripts/verify_openeuler.sh exports
    # AGENT_OPENEULER_PRECHECKS=<source SHA> only when compile/tests/stability/demo/workloads all
    # passed for this exact source tree; anything else stays PENDING.
    from agent_runtime.evaluation.runner import _env_info

    sha = _env_info()["source_tree_sha256"]
    signed = os.environ.get("AGENT_OPENEULER_PRECHECKS", "")
    if signed != sha:
        return False, f"{summary}; os=openEuler but full verification not signed for source {sha[:12]} (run scripts/verify_openeuler.sh)"
    return True, f"{summary}; prechecks signed for source {sha[:12]}"


def _run_two_related_groups() -> dict[str, Any]:
    kernel_transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    cfg = load_config(
        load_env=False,
        structured_wire_codec="session_msgpack",
        control_transport=kernel_transport,
        state_shared_memory=hasattr(socket, "AF_UNIX"),
        state_precision="adaptive",
    )
    memory = MemoryStore(embedding=build_embedding(cfg))
    details: list[dict[str, Any]] = []
    factories = (
        ("linux-service", make_service_group),
        ("openeuler-migration", make_migration_group),
        ("openeuler-kernel-tuning", make_kernel_group),
        ("openeuler-abi-adversarial", make_adversarial_group),
    )
    for group_name, factory in factories:
        for task in factory():
            ctx = build_context(
                cfg,
                "structured",
                task_id=task.task_id,
                use_memory=True,
                use_state_exchange=True,
                use_sandbox=True,
                memory=memory,
            )
            result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
            details.append(
                {
                    "group": group_name,
                    "task_id": task.task_id,
                    "success": result.success,
                    "memory_retrieved": result.metrics.memory_retrieved,
                    "memory_used": result.metrics.memory_used,
                    "memory_effective": result.metrics.memory_effective,
                    "memory_harmful": result.metrics.memory_harmful,
                    "message_count": result.metrics.message_count,
                    "pipeline_topology": cfg.pipeline_topology,
                    "state_transfers": result.metrics.state_transfers,
                    "pipeline_agents": sorted({m.source_agent for m in result.messages if m.source_agent in {"planner", "retriever", "executor", "reviewer"}}),
                }
            )
    records = memory.all_records()
    memory_unit = {
        "total": len(records),
        "with_artifact_refs": sum(int(bool(r.artifact_refs)) for r in records),
        "with_summary": sum(int(bool(r.summary.strip())) for r in records),
        "with_strategy": sum(int(bool(r.payload.get("solution"))) for r in records),
    }
    memory.close()
    return {
        "runs": details,
        "groups": {
            name: {
                "task_count": sum(1 for x in details if x["group"] == name),
                "success_count": sum(int(x["success"]) for x in details if x["group"] == name),
                "effective": sum(x["memory_effective"] for x in details if x["group"] == name),
                "harmful": sum(x["memory_harmful"] for x in details if x["group"] == name),
                "retrieved": sum(x["memory_retrieved"] for x in details if x["group"] == name),
                "used": sum(x["memory_used"] for x in details if x["group"] == name),
            }
            for name, _ in factories
        },
        "memory_unit": memory_unit,
        "successes": sum(int(x["success"]) for x in details),
        "effective": sum(x["memory_effective"] for x in details),
        "harmful": sum(x["memory_harmful"] for x in details),
    }


def _protocol_roundtrip() -> tuple[bool, str]:
    msg = AgentMessage(
        trace_id="trace-scorecard",
        task_id="task-scorecard",
        step_id="s0",
        context_id="ctx-scorecard",
        source_agent="planner",
        target_agent="retriever",
        action_type=ActionType.RETRIEVE,
        capability="retrieval",
        input_parameters={"query": "x", "top_k": 3},
        result={"status": "example"},
    )
    structured = from_wire(to_wire(msg, "structured"), "structured")
    text = from_wire(to_wire(msg, "text"), "text")
    ok = semantically_equivalent(msg, structured) and semantically_equivalent(msg, text)
    return ok, "same AgentMessage rendered/parses in text and structured modes"


_MIN_VIDEO_BYTES = 64 * 1024


def _video_path(root: Path) -> Path:
    """Release-named demo video (opus1), falling back to the numeric-version name."""
    return deliverable_path(root, "video")


def deliverable_path(root: Path, kind: str) -> Path:
    """OS-SOTA1.1: ASCII deliverable names first (Windows zip tools mangle non-UTF-8-flagged
    Chinese names -- the OS-SOTA1 Windows rerun reported the PPT/video as MISSING although both
    were shipped), then the historical Chinese names, release-named before version-named."""
    d = root / "deliverables"
    stem_ascii = {"video": ("demo_video", ".mp4"), "ppt": ("defense_deck", ".pptx")}[kind]
    stem_cn = {"video": ("演示视频", ".mp4"), "ppt": ("答辩PPT", ".pptx")}[kind]
    candidates = [
        d / f"{stem_ascii[0]}_{RELEASE_NAME}{stem_ascii[1]}",
        d / f"{stem_cn[0]}_{RELEASE_NAME}{stem_cn[1]}",
        d / f"{stem_ascii[0]}_v{__version__}{stem_ascii[1]}",
        d / f"{stem_cn[0]}_v{__version__}{stem_cn[1]}",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _is_real_mp4(path: Path) -> bool:
    """A demo video must be an ISO-BMFF container (``ftyp`` box), not a placeholder."""
    try:
        if path.stat().st_size < _MIN_VIDEO_BYTES:
            return False
        with path.open("rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) == 12 and head[4:8] == b"ftyp"


def _deliverable_status(kind: str, path: Path) -> str:
    if not path.exists():
        return "MISSING"
    if kind == "video" and not _is_real_mp4(path):
        return "INVALID(not an MP4 container or too small)"
    if path.is_file() and path.suffix == ".md" and path.stat().st_size < 200:
        return "INVALID(stub document)"
    return "OK"



def run_competition_scorecard(root: str | Path | None = None, *, rounds: int = 10) -> dict[str, Any]:
    root_path = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    cfg = load_config(load_env=False)
    agents = build_default_agents(cfg)
    registry = AgentRegistry()
    handshake_ok = all(registry.handshake(agent.card()).accepted for agent in agents.values())
    capability_contract_ok = all(
        card.capability_specs
        and set(spec.name for spec in card.capability_specs) == set(card.capabilities)
        and all(spec.description.strip() and spec.input_schema and spec.output_schema for spec in card.capability_specs)
        for card in registry.all_cards()
    )

    required_roles = {"planner", "retriever", "executor", "reviewer"}
    required_role_capabilities = {"task_planning", "retrieval", "execute", "summarize"}
    agent_names = set(agents)
    role_capability_coverage = {
        capability: registry.discover(capability)
        for capability in sorted(required_role_capabilities)
    }
    fields = set(AgentMessage.model_fields)
    required_protocol_fields = {"action_type", "input_parameters", "result", "capability"}
    required_memory_fields = {"memory_id", "source_agent", "created_at", "task_topic", "summary"}
    required_metrics = {
        "message_count", "text_tokens", "text_chars", "state_transfers", "state_bytes",
        "latency_sec", "memory_retrieved", "memory_used", "memory_effective",
        "ipc_frames", "ipc_framed_bytes", "ipc_verified", "ipc_socket_calls", "artifact_bytes",
        "state_shm_transfers", "memory_fast_path_hits",
    }

    proto_ok, proto_evidence = _protocol_roundtrip()

    # Same-task communication comparison on the OS-oriented contest workload.  Procedures
    # are self-contained here so Text / Structured / Structured+State all solve the same tasks.
    contest_workload = run_contest_workload()
    comm = contest_workload["communication"]
    same_success = all(v["task_success"] == 1.0 for v in comm.values())
    token_saving_b = 1 - comm["S"]["text_tokens"] / max(1.0, comm["T"]["text_tokens"])
    wire_saving_b = 1 - comm["S"]["wire_bytes"] / max(1.0, comm["T"]["wire_bytes"])
    token_saving_c = 1 - comm["SS"]["text_tokens"] / max(1.0, comm["T"]["text_tokens"])
    wire_saving_c = 1 - comm["SS"]["wire_bytes"] / max(1.0, comm["T"]["wire_bytes"])
    transport_saving_c = 1 - comm["SS"]["transport_bytes"] / max(1.0, comm["T"]["transport_bytes"])
    contest_memory = contest_workload["memory"]["M1"]["memory_summary"]
    kernel_ipc_expected = hasattr(socket, "AF_UNIX")
    real_ipc_ok = (
        all(row["ipc_frames"] == row["message_count"] == row["ipc_verified"] and row["ipc_socket_calls"] >= row["ipc_frames"] + 1 for row in comm.values())
        if kernel_ipc_expected
        else False
    )
    shm_state_ok = (comm["SS"]["state_shm_transfers"] > 0) if kernel_ipc_expected else False
    adaptive_state_ok = (
        comm["SS"]["state_int8_transfers"] + comm["SS"]["state_float16_transfers"] + comm["SS"].get("state_int4_transfers", 0)
    ) > 0

    groups = _run_two_related_groups()
    memory_accuracy = run_memory_accuracy_benchmark()
    stability = run_stability_benchmark(rounds=rounds)

    oe_ok, oe_detail = _is_target_openeuler()
    runtime_env = _env_info()
    native_linux_gate = runtime_env.get("platform_class") == "linux"
    required_deliverables = {
        "source": root_path / "src",
        "design": root_path / "docs" / "DESIGN.md",
        "deployment": root_path / "docs" / "DEPLOYMENT.md",
        "experiment_report": root_path / "docs" / "EXPERIMENT_REPORT.md",
        "video": _video_path(root_path),
    }
    deliverable_status = {k: _deliverable_status(k, p) for k, p in required_deliverables.items()}
    deliverables_ok = all(v == "OK" for v in deliverable_status.values())

    checks = [
        Check(
            "agents",
            "不少于3个Agent，覆盖规划/检索/执行/总结角色能力",
            len(agents) >= 3
            and required_roles <= agent_names
            and all(role_capability_coverage.values()),
            f"agents={sorted(agent_names)}; role capabilities={role_capability_coverage}",
        ),
        Check(
            "handshake",
            "结构化协议支持握手与能力发现",
            handshake_ok and all(role_capability_coverage.values()),
            f"handshake={handshake_ok}; capability discovery={role_capability_coverage}",
        ),
        Check("capability_contract", "能力描述包含机器可读描述及输入/输出schema", capability_contract_ok, f"cards={len(registry.all_cards())}; detailed_specs={sum(len(c.capability_specs) for c in registry.all_cards())}"),
        Check("protocol_fields", "通信至少含动作、输入参数、返回结果、能力描述", required_protocol_fields <= fields and capability_contract_ok, f"message fields={sorted(required_protocol_fields)}; capability id resolves to detailed handshake contract"),
        Check("dual_mode", "同时支持纯文本与结构化协议，且相同任务可复现对比", proto_ok and same_success, f"{proto_evidence}; OS T/S/SS success=100%"),
        Check(
            "communication_metrics",
            "展示消息、token/字符等通信开销，且结构化协议对精简文本基线有正向收益",
            comm["T"]["message_count"] > 0
            and comm["T"]["wire_bytes"] > 0
            and same_success
            and token_saving_b > 0
            and wire_saving_b > 0
            and token_saving_c > 0
            and transport_saving_c > 0
            and real_ipc_ok,
            f"Structured token/wire saving={token_saving_b:.1%}/{wire_saving_b:.1%}; "
            f"Structured+State codec payload={wire_saving_c:.1%}; full control+state+artifact={transport_saving_c:.1%}; "
            f"verified UDS frames={int(comm['SS']['ipc_verified'])}/{int(comm['SS']['message_count'])}; artifact={int(comm['SS']['artifact_bytes'])}B",
        ),
        Check(
            "platform",
            "核心工作负载在原生 Linux 上运行，且真实 UDS/SHM 路径已启用",
            native_linux_gate and real_ipc_ok and shm_state_ok,
            f"platform_class={runtime_env.get('platform_class')}; "
            f"verified_frames={int(comm['SS']['ipc_verified'])}/{int(comm['SS']['message_count'])}; "
            f"shm_handoffs={int(comm['SS']['state_shm_transfers'])}",
        ),
        Check(
            "ipc_integrity",
            "真实UDS控制面逐帧跨进程并通过SHA-256无损重编码校验",
            real_ipc_ok,
            f"T/S/SS verified={int(comm['T']['ipc_verified'])}/{int(comm['S']['ipc_verified'])}/{int(comm['SS']['ipc_verified'])}; socket calls SS={int(comm['SS']['ipc_socket_calls'])}",
        ),
        Check(
            "artifact_accounting",
            "严格总物理传输包含内容寻址Artifact数据面",
            all("artifact_bytes" in row and "full_transport_bytes" in row for row in comm.values())
            and comm["SS"]["artifact_bytes"] > 0,
            f"T/S/SS artifact bytes={int(comm['T']['artifact_bytes'])}/{int(comm['S']['artifact_bytes'])}/{int(comm['SS']['artifact_bytes'])}; full SS={int(comm['SS']['full_transport_bytes'])}B",
        ),
        Check(
            "state",
            "非文本中间状态直接传递并统计次数与规模",
            comm["SS"]["state_transfers"] > 0 and comm["SS"]["state_bytes"] > 0,
            f"transfers={int(comm['SS']['state_transfers'])}; physical={int(comm['SS']['state_bytes'])}B; "
            f"logical={int(comm['SS']['state_logical_bytes'])}B; materialization saving={comm['SS']['state_physical_saving_vs_logical']:.1%}",
        ),
        Check(
            "state_innovation_evidence",
            "状态传递包含快照/增量版本链、去重与引用化数据面证据",
            comm["SS"]["versioned_state_snapshots"] > 0
            and comm["SS"]["versioned_state_delta_updates"] > 0
            and comm["SS"]["state_physical_saving_vs_logical"] > 0
            and comm["SS"]["placement_reference"] > 0
            and adaptive_state_ok
            and shm_state_ok,
            f"snapshots/deltas={int(comm['SS']['versioned_state_snapshots'])}/{int(comm['SS']['versioned_state_delta_updates'])}; "
            f"delta ratio={comm['SS']['versioned_delta_ratio']:.1%}; dedup hits={int(comm['SS']['state_dedup_hits'])}; "
            f"reference placement={comm['SS']['reference_placement_rate']:.1%}; "
            f"adaptive int4(certified)/int8/float16={int(comm['SS'].get('state_int4_transfers', 0))}/{int(comm['SS']['state_int8_transfers'])}/{int(comm['SS']['state_float16_transfers'])}; "
            f"SHM handoffs={int(comm['SS']['state_shm_transfers'])}",
        ),
        Check("memory_metadata", "共享记忆包含规定基本元数据", required_memory_fields <= set(MemoryRecord.model_fields), f"fields={sorted(required_memory_fields)}"),
        Check("memory_retrieval", "支持关键词、标签和语义相似度检索", all(memory_accuracy["summary"][mode]["recall_at_3"] > 0 for mode in ("semantic", "keyword", "tag")), f"semantic={memory_accuracy['summary']['semantic']}; keyword={memory_accuracy['summary']['keyword']}; tag={memory_accuracy['summary']['tag']}; hybrid={memory_accuracy['summary']['hybrid']}"),
        Check("memory_unit_content", "统一记忆单元可沉淀摘要、策略与证据引用", groups["memory_unit"]["total"] >= 4 and groups["memory_unit"]["with_summary"] == groups["memory_unit"]["total"] and groups["memory_unit"]["with_strategy"] >= 4 and groups["memory_unit"]["with_artifact_refs"] >= 4, f"memory_unit={groups['memory_unit']}"),
        Check(
            "multi_step_task",
            "复杂任务经过规划/检索/执行/总结多步骤协作",
            all(
                set(x["pipeline_agents"]) == {"planner", "retriever", "executor", "reviewer"}
                and x["message_count"] >= (5 if x.get("pipeline_topology") == "forward" else 9)
                for x in groups["runs"]
            ),
            f"tasks={len(groups['runs'])}; request_reply>=9, forward>=5",
        ),
        Check(
            "related_groups",
            "至少3组关联性连续任务，其中一组为三步长链并包含对抗负迁移",
            len(groups["groups"]) >= 3
            and any(v["task_count"] >= 3 for v in groups["groups"].values())
            and groups["successes"] == sum(v["task_count"] for v in groups["groups"].values())
            and groups["harmful"] == 0
            and groups["groups"]["openeuler-kernel-tuning"]["effective"] >= 2
            and groups["groups"]["openeuler-abi-adversarial"]["retrieved"] > 0
            and groups["groups"]["openeuler-abi-adversarial"]["harmful"] == 0,
            f"groups={len(groups['groups'])}; successes={groups['successes']}/{sum(v['task_count'] for v in groups['groups'].values())}; "
            f"kernel-chain-effective={groups['groups']['openeuler-kernel-tuning']['effective']}; "
            f"adversarial-candidates={groups['groups']['openeuler-abi-adversarial']['retrieved']}; "
            f"query hit={contest_memory['query_hit_rate']:.1%}; negative transfer={contest_memory['negative_transfer_rate']:.1%}",
        ),
        Check("metrics", "统计消息、token/字符、状态、Artifact、单任务总耗时、Memory命中等指标", required_metrics <= set(MetricsSnapshot.__dataclass_fields__) and comm["SS"].get("mean_task_latency_sec", 0) > 0, f"required metrics present={len(required_metrics)}; OS mean task latency={comm['SS'].get('mean_task_latency_sec', 0):.4f}s; warm sandbox p50={contest_workload['sandbox_latency']['micro']['warm']['p50_ms']:.2f}ms"),
        Check("architecture", "包含运行时/协议调度/状态/Memory/评测模块", all((root_path / p).exists() for p in ["src/agent_runtime/runtime", "src/agent_runtime/protocol", "src/agent_runtime/scheduler", "src/agent_runtime/state_exchange", "src/agent_runtime/memory", "src/agent_runtime/evaluation"]), "required module directories present"),
        Check("stability", "稳定执行不少于10轮连续任务", stability["summary"]["success_count"] == rounds and stability["summary"]["error_count"] == 0, f"{stability['summary']['success_count']}/{rounds} success; RSS growth={stability['summary']['rss_growth_mb']:+.2f}MB; errors={stability['summary']['error_count']}"),
        Check("codeact", "CodeAct轻量沙箱（鼓励项）", (root_path / "src/agent_runtime/sandbox/executor.py").exists(), "sandbox executor present", mandatory=False),
        Check("codeact_kernel_guard", "CodeAct 沙箱内核级隔离（seccomp-BPF + Landlock，鼓励项）", _kernel_guard_passed(root_path), _kernel_guard_evidence(root_path), mandatory=False),
        Check("presentation", "答辩PPT与结果驱动交付构建器（增强交付项）",
              deliverable_path(root_path, "ppt").exists() and any((root_path / "deliverables" / name).exists() for name in ("build_deliverables.mjs", "build_deliverables.py")),
              ("PPT + result-driven builder present: " + deliverable_path(root_path, "ppt").name)
              if deliverable_path(root_path, "ppt").exists() and any((root_path / "deliverables" / name).exists() for name in ("build_deliverables.mjs", "build_deliverables.py"))
              else f"MISSING: deliverables/defense_deck_{RELEASE_NAME}.pptx (or 答辩PPT_*) and/or deliverables/build_deliverables.py",
              mandatory=False),
        Check("deliverables", "源码/设计/部署/实验报告/演示视频完整", deliverables_ok, "; ".join(f"{k}={v}" for k, v in deliverable_status.items())),
        Check("openeuler", "最终代码在 openEuler 24.03-LTS-SP3 编译、运行、测试", oe_ok, oe_detail),
    ]

    env_ids = _environment_check_ids()
    mandatory_non_env = [c for c in checks if c.mandatory and c.id not in env_ids]
    return {
        "suite": "competition_scorecard",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "env": runtime_env,
        "host": {"platform": platform.platform(), "openeuler_target_verified": oe_ok, "openeuler_detail": oe_detail},
        "checks": [asdict(c) for c in checks],
        "environment_check_ids": sorted(env_ids),
        "summary": {
            "mandatory_non_environment_passed": sum(int(c.passed) for c in mandatory_non_env),
            "mandatory_non_environment_total": len(mandatory_non_env),
            "all_non_environment_mandatory_passed": all(c.passed for c in mandatory_non_env),
            "openeuler_target_verified": oe_ok,
            "communication": {
                "structured_token_saving_vs_text": token_saving_b,
                "structured_wire_saving_vs_text": wire_saving_b,
                "structured_physical_control_saving_vs_text": 1 - comm["S"]["control_physical_bytes"] / max(1.0, comm["T"]["control_physical_bytes"]),
                "state_ref_token_saving_vs_text": token_saving_c,
                "state_ref_control_wire_saving_vs_text": wire_saving_c,
                "state_ref_physical_control_saving_vs_text": 1 - comm["SS"]["control_physical_bytes"] / max(1.0, comm["T"]["control_physical_bytes"]),
                "state_ref_control_state_saving_vs_text": 1 - comm["SS"]["control_state_transport_bytes"] / max(1.0, comm["T"]["control_state_transport_bytes"]),
                "state_ref_full_physical_saving_vs_text": transport_saving_c,
                "state_ref_total_transport_saving_vs_text": transport_saving_c,
            },
            "contest_memory": contest_memory,
            "memory_retrieval": memory_accuracy["summary"],
            "related_groups": {k: v for k, v in groups.items() if k != "runs"},
            "stability": stability["summary"],
        },
        "details": {"contest_workload": contest_workload, "communication_runs": comm, "related_groups": groups, "memory_accuracy": memory_accuracy, "stability": stability},
    }


def render_competition_report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        f"# Competition Requirement Scorecard — v{__version__}",
        "",
        "This is a requirement-verification matrix, **not a predicted judge score**.",
        "",
        f"- Non-environment mandatory checks: **{s['mandatory_non_environment_passed']}/{s['mandatory_non_environment_total']} PASS**",
        f"- openEuler 24.03-LTS-SP3 target-host verification: **{'PASS' if s['openeuler_target_verified'] else 'PENDING'}**",
        "",
        "| Requirement | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for check in payload["checks"]:
        status = "PASS" if check["passed"] else ("PENDING" if check["id"] in payload.get("environment_check_ids", ["openeuler"]) else "FAIL")
        if not check["passed"] and payload.get("env", {}).get("platform_class") != "linux" and check["id"] in {"platform", "ipc_integrity", "state_innovation_evidence", "communication_metrics", "codeact_kernel_guard"}:
            status = "UNSUPPORTED"
        lines.append(f"| {check['requirement']} | **{status}** | {check['evidence']} |")
    c = s["communication"]
    lines += [
        "",
        "## Official-score evidence snapshot",
        "",
        f"- Structured protocol saving vs lean text: **token {c['structured_token_saving_vs_text']:.1%} / codec payload {c['structured_wire_saving_vs_text']:.1%} / kernel-framed control {c['structured_physical_control_saving_vs_text']:.1%}**",
        f"- Structured + state-ref token saving vs text: **{c['state_ref_token_saving_vs_text']:.1%}**",
        f"- Structured + state-ref control payload / kernel-framed control saving vs text: **{c['state_ref_control_wire_saving_vs_text']:.1%} / {c['state_ref_physical_control_saving_vs_text']:.1%}**",
        f"- Structured + state-ref full physical transport saving (verified control + state + artifact) vs text: **{c['state_ref_total_transport_saving_vs_text']:.1%}**",
        f"- 10+ round stability: **{s['stability']['success_count']}/{payload['details']['stability']['rounds']} success**, errors={s['stability']['error_count']}, RSS growth={s['stability']['rss_growth_mb']:+.2f} MB",
        f"- Related task groups ({len(s['related_groups']['groups'])} groups): successes={s['related_groups']['successes']}/{sum(v['task_count'] for v in s['related_groups']['groups'].values())}; follow-up effective reuse={s['contest_memory']['followup_effective_reuse_rate']:.1%}; adoption precision={s['contest_memory']['adoption_precision']:.1%}; negative transfer={s['contest_memory']['negative_transfer_rate']:.1%}",
        "",
        f"Source SHA-256: `{payload['env']['source_tree_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def write_competition_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "competition_scorecard")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_competition_report(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps({"generated_at_utc": payload["generated_at_utc"], "suite": payload["suite"], "environment": payload["env"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    return out


def run_competition_scorecard_cli(args) -> int:
    payload = run_competition_scorecard(rounds=args.rounds)
    out = write_competition_results(payload)
    print(render_competition_report(payload))
    print(f"\n结果已保存到: {out}")
    if getattr(args, "require_openeuler", False) and not payload["summary"]["openeuler_target_verified"]:
        return 2
    return 0 if payload["summary"]["all_non_environment_mandatory_passed"] else 1


__all__ = ["run_competition_scorecard", "write_competition_results", "run_competition_scorecard_cli"]
