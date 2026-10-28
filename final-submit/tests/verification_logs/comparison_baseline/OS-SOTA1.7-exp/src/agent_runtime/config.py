"""全局配置加载。

配置来源优先级（高→低）：
1. 显式传入的覆盖参数；
2. 环境变量（含 .env 文件，若存在）；
3. 代码内默认值。

设计为纯标准库实现（不依赖 python-dotenv），保证 openEuler 上零额外依赖即可工作。
"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_ENV_LOADED = False


def _load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """将 .env 文件中的键值对加载进 os.environ（不覆盖已存在的变量）。"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _get(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_optional_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "mock"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    # None means the provider's natural/default completion limit.  An explicit
    # cap is opt-in so the baseline does not silently change its output budget.
    max_tokens: int | None = None
    output_contract: str = "none"
    # fable6: bounded per-request wall clock and runtime-managed retries.  The OpenAI SDK
    # default (600 s timeout x 3 attempts) let a single stalled request block a benchmark for
    # 30 minutes with no output, which is exactly how the fable5 real-LLM rerun failed.
    timeout_sec: float = 60.0
    max_retries: int = 3
    # sota1: completion-budget governor.  ``reasoning_effort`` is forwarded only when set (or
    # when the governor escalates after a measured cap violation) and is dropped automatically
    # if the provider rejects the parameter.
    reasoning_effort: str | None = None
    completion_governor: bool = True

    def __post_init__(self) -> None:
        if self.reasoning_effort is not None and self.reasoning_effort not in {"minimal", "low", "medium", "high"}:
            raise ValueError("llm reasoning_effort must be one of: minimal, low, medium, high")
        if not math.isfinite(self.temperature):
            raise ValueError("llm temperature must be finite")
        if not math.isfinite(self.timeout_sec) or self.timeout_sec <= 0:
            raise ValueError("llm timeout_sec must be finite and positive")
        if self.max_retries < 0:
            raise ValueError("llm max_retries must be >= 0")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("llm max_tokens must be positive")
        if self.output_contract not in {"none", "concise", "json"}:
            raise ValueError(
                "llm output_contract must be one of: none, concise, json"
            )


@dataclass(frozen=True)
class EmbeddingConfig:
    # fable13: the default is a real (non-mock) semantic embedding trained offline from system
    # documentation (see providers/docsem_embedding.py); "mock" remains available.
    provider: str = "docsem"
    model: str = "docsem-ppmi-svd-sif"
    dim: int = 256

    def __post_init__(self) -> None:
        if self.dim <= 0:
            raise ValueError("embedding dim must be positive")


@dataclass(frozen=True)
class SandboxConfig:
    backend: str = "subprocess"
    timeout_sec: int = 10
    mem_mb: int = 256
    cpu_sec: int = 8
    network: str = "disabled"
    # v0.8: warm single-threaded fork-server avoids repeated interpreter exec startup on Linux/openEuler.
    warm_start: bool = True
    # Explicit opt-in to a real user+mount+network+pid namespace. Unsupported hosts fail closed.
    namespace_isolation: bool = False

    def __post_init__(self) -> None:
        if self.backend.lower() not in {"subprocess", "podman", "cgroup"}:
            raise ValueError(f"unsupported sandbox backend: {self.backend!r}")
        if self.timeout_sec <= 0 or self.mem_mb <= 0 or self.cpu_sec <= 0:
            raise ValueError("sandbox timeout_sec, mem_mb and cpu_sec must be positive")
        if self.network.lower() not in {"disabled", "enabled"}:
            raise ValueError("sandbox network must be 'disabled' or 'enabled'")


@dataclass(frozen=True)
class Config:
    """运行时全局配置（不可变）。"""

    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    vector_backend: str = "numpy"
    # 通信 token 计量后端：heuristic（零依赖、可复现）/ auto / tiktoken。
    # 注意：wire_bytes 始终作为 tokenizer-independent 的主辅助指标保留。
    tokenizer_backend: str = "heuristic"
    # Text-mode wire renderer. ``verbose`` preserves the original natural-language
    # baseline; ``lean`` is a deliberately stronger concise textual lower bound used
    # by the fairness benchmark. Both carry the same logical fields/payload.
    text_style: str = "verbose"
    # 共享记忆复用前的兼容性门控；默认开启。仅 benchmark 消融允许关闭。
    memory_compatibility_gate: bool = True
    # ``inbound``: every agent LLM call reads its inbound wire message (what an LLM-driven agent
    # actually sees), so provider-billed prompt tokens reflect the protocol.
    llm_context_mode: str = "none"
    # sota1: LLM call coalescing (vectored completion).  ``auto`` = on for real providers only;
    # ``task`` = one provider request per task barrier for all deferrable calls; ``off``.
    llm_coalesce: str = "auto"
    llm_advisory: bool = True
    large_state_handoff: str = "auto"
    # Semantic judge (reads only the inbound wire).  Carried in config so that it also reaches
    # agents running in separate OS processes (env of a pre-started host may be stale).
    llm_judge: bool = False
    # Critical-path calls carry queued deferrable items (only when coalescing is on).
    llm_piggyback: bool = True
    # Judge label space: "global" = 8-way (scenario labels + OS-fault distractors); "task" = 2-way.
    llm_judge_labels: str = "global"
    # LLM-visible projection of inbound messages: "wire" (as transported) or "semantic".
    llm_view: str = "wire"
    # Exact-input cache fast path: a previously verified identical task may bypass repeated
    # Planner/Retriever/Executor work and go directly to Reviewer verification.
    memory_fast_path: bool = True
    # Structured Protocol 1.3 physical wire.  JSON remains available as a compatibility
    # ablation; session_msgpack is the low-overhead default.
    structured_wire_codec: str = "session_msgpack"
    # Protocol 1.4 (fable7): context-predictive header compression + negotiated routines +
    # HPACK-style reference table on session codecs.  AGENT_HEADER_PREDICTION=0 restores 1.3.
    header_prediction: bool = True
    # fable7: pipelined control-plane verification (ACK window + commit barrier).
    # AGENT_PIPELINED_VERIFY=0 restores one blocking verified round trip per frame.
    pipelined_verification: bool = True
    # fable8: cumulative hash-chain ACK at barriers (AGENT_ACK_CHAIN=0 -> per-frame ACKs).
    ack_chain: bool = True
    # fable8: schema key atoms + columnar record lists (AGENT_SCHEMA_ATOMS=0 disables).
    schema_atoms: bool = True
    # fable8: agentic plan caching from verified shared memory (AGENT_PLAN_CACHE=0 disables).
    plan_cache: bool = True
    # fable10: run every Agent in its own OS process (AGENT_AGENT_PROCESSES=1).
    agent_processes: bool = False
    # fable11: content-defined chunk dedup of task context across rounds of one collaboration
    # context (tasks sharing ``context_id``); AGENT_CONTEXT_DEDUP=0 disables.
    context_dedup: bool = True
    # fable14: versioned collaboration state references the plan state instead of copying it.
    state_normalize: bool = True
    # Message topology. ``request_reply`` preserves the historical accounting; ``forward``
    # omits intermediate response frames while retaining the result object in-process.
    pipeline_topology: str = "request_reply"
    # Optional main-pipeline sealed memfd attachment for large state payloads.
    state_fd_handoff: bool = False
    fd_handoff_min_bytes: int = 1024 * 1024
    # Optional real kernel control-plane path.  ``uds`` uses a dedicated parser/router process.
    control_transport: str = "inproc"
    state_shared_memory: bool = False
    # 非文本状态传输精度：adaptive 优先 int8，在余弦保真阈值不满足时回退 float16。
    state_precision: str = "adaptive"
    state_adaptive_cosine_threshold: float = 0.999
    # 内容寻址状态去重：同一 task 内相同 state:// 内容只物化一次。
    state_deduplicate: bool = True
    # Evolving collaboration metadata: True advances immutable versions with compact deltas;
    # False materializes a full snapshot at every stage (controlled ablation only).
    state_versioned_delta: bool = True
    # Producer-side reference-first data plane. False is retained only for controlled
    # ablation/legacy comparisons; text mode is unaffected.
    reference_first_data_plane: bool = True
    # Runtime event journal is opt-in so benchmark latency is not polluted by durable I/O.
    event_journal_enabled: bool = False
    # Backward-compat boolean: False forces buffered mode. New deployments should use
    # event_journal_durability: sync (fsync each event), task (fsync at task boundary),
    # or buffered (no implicit fsync).
    event_journal_durable: bool = True
    event_journal_durability: str = "sync"
    # Artifact persistence is opt-in to keep the benchmark free of disk I/O by default.
    artifact_persistent: bool = False
    artifact_verify_reads: bool = True
    # Completed-result idempotency persistence is opt-in; the default stays zero-I/O.
    idempotency_persistent: bool = False
    idempotency_ttl_sec: float = 24 * 3600
    # Task/agent failure containment. ``None`` keeps historical unrestricted shared retry
    # behavior; per-agent RetryPolicy still applies independently.
    task_retry_budget: int | None = None
    agent_circuit_failure_threshold: int | None = None
    agent_circuit_reset_sec: float = 30.0
    # Memory lifecycle controls. ``None`` means unbounded record count.
    memory_max_records: int | None = None
    memory_recency_half_life_sec: float = 7 * 24 * 3600
    random_seed: int = 42
    data_dir: str = ".agent_data"
    log_level: str = "INFO"
    log_format: str = "json"

    def __post_init__(self) -> None:
        if self.large_state_handoff not in {"auto", "copy", "sealed_fd"}:
            raise ValueError(f"unsupported large_state_handoff: {self.large_state_handoff!r}")
        if self.vector_backend.lower() not in {"numpy", "hnswlib", "faiss"}:
            raise ValueError(f"unsupported vector backend: {self.vector_backend!r}")
        if self.tokenizer_backend not in {"heuristic", "auto", "tiktoken"}:
            raise ValueError(f"unsupported tokenizer backend: {self.tokenizer_backend!r}")
        if self.text_style not in {"verbose", "lean"}:
            raise ValueError(f"unsupported text_style: {self.text_style!r}")
        if self.structured_wire_codec not in {"json", "msgpack", "session_json", "session_msgpack", "session_msgpack_dict"}:
            raise ValueError(
                f"unsupported structured_wire_codec: {self.structured_wire_codec!r}"
            )
        if self.pipeline_topology not in {"request_reply", "forward"}:
            raise ValueError(f"unsupported pipeline_topology: {self.pipeline_topology!r}")
        if self.fd_handoff_min_bytes <= 0:
            raise ValueError("fd_handoff_min_bytes must be positive")
        if self.control_transport not in {"inproc", "uds"}:
            raise ValueError(f"unsupported control_transport: {self.control_transport!r}")
        if self.state_precision not in {"float32", "float16", "int8", "adaptive"}:
            raise ValueError(f"unsupported state_precision: {self.state_precision!r}")
        if (
            not math.isfinite(self.state_adaptive_cosine_threshold)
            or not 0.0 < self.state_adaptive_cosine_threshold <= 1.0
        ):
            raise ValueError("state_adaptive_cosine_threshold must be in (0, 1]")
        if self.event_journal_durability not in {"sync", "task", "buffered"}:
            raise ValueError(
                f"unsupported event_journal_durability: {self.event_journal_durability!r}"
            )
        if not math.isfinite(self.idempotency_ttl_sec) or self.idempotency_ttl_sec <= 0:
            raise ValueError("idempotency_ttl_sec must be a finite positive number")
        if self.task_retry_budget is not None and self.task_retry_budget < 0:
            raise ValueError("task_retry_budget must be >= 0 when provided")
        if (
            self.agent_circuit_failure_threshold is not None
            and self.agent_circuit_failure_threshold <= 0
        ):
            raise ValueError("agent_circuit_failure_threshold must be positive when provided")
        if not math.isfinite(self.agent_circuit_reset_sec) or self.agent_circuit_reset_sec < 0:
            raise ValueError("agent_circuit_reset_sec must be a finite non-negative number")
        if self.memory_max_records is not None and self.memory_max_records <= 0:
            raise ValueError("memory_max_records must be positive when provided")
        if (
            not math.isfinite(self.memory_recency_half_life_sec)
            or self.memory_recency_half_life_sec <= 0
        ):
            raise ValueError("memory_recency_half_life_sec must be a finite positive number")

    # ----- 派生路径 -----
    @property
    def artifact_dir(self) -> Path:
        return Path(self.data_dir) / "artifacts"

    @property
    def memory_dir(self) -> Path:
        return Path(self.data_dir) / "memory"

    @property
    def state_dir(self) -> Path:
        return Path(self.data_dir) / "state"

    def with_overrides(self, **overrides: Any) -> Config:
        """返回应用覆盖参数后的新配置实例。"""
        return replace(self, **overrides)


def load_config(load_env: bool = True, **overrides: Any) -> Config:
    """从环境变量构造配置，可选地先加载 .env，并应用显式覆盖。"""
    if load_env:
        _load_dotenv()

    cfg = Config(
        llm=LLMConfig(
            provider=_get("AGENT_LLM_PROVIDER", "mock"),
            api_key=_get("AGENT_LLM_API_KEY", ""),
            base_url=_get("AGENT_LLM_BASE_URL", "https://api.openai.com/v1"),
            model=_get("AGENT_LLM_MODEL", "gpt-4o-mini"),
            temperature=_get_float("AGENT_LLM_TEMPERATURE", 0.0),
            max_tokens=_get_optional_int("AGENT_LLM_MAX_TOKENS"),
            output_contract=_get("AGENT_LLM_OUTPUT_CONTRACT", "none").lower(),
            timeout_sec=_get_float("AGENT_LLM_TIMEOUT_SEC", 60.0),
            max_retries=int(_get_float("AGENT_LLM_MAX_RETRIES", 3)),
            reasoning_effort=(_get("AGENT_LLM_REASONING_EFFORT", "").strip().lower() or None),
            completion_governor=_get("AGENT_LLM_COMPLETION_GOVERNOR", "1").strip().lower()
            not in {"0", "false", "no", "off"},
        ),
        embedding=EmbeddingConfig(
            provider=_get("AGENT_EMBEDDING_PROVIDER", "docsem"),
            model=_get("AGENT_EMBEDDING_MODEL", "docsem-ppmi-svd-sif"),
            dim=_get_int("AGENT_EMBEDDING_DIM", 256),
        ),
        sandbox=SandboxConfig(
            backend=_get("AGENT_SANDBOX_BACKEND", "subprocess"),
            timeout_sec=_get_int("AGENT_SANDBOX_TIMEOUT_SEC", 10),
            mem_mb=_get_int("AGENT_SANDBOX_MEM_MB", 256),
            cpu_sec=_get_int("AGENT_SANDBOX_CPU_SEC", 8),
            network=_get("AGENT_SANDBOX_NETWORK", "disabled"),
            warm_start=_get("AGENT_SANDBOX_WARM", "true").lower() in ("1", "true", "yes"),
            namespace_isolation=_get("AGENT_SANDBOX_NAMESPACE", "false").lower()
            in ("1", "true", "yes"),
        ),
        vector_backend=_get("AGENT_VECTOR_BACKEND", "numpy"),
        tokenizer_backend=_get("AGENT_TOKENIZER", "heuristic").lower(),
        text_style=_get("AGENT_TEXT_STYLE", "verbose").lower(),
        llm_context_mode=_get("AGENT_LLM_CONTEXT", "none").lower(),
        llm_coalesce=_get("AGENT_LLM_COALESCE", "auto").strip().lower(),
        llm_advisory=_get("AGENT_LLM_ADVISORY", "1").strip().lower() not in {"0", "false", "no"},
        large_state_handoff=_get("AGENT_LARGE_STATE_HANDOFF", "auto").strip().lower(),
        llm_judge=_get("AGENT_LLM_JUDGE", "0").strip().lower() in {"1", "true", "yes", "on"},
        llm_judge_labels=_get("AGENT_LLM_JUDGE_LABELS", "global").strip().lower(),
        llm_view=_get("AGENT_LLM_VIEW", "wire").strip().lower(),
        llm_piggyback=_get("AGENT_LLM_PIGGYBACK", "1").strip().lower() not in {"0", "false", "no", "off"},
        memory_compatibility_gate=_get("AGENT_MEMORY_GATE", "true").lower()
        in ("1", "true", "yes"),
        memory_fast_path=_get("AGENT_MEMORY_FAST_PATH", "true").lower()
        in ("1", "true", "yes"),
        structured_wire_codec=_get("AGENT_STRUCTURED_CODEC", "session_msgpack").lower(),
        state_normalize=_get("AGENT_STATE_NORMALIZE", "1").strip().lower() not in {"0", "false", "no", "off"},
        context_dedup=_get("AGENT_CONTEXT_DEDUP", "1").strip().lower() not in {"0", "false", "no", "off"},
        agent_processes=_get("AGENT_AGENT_PROCESSES", "0").strip().lower() in {"1", "true", "yes", "on"},
        plan_cache=_get("AGENT_PLAN_CACHE", "1").strip().lower() not in {"0", "false", "no", "off"},
        schema_atoms=_get("AGENT_SCHEMA_ATOMS", "1").strip().lower() not in {"0", "false", "no", "off"},
        ack_chain=_get("AGENT_ACK_CHAIN", "1").strip().lower() not in {"0", "false", "no", "off"},
        pipelined_verification=_get("AGENT_PIPELINED_VERIFY", "1").strip().lower() not in {"0", "false", "no", "off"},
        header_prediction=_get("AGENT_HEADER_PREDICTION", "1").strip().lower() not in {"0", "false", "no", "off"},
        pipeline_topology=_get("AGENT_PIPELINE_TOPOLOGY", "request_reply").lower(),
        state_fd_handoff=_get("AGENT_STATE_FD_HANDOFF", "false").lower() in ("1", "true", "yes"),
        fd_handoff_min_bytes=_get_int("AGENT_FD_HANDOFF_MIN_BYTES", 1024 * 1024),
        control_transport=_get("AGENT_CONTROL_TRANSPORT", "inproc").lower(),
        state_shared_memory=_get("AGENT_STATE_SHM", "false").lower() in ("1", "true", "yes"),
        state_precision=_get("AGENT_STATE_PRECISION", "adaptive").lower(),
        state_adaptive_cosine_threshold=_get_float("AGENT_STATE_ADAPTIVE_COSINE", 0.999),
        state_deduplicate=_get("AGENT_STATE_DEDUP", "true").lower() in ("1", "true", "yes"),
        state_versioned_delta=_get("AGENT_STATE_VERSIONED_DELTA", "true").lower()
        in ("1", "true", "yes"),
        reference_first_data_plane=_get("AGENT_REFERENCE_FIRST", "true").lower()
        in ("1", "true", "yes"),
        event_journal_enabled=_get("AGENT_EVENT_JOURNAL", "false").lower()
        in ("1", "true", "yes"),
        event_journal_durable=_get("AGENT_EVENT_JOURNAL_DURABLE", "true").lower()
        in ("1", "true", "yes"),
        event_journal_durability=_get("AGENT_EVENT_JOURNAL_DURABILITY", "sync").lower(),
        artifact_persistent=_get("AGENT_ARTIFACT_PERSISTENT", "false").lower()
        in ("1", "true", "yes"),
        artifact_verify_reads=_get("AGENT_ARTIFACT_VERIFY_READS", "true").lower()
        in ("1", "true", "yes"),
        idempotency_persistent=_get("AGENT_IDEMPOTENCY_PERSISTENT", "false").lower()
        in ("1", "true", "yes"),
        idempotency_ttl_sec=_get_float("AGENT_IDEMPOTENCY_TTL_SEC", 24 * 3600),
        task_retry_budget=_get_optional_int("AGENT_TASK_RETRY_BUDGET"),
        agent_circuit_failure_threshold=_get_optional_int("AGENT_CIRCUIT_FAILURE_THRESHOLD"),
        agent_circuit_reset_sec=_get_float("AGENT_CIRCUIT_RESET_SEC", 30.0),
        memory_max_records=_get_optional_int("AGENT_MEMORY_MAX_RECORDS"),
        memory_recency_half_life_sec=_get_float(
            "AGENT_MEMORY_RECENCY_HALF_LIFE_SEC", 7 * 24 * 3600
        ),
        random_seed=_get_int("AGENT_RANDOM_SEED", 42),
        data_dir=_get("AGENT_DATA_DIR", ".agent_data"),
        log_level=_get("AGENT_LOG_LEVEL", "INFO"),
        log_format=_get("AGENT_LOG_FORMAT", "json"),
    )
    if overrides:
        cfg = cfg.with_overrides(**overrides)
    return cfg


__all__ = [
    "Config",
    "LLMConfig",
    "EmbeddingConfig",
    "SandboxConfig",
    "load_config",
]
