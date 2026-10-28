"""运行时装配：把配置、Provider、Agent、上下文与编排器组装起来。"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from agent_runtime.agents import (
    CodeActExecutorAgent,
    PlannerAgent,
    RetrieverAgent,
    ReviewerSummarizerAgent,
)
from agent_runtime.artifact_store.store import ArtifactStore
from agent_runtime.config import Config, load_config
from agent_runtime.memory.store import MemoryStore
from agent_runtime.observability.logging import get_logger
from agent_runtime.observability.metrics import MetricsCollector
from agent_runtime.providers.factory import build_embedding, build_llm
from agent_runtime.providers.tokenizer import get_tokenizer
from agent_runtime.runtime.agent import BaseAgent
from agent_runtime.runtime.channel import Channel
from agent_runtime.runtime.checkpoint import FileCheckpointStore, WorkflowCheckpoint
from agent_runtime.runtime.context import RunContext
from agent_runtime.runtime.llm_gateway import LLMGateway, resolve_coalesce_mode
from agent_runtime.runtime.event_log import EventJournal, journal_path_for
from agent_runtime.runtime.orchestrator import Orchestrator, RunResult
from agent_runtime.runtime.resilience import CircuitBreaker, RetryBudget
from agent_runtime.runtime.task import Task
from agent_runtime.sandbox.executor import CodeActExecutor
from agent_runtime.state_exchange.exchange import StateExchange


def build_default_agents(config: Config) -> dict[str, BaseAgent]:
    """构造默认四类 Agent，并按配置为每个 Agent 建独立 failure circuit。"""

    def breaker() -> CircuitBreaker | None:
        threshold = config.agent_circuit_failure_threshold
        if threshold is None:
            return None
        return CircuitBreaker(
            failure_threshold=threshold,
            reset_timeout_sec=config.agent_circuit_reset_sec,
        )

    return {
        "planner": PlannerAgent(circuit_breaker=breaker()),
        "retriever": RetrieverAgent(circuit_breaker=breaker()),
        "executor": CodeActExecutorAgent(circuit_breaker=breaker()),
        "reviewer": ReviewerSummarizerAgent(circuit_breaker=breaker()),
    }


def _degrade_unsupported_transport(config: Config) -> Config:
    """OS-SOTA1: hosts without AF_UNIX (Windows) fall back to the in-process control plane.

    Set ``AGENT_STRICT_TRANSPORT=1`` to keep the old fail-fast behaviour.  The fallback is
    logged and visible in results (``control_transport`` in the run config), so it can never be
    mistaken for verified UDS evidence (``verify_results.py --require-linux`` still rejects it).
    """
    import os
    import socket

    if config.control_transport == "uds" and not hasattr(socket, "AF_UNIX"):
        if os.environ.get("AGENT_STRICT_TRANSPORT", "0").strip().lower() in {"1", "true", "yes", "on"}:
            return config
        get_logger("runtime").warning("AF_UNIX unavailable: control_transport uds -> inproc (degraded)")
        return config.with_overrides(control_transport="inproc", state_fd_handoff=False)
    return config


def build_context(
    config: Config,
    mode: str,
    *,
    task_id: str = "",
    use_memory: bool = False,
    use_state_exchange: bool = False,
    use_sandbox: bool = False,
    memory: MemoryStore | None = None,
    llm_gateway: LLMGateway | None = None,
) -> RunContext:
    """根据配置与模式构造运行上下文。

    若 `use_memory` 为真且未传入共享 `memory`，则新建一个进程内记忆库（无跨任务复用）。
    跨任务复用场景应由调用方创建一个共享 `MemoryStore` 并传入。
    """
    config = _degrade_unsupported_transport(config)
    llm = build_llm(config)
    embedding = build_embedding(config)
    metrics = MetricsCollector()
    tokenizer = get_tokenizer(model=config.llm.model, backend=config.tokenizer_backend)
    trace_id = f"trace_{uuid.uuid4().hex[:12]}"
    logger = get_logger("runtime", mode=mode, task_id=task_id, trace_id=trace_id)
    channel = Channel(
        mode=mode, metrics=metrics, tokenizer=tokenizer, logger=logger,
        reference_first=config.reference_first_data_plane,
        text_style=config.text_style,
        structured_wire_codec=config.structured_wire_codec,
        control_transport=config.control_transport,
        header_prediction=config.header_prediction,
        pipelined_verification=config.pipelined_verification,
        ack_chain=config.ack_chain,
        schema_atoms=config.schema_atoms,
    )
    rng = np.random.default_rng(config.random_seed)
    artifact_store = ArtifactStore(
        task_id=task_id or "global",
        root=(Path(config.data_dir) / "artifacts") if config.artifact_persistent else None,
        verify_reads=config.artifact_verify_reads,
    )
    state_fd_transport = None
    if config.state_fd_handoff and config.control_transport == "uds":
        from agent_runtime.scheduler.control_transport import UDSStateAttachTransport

        state_fd_transport = UDSStateAttachTransport()

        def _attach_large_state(payload: bytes):
            try:
                ok, socket_bytes = state_fd_transport.attach_bytes(payload)
            except Exception:
                metrics.record_state_fd_handoff(
                    state_bytes=len(payload), socket_bytes=0, ok=False
                )
                raise
            metrics.record_state_fd_handoff(
                state_bytes=len(payload), socket_bytes=socket_bytes, ok=ok
            )
            return ok, socket_bytes

    # 共享内存传输：默认进程内（跨平台稳定）；openEuler 上可通过 env 开启。
    state_exchange = StateExchange(
        embedding=embedding,
        task_id=task_id or "global",
        use_shared_memory=config.state_shared_memory,
        precision=config.state_precision,
        adaptive_cosine_threshold=config.state_adaptive_cosine_threshold,
        deduplicate=config.state_deduplicate,
        on_large_state=_attach_large_state if state_fd_transport is not None else None,
        fd_handoff_min_bytes=config.fd_handoff_min_bytes,
    )
    memory_owned = False
    if use_memory and memory is None:
        memory = MemoryStore(
            embedding=embedding,
            vector_backend=config.vector_backend,
            max_records=config.memory_max_records,
            recency_half_life_sec=config.memory_recency_half_life_sec,
        )
        memory_owned = True
    sandbox = CodeActExecutor(config.sandbox) if use_sandbox else None
    event_journal = None
    if config.event_journal_enabled:
        journal_path = journal_path_for(Path(config.data_dir) / "events", task_id or "global")
        journal_durability = (
            config.event_journal_durability if config.event_journal_durable else "buffered"
        )
        event_journal = EventJournal(journal_path, durability=journal_durability)
    coalesce_mode = resolve_coalesce_mode(getattr(config, "llm_coalesce", "off"), config.llm.provider)
    if llm_gateway is None and coalesce_mode == "session":
        coalesce_mode = "task"  # a session gateway must be supplied by the session owner
    if llm_gateway is None and coalesce_mode != "off":
        from agent_runtime.runtime.context import _COALESCED_HINTS

        llm_gateway = LLMGateway(
            llm, mode=coalesce_mode, contract_hint=_COALESCED_HINTS.get(config.llm.output_contract, "")
        )
    return RunContext(
        config=config,
        llm=llm,
        embedding=embedding,
        metrics=metrics,
        channel=channel,
        mode=mode,
        task_id=task_id,
        trace_id=trace_id,
        rng=rng,
        logger=logger,
        use_memory=use_memory,
        use_state_exchange=use_state_exchange,
        artifact_store=artifact_store,
        artifact_store_owned=True,
        state_exchange=state_exchange,
        memory=memory,
        memory_owned=memory_owned,
        sandbox=sandbox,
        event_journal=event_journal,
        retry_budget=(
            RetryBudget(config.task_retry_budget)
            if config.task_retry_budget is not None
            else None
        ),
        state_fd_transport=state_fd_transport,
        llm_gateway=llm_gateway,
    )


async def run_task(
    task: Task,
    *,
    config: Config | None = None,
    mode: str = "structured",
    use_memory: bool = False,
    use_state_exchange: bool = False,
    use_sandbox: bool = False,
    memory: MemoryStore | None = None,
    checkpoint_store: FileCheckpointStore | None = None,
    resume_from: WorkflowCheckpoint | None = None,
    pause_after_stage: str | None = None,
) -> RunResult:
    """便捷入口：装配并运行一个任务。"""
    config = config or load_config()
    ctx = build_context(
        config,
        mode,
        task_id=task.task_id,
        use_memory=use_memory,
        use_state_exchange=use_state_exchange,
        use_sandbox=use_sandbox,
        memory=memory,
    )
    orchestrator = Orchestrator(build_default_agents(config))
    return await orchestrator.run_task(
        task,
        ctx,
        checkpoint_store=checkpoint_store,
        resume_from=resume_from,
        pause_after_stage=pause_after_stage,
    )


__all__ = ["build_default_agents", "build_context", "run_task"]
