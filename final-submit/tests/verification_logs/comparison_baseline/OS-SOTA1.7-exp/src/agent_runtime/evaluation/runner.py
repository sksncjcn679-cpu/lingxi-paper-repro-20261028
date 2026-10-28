"""基准实验运行器。

运行 4 组主实验（A 文本基线 / B 结构化 / C 结构化+状态 / D 全系统）与若干消融，
统计 16 项指标 + 派生指标，按时间戳落盘到 `results/<ts>-<suite>/`（不覆盖旧实验）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_runtime.config import load_config
from agent_runtime.evaluation.stats import summarize_runs
from agent_runtime.memory.store import MemoryStore
from agent_runtime.observability.metrics import MetricsSnapshot
from agent_runtime.providers.factory import build_embedding
from agent_runtime.providers.tokenizer import get_tokenizer
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.runtime.task import Task
from agent_runtime.sandbox.capabilities import summary as capability_summary

SUM_KEYS = [
    "routine_uploads",
    "routine_upload_tokens",
    "routine_upload_bytes",
    "routine_references",
    "cphc_predicted_fields",
    "cphc_explicit_fields",
    "cphc_reference_hits",
    "ipc_pipelined_frames",
    "ipc_barrier_wait_sec",
    "ipc_barrier_requests",
    "plan_cache_hits",
    "state_int4_transfers",
    "state_q4block_transfers",
    "state_decision_certificates",
    "plan_cache_misses",
    "message_count",
    "text_tokens",
    "text_chars",
    "wire_bytes",
    "binary_frames",
    "session_frames",
    "ipc_frames",
    "ipc_payload_bytes",
    "ipc_framed_bytes",
    "ipc_ack_bytes",
    "ipc_roundtrip_sec",
    "ipc_socket_calls",
    "ipc_verified",
    "artifact_bytes",
    "state_transfers",
    "state_bytes",
    "state_logical_bytes",
    "state_dedup_hits",
    "state_float32_transfers",
    "state_float16_transfers",
    "state_int8_transfers",
    "state_shm_transfers",
    "state_inproc_transfers",
    "versioned_state_transfers",
    "versioned_state_bytes",
    "versioned_state_logical_bytes",
    "versioned_state_delta_updates",
    "versioned_state_snapshots",
    "artifact_refs",
    "placement_decisions",
    "placement_reference",
    "placement_inline",
    "placement_estimated_savings_bytes",
    "llm_calls",
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "llm_cached_prompt_tokens",
    "llm_judgements",
    "llm_judgements_correct",
    "tool_calls",
    "repeated_tool_calls",
    "memory_queries",
    "memory_query_hits",
    "memory_retrieved",
    "memory_incompatible",
    "memory_rejected",
    "memory_used",
    "memory_effective",
    "memory_harmful",
    "memory_conflicts",
    "memory_unattributed",
    "memory_fast_path_probes",
    "memory_fast_path_hits",
    "memory_fast_path_tasks",
    "memory_fast_path_skipped_stages",
    "idempotency_replays",
    "retry_attempts",
    "retry_budget_exhausted",
    "circuit_rejections",
    "error_count",
    "degradation_count",
    "latency_sec",
]
STAT_KEYS = [*SUM_KEYS, "rss_peak_mb", "task_quality", "task_success", "total_llm_tokens"]


@dataclass(frozen=True)
class ExperimentConfig:
    id: str
    label: str
    mode: str
    use_state: bool
    use_memory: bool
    use_sandbox: bool
    # Text-mode fairness control. ``lean`` removes conversational filler while still
    # inlining the complete payload; structured mode ignores this field.
    text_style: str | None = None
    # None 表示使用全局默认；专项 state suite 用于拆分低开销状态机制贡献。
    state_precision: str | None = None
    state_deduplicate: bool | None = None
    state_versioned_delta: bool | None = None
    reference_first_data_plane: bool | None = None
    memory_compatibility_gate: bool | None = None
    memory_fast_path: bool | None = None
    structured_wire_codec: str | None = None
    pipeline_topology: str | None = None
    control_transport: str | None = None
    state_shared_memory: bool | None = None
    state_adaptive_cosine_threshold: float | None = None
    sandbox_warm_start: bool | None = None
    # 某些机制消融会改变执行路径/工作量，即使最终成功率相同也不应比较效率。
    compare_efficiency_to_baseline: bool = True
    # Metric-specific comparability. None inherits compare_efficiency_to_baseline.
    compare_communication_to_baseline: bool | None = None
    compare_latency_to_baseline: bool | None = None
    compare_tools_to_baseline: bool | None = None


MAIN_SUITE = [
    ExperimentConfig("A", "Lean Text Baseline", "text", False, False, False, text_style="lean"),
    ExperimentConfig("B", "Structured Protocol", "structured", False, False, False),
    ExperimentConfig("C", "Structured + State", "structured", True, False, False),
    ExperimentConfig("D", "Full System", "structured", True, True, True, compare_latency_to_baseline=False, compare_tools_to_baseline=False),
]

ABLATION_SUITE = [
    ExperimentConfig("D", "Full System", "structured", True, True, True),
    ExperimentConfig("D-noMem", "−Shared Memory", "structured", True, False, True),
    ExperimentConfig("D-noState", "−State Exchange", "structured", False, True, True),
    ExperimentConfig("D-noSandbox", "−CodeAct Sandbox", "structured", True, True, False),
    ExperimentConfig("B", "Structured only", "structured", False, False, False),
    ExperimentConfig("A", "Lean Text Baseline", "text", False, False, False, text_style="lean"),
]

# Explicitly quantify how sensitive protocol-only conclusions are to text rendering style.
# The lean row is the conservative baseline used by MAIN_SUITE; verbose is retained only as a
# diagnostic/historical comparison, not as the sole evidence for structured-protocol savings.
TEXT_FAIRNESS_SUITE = [
    ExperimentConfig("TV", "Verbose Natural-Language Text", "text", False, False, False, text_style="verbose"),
    ExperimentConfig("TL", "Lean Full-Payload Text", "text", False, False, False, text_style="lean"),
    ExperimentConfig("B", "Structured Protocol", "structured", False, False, False),
    ExperimentConfig("C", "Structured + State", "structured", True, False, False),
]

MEMORY_SUITE = [
    ExperimentConfig("M0", "No Shared Memory", "structured", True, False, True),
    ExperimentConfig("M1", "Shared Memory", "structured", True, True, True),
]

# 状态传递专项消融：保持任务/Agent/协议不变，只切换状态物化策略。
# S0 = 历史 float32 + 重复物化；S1 = 内容寻址去重；S2 = 去重 + float16；
# S3 = 去重 + per-vector symmetric int8 transport（含实际 scale payload）。
STATE_SUITE = [
    ExperimentConfig(
        "S0", "State float32 / no dedup", "structured", True, False, False,
        state_precision="float32", state_deduplicate=False, reference_first_data_plane=False,
    ),
    ExperimentConfig(
        "S1", "State float32 / dedup", "structured", True, False, False,
        state_precision="float32", state_deduplicate=True, reference_first_data_plane=False,
    ),
    ExperimentConfig(
        "S2", "State float16 / dedup", "structured", True, False, False,
        state_precision="float16", state_deduplicate=True, reference_first_data_plane=False,
    ),
    ExperimentConfig(
        "S3", "State int8 / dedup", "structured", True, False, False,
        state_precision="int8", state_deduplicate=True, reference_first_data_plane=False,
    ),
]

# Reference-first control/data-plane ablation. Keep all other runtime semantics identical.
REFERENCE_FIRST_SUITE = [
    ExperimentConfig(
        "R0",
        "Structured + State / legacy inline producer replies",
        "structured",
        True,
        False,
        False,
        reference_first_data_plane=False,
    ),
    ExperimentConfig(
        "R1",
        "Structured + State / producer reference-first",
        "structured",
        True,
        False,
        False,
        reference_first_data_plane=True,
        compare_latency_to_baseline=False,
    ),
]

VERSIONED_PIPELINE_SUITE = [
    ExperimentConfig(
        "V0",
        "Main pipeline / full workflow snapshots",
        "structured",
        True,
        False,
        False,
        state_versioned_delta=False,
        reference_first_data_plane=True,
    ),
    ExperimentConfig(
        "V1",
        "Main pipeline / versioned workflow deltas",
        "structured",
        True,
        False,
        False,
        state_versioned_delta=True,
        reference_first_data_plane=True,
    ),
]

MEMORY_GUARD_SUITE = [
    ExperimentConfig(
        "G0",
        "Memory compatibility gate OFF",
        "structured",
        True,
        True,
        True,
        memory_compatibility_gate=False,
    ),
    ExperimentConfig(
        "G1",
        "Memory compatibility gate ON",
        "structured",
        True,
        True,
        True,
        memory_compatibility_gate=True,
        compare_efficiency_to_baseline=False,
    ),
]


def _default_task_factory() -> list[Task]:
    """主实验任务序列：两个相同任务，便于体现记忆复用。"""
    from agent_runtime.scenarios.demo import make_demo_task

    return [make_demo_task(), make_demo_task()]


def _memory_task_factory() -> list[Task]:
    """过程性记忆迁移任务：A1→A2 与 B1→B2 两组受控软件改造。"""
    from agent_runtime.scenarios.task_group_a import make_group as make_group_a
    from agent_runtime.scenarios.task_group_b import make_group as make_group_b

    return [*make_group_a(), *make_group_b()]


def _memory_guard_task_factory() -> list[Task]:
    """负迁移受控任务：先写入 A 类策略，再执行不兼容 B2。

    B2 故意不给正确策略，因此门控开启后应“拒绝而失败”；关闭门控后会
    误用 A 类策略并由 Reviewer 记为 harmful。该实验只验证防错，不把失败早停
    解释为性能收益。
    """
    from agent_runtime.scenarios.task_group_a import make_a1
    from agent_runtime.scenarios.task_group_b import make_b2

    a1 = make_a1()
    return [a1, make_b2(parent_task_id=a1.task_id)]




def _reference_first_task_factory() -> list[Task]:
    """Large deterministic state/evidence task used to expose placement break-even."""
    n = 128
    facts = {f"k{i}": i for i in range(n)}
    operands = list(facts)
    return [
        Task(
            topic="reference-first-large-sum",
            description="Sum a large set of facts for reference placement evaluation.",
            payload={"facts": facts, "question": "sum", "operands": operands},
            expected={"answer": sum(facts.values())},
        )
    ]


def _aggregate(snaps: list[MetricsSnapshot]) -> dict[str, Any]:
    agg: dict[str, Any] = dict.fromkeys(SUM_KEYS, 0.0)
    rss = 0.0
    quals: list[float] = []
    success = True
    for s in snaps:
        for k in SUM_KEYS:
            agg[k] += float(getattr(s, k))
        rss = max(rss, s.rss_peak_mb)
        quals.append(s.task_quality)
        success = success and s.task_success
    agg["rss_peak_mb"] = rss
    agg["task_quality"] = sum(quals) / len(quals) if quals else 0.0
    agg["task_success"] = 1.0 if success else 0.0
    agg["total_llm_tokens"] = agg["llm_prompt_tokens"] + agg["llm_completion_tokens"]
    return agg


def run_once(
    cfg_exp: ExperimentConfig,
    task_factory: Callable[[], list[Task]],
    seed: int,
    *,
    tokenizer_backend: str | None = None,
) -> dict:
    overrides: dict[str, Any] = {"random_seed": seed}
    if tokenizer_backend is not None:
        overrides["tokenizer_backend"] = tokenizer_backend
    if cfg_exp.state_precision is not None:
        overrides["state_precision"] = cfg_exp.state_precision
    if cfg_exp.state_deduplicate is not None:
        overrides["state_deduplicate"] = cfg_exp.state_deduplicate
    if cfg_exp.state_versioned_delta is not None:
        overrides["state_versioned_delta"] = cfg_exp.state_versioned_delta
    if cfg_exp.reference_first_data_plane is not None:
        overrides["reference_first_data_plane"] = cfg_exp.reference_first_data_plane
    if cfg_exp.memory_compatibility_gate is not None:
        overrides["memory_compatibility_gate"] = cfg_exp.memory_compatibility_gate
    if cfg_exp.memory_fast_path is not None:
        overrides["memory_fast_path"] = cfg_exp.memory_fast_path
    if cfg_exp.structured_wire_codec is not None:
        overrides["structured_wire_codec"] = cfg_exp.structured_wire_codec
    if cfg_exp.pipeline_topology is not None:
        overrides["pipeline_topology"] = cfg_exp.pipeline_topology
    if cfg_exp.control_transport is not None:
        overrides["control_transport"] = cfg_exp.control_transport
    if cfg_exp.state_shared_memory is not None:
        overrides["state_shared_memory"] = cfg_exp.state_shared_memory
    if cfg_exp.state_adaptive_cosine_threshold is not None:
        overrides["state_adaptive_cosine_threshold"] = cfg_exp.state_adaptive_cosine_threshold
    if cfg_exp.text_style is not None:
        overrides["text_style"] = cfg_exp.text_style
    config = load_config(load_env=False, **overrides)
    if cfg_exp.sandbox_warm_start is not None:
        from dataclasses import replace
        config = config.with_overrides(sandbox=replace(config.sandbox, warm_start=cfg_exp.sandbox_warm_start))
    tasks = task_factory()
    # Protocol 1.4: every measured run starts with an empty client-side routine registry, so
    # routine learning and the one-time routine upload are part of the measured cost.
    from agent_runtime.protocol.routine import reset_routine_registry

    reset_routine_registry()
    memory = MemoryStore(embedding=build_embedding(config)) if cfg_exp.use_memory else None
    snaps: list[MetricsSnapshot] = []
    for task in tasks:
        ctx = build_context(
            config,
            cfg_exp.mode,
            task_id=task.task_id,
            use_memory=cfg_exp.use_memory,
            use_state_exchange=cfg_exp.use_state,
            use_sandbox=cfg_exp.use_sandbox,
            memory=memory,
        )
        orch = Orchestrator(build_default_agents(config))
        result = asyncio.run(orch.run_task(task, ctx))
        snaps.append(result.metrics)
    return _aggregate(snaps)


def run_suite(
    suite: str = "main",
    repeat: int = 5,
    seed: int = 42,
    task_factory: Callable[[], list[Task]] | None = None,
    *,
    tokenizer_backend: str | None = None,
) -> dict[str, Any]:
    suites = {
        "main": MAIN_SUITE,
        "ablation": ABLATION_SUITE,
        "memory": MEMORY_SUITE,
        "memory_guard": MEMORY_GUARD_SUITE,
        "state": STATE_SUITE,
        "reference_first": REFERENCE_FIRST_SUITE,
        "text_fairness": TEXT_FAIRNESS_SUITE,
        "versioned_pipeline": VERSIONED_PIPELINE_SUITE,
    }
    if suite not in suites:
        raise ValueError(f"unknown suite: {suite}")
    configs = suites[suite]
    if task_factory is not None:
        factory = task_factory
    elif suite == "memory":
        factory = _memory_task_factory
    elif suite == "memory_guard":
        factory = _memory_guard_task_factory
    elif suite == "reference_first":
        factory = _reference_first_task_factory
    elif suite == "versioned_pipeline":
        factory = _reference_first_task_factory
    else:
        factory = _default_task_factory

    results: dict[str, Any] = {}
    for cfg_exp in configs:
        runs = [
            run_once(
                cfg_exp,
                factory,
                seed + i,
                tokenizer_backend=tokenizer_backend,
            )
            for i in range(repeat)
        ]
        results[cfg_exp.id] = {
            "label": cfg_exp.label,
            "config": asdict(cfg_exp),
            "summary": summarize_runs(runs, STAT_KEYS),
            "raw": runs,
        }

    baseline_id = configs[0].id
    _attach_derived(results, baseline_id=baseline_id)
    return {
        "suite": suite,
        "repeat": repeat,
        "seed": seed,
        "baseline_id": baseline_id,
        "env": _env_info(tokenizer_backend=tokenizer_backend),
        "results": results,
    }


def _attach_derived(results: dict[str, Any], *, baseline_id: str) -> None:
    baseline = results[baseline_id]
    base_tokens = baseline["summary"]["text_tokens"]["mean"] or 1.0
    base_wire_bytes = baseline["summary"]["wire_bytes"]["mean"] or 1.0
    base_latency = baseline["summary"]["latency_sec"]["mean"] or 1.0
    base_tools = baseline["summary"]["tool_calls"]["mean"] or 1.0
    base_repeated = baseline["summary"]["repeated_tool_calls"]["mean"]
    base_state_bytes = baseline["summary"]["state_bytes"]["mean"]
    base_success = round(baseline["summary"]["task_success"]["mean"], 4)
    for cid, r in results.items():
        s = r["summary"]
        used = s["memory_used"]["mean"]
        effective = s["memory_effective"]["mean"]
        repeated = s["repeated_tool_calls"]["mean"]
        success_rate = round(s["task_success"]["mean"], 4)

        # Efficiency ratios are only meaningful when compared configurations achieve
        # the same task success.  Otherwise a configuration that aborts early can look
        # artificially "faster" or "cheaper".  The baseline is always comparable to
        # itself; capability deltas are reported separately.
        cfg = r.get("config", {})
        declared_comparable = bool(cfg.get("compare_efficiency_to_baseline", True))
        def _metric_declared(name: str) -> bool:
            value = cfg.get(name)
            return declared_comparable if value is None else bool(value)

        success_comparable = success_rate == base_success
        communication_comparable = cid == baseline_id or (success_comparable and _metric_declared("compare_communication_to_baseline"))
        latency_comparable = cid == baseline_id or (success_comparable and _metric_declared("compare_latency_to_baseline"))
        tool_comparable = cid == baseline_id or (success_comparable and _metric_declared("compare_tools_to_baseline"))
        efficiency_comparable = communication_comparable and latency_comparable and tool_comparable
        token_saving = (
            round(1 - s["text_tokens"]["mean"] / base_tokens, 4)
            if communication_comparable
            else None
        )
        wire_byte_saving = (
            round(1 - s["wire_bytes"]["mean"] / base_wire_bytes, 4)
            if communication_comparable
            else None
        )
        latency_improvement = (
            round(1 - s["latency_sec"]["mean"] / base_latency, 4)
            if latency_comparable
            else None
        )
        tool_reduction = (
            round(1 - s["tool_calls"]["mean"] / base_tools, 4)
            if tool_comparable
            else None
        )
        r["derived"] = {
            "baseline_id": baseline_id,
            "efficiency_comparable": efficiency_comparable,
            "communication_comparable": communication_comparable,
            "latency_comparable": latency_comparable,
            "tool_comparable": tool_comparable,
            "success_rate": success_rate,
            "success_delta_vs_baseline": round(success_rate - base_success, 4),
            "token_saving_vs_baseline": token_saving,
            "wire_byte_saving_vs_baseline": wire_byte_saving,
            "latency_improvement_vs_baseline": latency_improvement,
            "tool_call_reduction_vs_baseline": tool_reduction,
            # 只有基线确实存在重复调用且成功率可比时才定义“重复计算降低率”，
            # 避免用总工具调用数冒充重复调用指标，也避免早停造成虚假收益。
            "repeated_calc_reduction_vs_baseline": (
                round(1 - repeated / base_repeated, 4)
                if tool_comparable and base_repeated
                else None
            ),
            "effective_hit_rate": round(effective / used, 4) if used else 0.0,
            "state_materialization_saving": (
                round(1 - s["state_bytes"]["mean"] / s["state_logical_bytes"]["mean"], 4)
                if s["state_logical_bytes"]["mean"]
                else 0.0
            ),
            "state_bytes_reduction_vs_baseline": (
                round(1 - s["state_bytes"]["mean"] / base_state_bytes, 4)
                if communication_comparable and base_state_bytes
                else None
            ),
        }
        # Backward-compatible aliases for existing main-suite consumers.
        if baseline_id == "A":
            r["derived"]["token_saving_vs_A"] = token_saving
            r["derived"]["wire_byte_saving_vs_A"] = wire_byte_saving
            r["derived"]["latency_improvement_vs_A"] = latency_improvement


def _env_info(*, tokenizer_backend: str | None = None) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    cfg = load_config(
        load_env=False,
        **({"tokenizer_backend": tokenizer_backend} if tokenizer_backend is not None else {}),
    )
    tokenizer = get_tokenizer(model=cfg.llm.model, backend=cfg.tokenizer_backend)
    packages: dict[str, str] = {}
    for dist in ("pydantic", "numpy", "psutil", "PyYAML"):
        try:
            packages[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            packages[dist] = "not-installed"
    return {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "platform_class": _platform_class(),
        "machine": platform.machine(),
        "host_identity": hashlib.sha256(platform.node().encode("utf-8")).hexdigest(),
        "packages": packages,
        "capabilities": capability_summary(),
        "communication_tokenizer": tokenizer.name,
        "tokenizer_backend_requested": cfg.tokenizer_backend,
        "source_tree_sha256": _source_tree_sha256(root),
        "git_commit": _git_commit(root),
    }


def _platform_class() -> str:
    """Classify the execution kernel for evidence gates.

    WSL is deliberately separate from native Linux: it provides useful development
    evidence, but it is not an independent openEuler/native-Linux certification.
    """
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        release = platform.release().lower()
        return "wsl" if "microsoft" in release or "wsl" in release else "linux"
    return "other"


def _source_tree_sha256(root: Path) -> str:
    """Hash runtime, scenarios, model/dataset files, run scripts and packaging config."""
    candidates = [root / "pyproject.toml"]
    for folder in (root / "src", root / "scenarios", root / "scripts"):
        if folder.is_dir():
            candidates.extend(sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix in {".py", ".sh", ".json", ".npz"}))
    h = hashlib.sha256()
    for path in sorted(candidates, key=lambda p: p.relative_to(root).as_posix()):
        rel = str(path.relative_to(root)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _git_commit(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = proc.stdout.strip()
    return commit if proc.returncode == 0 and commit else None


def _allocate_result_dir(root: str, suite: str) -> Path:
    """Atomically allocate a unique result directory; never overwrite evidence."""
    base_root = Path(root)
    base_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    for suffix in range(1000):
        name = f"{stamp}-{suite}" if suffix == 0 else f"{stamp}-{suffix:03d}-{suite}"
        candidate = base_root / name
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        (candidate / "raw").mkdir(exist_ok=False)
        return candidate
    raise RuntimeError("unable to allocate unique benchmark result directory")


def write_results(payload: dict[str, Any], root: str = "results") -> Path:
    out_dir = _allocate_result_dir(root, payload["suite"])

    # 配置快照
    (out_dir / "config.yaml").write_text(
        _to_yaml(
            {
                "suite": payload["suite"],
                "repeat": payload["repeat"],
                "seed": payload["seed"],
                "baseline_id": payload.get("baseline_id"),
                "env": payload["env"],
                "configs": [r["config"] for r in payload["results"].values()],
            }
        ),
        encoding="utf-8",
    )
    # 原始数据
    for cid, r in payload["results"].items():
        for k, run in enumerate(r["raw"]):
            (out_dir / "raw" / f"{cid}_run_{k:02d}.json").write_text(
                json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    # 聚合
    summary = {
        "suite": payload["suite"],
        "repeat": payload["repeat"],
        "seed": payload["seed"],
        "baseline_id": payload.get("baseline_id"),
        "env": payload["env"],
        "results": {
            cid: {
                "label": r["label"],
                "config": r["config"],
                "summary": r["summary"],
                "derived": r["derived"],
            }
            for cid, r in payload["results"].items()
        },
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_report(summary), encoding="utf-8")

    metadata_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "suite": payload["suite"],
        "repeat": payload["repeat"],
        "seed": payload["seed"],
        "baseline_id": payload.get("baseline_id"),
        "environment": payload["env"],
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_checksum_manifest(out_dir)
    return out_dir


def _write_checksum_manifest(out_dir: Path) -> None:
    files = sorted(
        p
        for p in out_dir.rglob("*")
        if p.is_file() and p.name != "MANIFEST.sha256"
    )
    lines = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rel = path.relative_to(out_dir).as_posix()
        lines.append(f"{digest}  {rel}")
    (out_dir / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_report(summary: dict[str, Any]) -> str:
    lines = [
        f"# 实验报告 ({summary['suite']})",
        "",
        f"- 重复次数: {summary['repeat']}　随机种子基: {summary['seed']}",
        f"- 对照基线: {summary.get('baseline_id', 'A')}",
        f"- 环境: Python {summary['env']['python']} / {summary['env']['platform']} / {summary['env']['machine']}",
        f"- 源码 SHA-256: {summary['env'].get('source_tree_sha256', 'legacy-result')}",
        f"- 通信 token 计量器: {summary['env'].get('communication_tokenizer', 'unknown')}（同时记录 tokenizer-independent UTF-8 wire bytes）",
        "",
        "## 关键指标（均值）",
        "",
        "| 配置 | 成功率 | text_tokens | Token节省 | 时延(s) | 时延改善 | 工具调用 | 工具调用降低 | 有效记忆命中率 | RSS峰值(MB) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cid, r in summary["results"].items():
        s = r["summary"]
        d = r["derived"]
        token_saving = d.get("token_saving_vs_baseline", d.get("token_saving_vs_A"))
        latency_improvement = d.get(
            "latency_improvement_vs_baseline", d.get("latency_improvement_vs_A")
        )
        tool_reduction = d.get(
            "tool_call_reduction_vs_baseline", d.get("repeated_calc_reduction_vs_A")
        )
        token_text = f"{token_saving:.1%}" if token_saving is not None else "N/A*"
        latency_text = (
            f"{latency_improvement:.1%}" if latency_improvement is not None else "N/A*"
        )
        tool_text = f"{tool_reduction:.1%}" if tool_reduction is not None else "N/A*"
        lines.append(
            f"| {cid} {r['label']} | {d['success_rate']:.0%} | "
            f"{s['text_tokens']['mean']:.0f} | {token_text} | "
            f"{s['latency_sec']['mean']:.4f} | {latency_text} | "
            f"{s['tool_calls']['mean']:.1f} | {tool_text} | "
            f"{d['effective_hit_rate']:.2f} | {s['rss_peak_mb']['mean']:.1f} |"
        )
    if any(not r["derived"].get("efficiency_comparable", True) for r in summary["results"].values()):
        lines += [
            "",
            "* N/A：该指标与基线成功率或工作量语义不可比。通信、时延、工具计算分别判断可比性；"
            "例如 Full System 仍可比较通信字节/token，但不把额外 Memory/Sandbox 开销误算成通信时延退化。",
        ]
    lines += [
        "",
        "## 通信开销（均值 ± 标准差）",
        "",
        "| 配置 | messages | text_tokens | text_chars | wire_bytes | wire节省 | state_transfers | logical_state_B | materialized_state_B | versioned_B | versioned_delta | dedup_hits | 物化节省 | artifact_refs |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cid, r in summary["results"].items():
        s = r["summary"]
        d = r["derived"]
        wire_saving = d.get("wire_byte_saving_vs_baseline")
        wire_saving_text = f"{wire_saving:.1%}" if wire_saving is not None else "N/A*"
        lines.append(
            f"| {cid} | {s['message_count']['mean']:.0f}±{s['message_count']['std']:.0f} | "
            f"{s['text_tokens']['mean']:.0f}±{s['text_tokens']['std']:.0f} | "
            f"{s['text_chars']['mean']:.0f} | {s['wire_bytes']['mean']:.0f} | {wire_saving_text} | "
            f"{s['state_transfers']['mean']:.1f} | "
            f"{s['state_logical_bytes']['mean']:.0f} | {s['state_bytes']['mean']:.0f} | "
            f"{s['versioned_state_bytes']['mean']:.0f} | {s['versioned_state_delta_updates']['mean']:.1f} | "
            f"{s['state_dedup_hits']['mean']:.1f} | {d['state_materialization_saving']:.1%} | "
            f"{s['artifact_refs']['mean']:.1f} |"
        )
    lines += [
        "",
        "## 记忆复用",
        "",
        "| 配置 | queries | query hits | 查询命中率 | candidates | incompatible | rejected | used | effective | harmful | unattributed | 有效复用率 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cid, r in summary["results"].items():
        s = r["summary"]
        d = r["derived"]
        hit_rate = (s["memory_query_hits"]["mean"] / s["memory_queries"]["mean"] if s["memory_queries"]["mean"] else 0.0)
        lines.append(
            f"| {cid} | {s['memory_queries']['mean']:.1f} | {s['memory_query_hits']['mean']:.1f} | {hit_rate:.2f} | {s['memory_retrieved']['mean']:.1f} | "
            f"{s['memory_incompatible']['mean']:.1f} | {s['memory_rejected']['mean']:.1f} | "
            f"{s['memory_used']['mean']:.1f} | {s['memory_effective']['mean']:.1f} | "
            f"{s['memory_harmful']['mean']:.1f} | {s['memory_unattributed']['mean']:.1f} | "
            f"{d['effective_hit_rate']:.2f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _to_yaml(data: Any, indent: int = 0) -> str:
    """极简 YAML 序列化（避免引入额外依赖即可读写配置快照）。"""
    pad = "  " * indent
    if isinstance(data, dict):
        out = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                out.append(f"{pad}{k}:")
                out.append(_to_yaml(v, indent + 1))
            else:
                out.append(f"{pad}{k}: {v}")
        return "\n".join(out)
    if isinstance(data, list):
        out = []
        for item in data:
            if isinstance(item, dict):
                out.append(f"{pad}-")
                out.append(_to_yaml(item, indent + 1))
            else:
                out.append(f"{pad}- {item}")
        return "\n".join(out)
    return f"{pad}{data}"


def run_benchmark_cli(args) -> int:
    payload = run_suite(
        suite=args.suite,
        repeat=args.repeat,
        seed=args.seed,
        tokenizer_backend=getattr(args, "tokenizer", None),
    )
    out_dir = write_results(payload)
    print(
        render_report(
            {
                "suite": payload["suite"],
                "repeat": payload["repeat"],
                "seed": payload["seed"],
                "baseline_id": payload.get("baseline_id"),
                "env": payload["env"],
                "results": {
                    cid: {
                        "label": r["label"],
                        "config": r["config"],
                        "summary": r["summary"],
                        "derived": r["derived"],
                    }
                    for cid, r in payload["results"].items()
                },
            }
        )
    )
    print(f"\n结果已保存到: {out_dir}")
    return 0


__all__ = [
    "run_suite",
    "run_once",
    "write_results",
    "render_report",
    "run_benchmark_cli",
    "ExperimentConfig",
    "STATE_SUITE",
    "MEMORY_GUARD_SUITE",
]
