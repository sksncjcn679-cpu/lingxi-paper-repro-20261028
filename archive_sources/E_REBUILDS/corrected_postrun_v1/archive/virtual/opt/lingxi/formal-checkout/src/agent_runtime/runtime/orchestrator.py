"""编排器：驱动一次多 Agent 协作任务的端到端执行。

默认流水线为 Planner → Retriever → Executor → Reviewer。除结构化通信、能力发现、
幂等外，本模块支持**阶段边界 checkpoint / resume**：checkpoint 保存已完成阶段结果、
共享 blackboard、消息历史和指标状态；恢复时重新物化进程内 state/artifact 引用，避免
把已经失效的共享内存句柄写进持久化文件。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.observability.metrics import MetricsSnapshot
from agent_runtime.protocol.idempotency import (
    IdempotencyCache,
    PersistentIdempotencyCache,
    rebind_cached_result,
)
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.registry.registry import AgentRegistry
from agent_runtime.runtime.agent import BaseAgent
from agent_runtime.runtime.checkpoint import (
    FileCheckpointStore,
    WorkflowCheckpoint,
    validate_checkpoint,
)
from agent_runtime.runtime.context import RunContext
from agent_runtime.runtime.errors import ProtocolError
from agent_runtime.runtime.task import Task


@dataclass
class RunResult:
    """一次任务运行的结果汇总。"""

    task_id: str
    mode: str
    success: bool
    quality: float
    outputs: dict[str, Any]
    metrics: MetricsSnapshot
    error: dict[str, Any] | None = None
    messages: list[AgentMessage] = field(default_factory=list)
    paused: bool = False
    checkpoint: WorkflowCheckpoint | None = None


@dataclass
class StageInput:
    """某阶段入站消息所携带的内容与引用。

    - ``params`` 进入消息的 input_parameters；
    - ``artifact_refs`` / ``state_ref`` 进入消息的引用字段（不内联全文）。
    """

    params: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)
    state_ref: str | None = None


PIPELINE_STAGES: list[tuple[str, str]] = [
    ("planner", "task_planning"),
    ("retriever", "retrieval"),
    ("executor", "execute"),
    ("reviewer", "review"),
]

# 只持久化恢复执行真正需要、且应当是 JSON-safe 的运行态字段。
_CHECKPOINT_BLACKBOARD_KEYS = {
    "plan",
    "evidence",
    "evidence_artifact_ref",
    "retrieved_evidence_refs",
    "reused_answer",
    "reused_solution",
    "reused_memory_ids",
    "signature",
    "state_consumed",
    "solution_used",
    "execution",
    "answer",
}


class Orchestrator:
    """编排多个 Agent 完成任务。

    启动时对每个 Agent 进行握手与注册，运行时通过能力发现解析流水线阶段。可选的
    checkpoint 在每个成功阶段后创建；恢复要求任务指纹、阶段拓扑与运行配置一致。
    """

    PIPELINE = [name for name, _ in PIPELINE_STAGES]

    def __init__(self, agents: dict[str, BaseAgent]) -> None:
        self.agents = agents
        self.registry = AgentRegistry()
        for agent in agents.values():
            result = self.registry.handshake(agent.card())
            if not result.accepted:
                raise ProtocolError(f"Agent {agent.name} 握手失败: {result.reason}")
        self._resolved: dict[str, str] = {}
        for stage, capability in PIPELINE_STAGES:
            candidates = self.registry.discover(capability)
            if not candidates:
                raise ValueError(f"无 Agent 提供能力 '{capability}'（阶段 {stage}）")
            self._resolved[stage] = candidates[0]

    async def run_task(
        self,
        task: Task,
        ctx: RunContext,
        *,
        checkpoint_store: FileCheckpointStore | None = None,
        resume_from: WorkflowCheckpoint | None = None,
        pause_after_stage: str | None = None,
    ) -> RunResult:
        """Run one task and guarantee best-effort cleanup on unexpected runtime crashes.

        Agent business failures are normally converted to ``AgentMessage(ERROR)`` by
        ``BaseAgent.run``.  This outer guard is for infrastructure failures (channel,
        journal, checkpoint filesystem, serializer, etc.) that legitimately propagate.
        Those failures must not leave started Agent lifecycles, shared-memory handles or
        persistent idempotency DB connections dangling.
        """
        try:
            return await self._run_task_impl(
                task,
                ctx,
                checkpoint_store=checkpoint_store,
                resume_from=resume_from,
                pause_after_stage=pause_after_stage,
            )
        except BaseException as exc:
            with contextlib.suppress(Exception):
                ctx.record_event(
                    "task.crashed",
                    payload={"error_type": type(exc).__name__, "message": str(exc)[:256]},
                )
            gateway = getattr(ctx, "llm_gateway", None)
            if gateway is not None and getattr(gateway, "mode", "task") == "task":
                with contextlib.suppress(Exception):
                    gateway.discard()
            started = ctx.blackboard.pop("_runtime_started_agents", [])
            for name in reversed(started):
                agent = self.agents.get(name)
                if agent is not None:
                    with contextlib.suppress(Exception):
                        await agent.on_stop(ctx)
            with contextlib.suppress(Exception):
                ctx.metrics.stop()
            if ctx.state_exchange is not None:
                with contextlib.suppress(Exception):
                    ctx.state_exchange.close()
            if ctx.state_fd_transport is not None:
                with contextlib.suppress(Exception):
                    ctx.state_fd_transport.close()
            if ctx.artifact_store is not None and ctx.artifact_store_owned:
                with contextlib.suppress(Exception):
                    ctx.artifact_store.close()
            if ctx.memory is not None and ctx.memory_owned:
                with contextlib.suppress(Exception):
                    ctx.memory.close()
            with contextlib.suppress(Exception):
                ctx.channel.close()
            idem = getattr(ctx, "_active_idempotency_store", None)
            if isinstance(idem, PersistentIdempotencyCache):
                with contextlib.suppress(Exception):
                    idem.close()
            if hasattr(ctx, "_active_idempotency_store"):
                delattr(ctx, "_active_idempotency_store")
            raise

    async def _run_task_impl(
        self,
        task: Task,
        ctx: RunContext,
        *,
        checkpoint_store: FileCheckpointStore | None = None,
        resume_from: WorkflowCheckpoint | None = None,
        pause_after_stage: str | None = None,
    ) -> RunResult:
        """运行任务；可在阶段边界暂停并从持久化 checkpoint 恢复。

        ``pause_after_stage`` 主要用于人工审批、演示与故障恢复测试。正常执行不传即可，
        因此 checkpoint 功能不会给主 benchmark 引入额外文件 I/O。
        """
        if pause_after_stage is not None and pause_after_stage not in self.PIPELINE:
            raise ValueError(f"unknown pause stage: {pause_after_stage}")

        ctx.task_id = task.task_id
        ctx.blackboard["task"] = task
        from agent_runtime.memory.signature import request_fingerprint

        ctx.blackboard["request_fingerprint"] = request_fingerprint(task.topic, task.payload)
        ctx.blackboard["task_topic"] = task.topic
        # fable11: every artifact of a task that belongs to a multi-round collaboration context
        # is content-defined-chunked against that context's shared chunk pool.
        if (
            getattr(ctx.config, "context_dedup", False)
            and task.context_id
            and ctx.artifact_store is not None
            and getattr(ctx.artifact_store, "_conn", None) is None
        ):
            from agent_runtime.artifact_store.cdc import context_pool

            ctx.artifact_store.default_chunk_pool = context_pool(task.context_id)
        idem = (
            PersistentIdempotencyCache(
                Path(ctx.config.data_dir) / "idempotency" / "completed.sqlite3",
                ttl_sec=ctx.config.idempotency_ttl_sec,
            )
            if ctx.config.idempotency_persistent
            else IdempotencyCache()
        )
        setattr(ctx, "_active_idempotency_store", idem)
        ctx.blackboard["_runtime_started_agents"] = []
        stage_inputs = self._new_stage_inputs(task, ctx)
        stage_results: list[AgentMessage] = []
        start_index = 0
        prev_source = "orchestrator"
        last_result: AgentMessage | None = None
        last_inbound: AgentMessage | None = None

        if resume_from is not None:
            (
                start_index,
                prev_source,
                last_result,
                stage_results,
                stage_inputs,
            ) = self._restore_checkpoint(task, ctx, resume_from)
            if stage_results:
                prior_requests = [
                    message for message in ctx.channel.history
                    if message.target_agent in self.agents
                ]
                last_inbound = prior_requests[-1] if prior_requests else None

        ctx.record_event(
            "task.started",
            payload={
                "context_id": task.context_id or task.parent_task_id or task.task_id,
                "resume": resume_from is not None,
            },
        )
        ctx.metrics.start()
        for agent in self.agents.values():
            await agent.on_start(ctx)
            ctx.blackboard["_runtime_started_agents"].append(agent.name)

        if (
            ctx.config.memory_fast_path
            and ctx.use_memory
            and ctx.memory is not None
            and resume_from is None
            and checkpoint_store is None
            and pause_after_stage is None
        ):
            ctx.metrics.record_memory_fast_path(probes=1)
            operation = str(task.payload.get("question") or "noop")
            record = ctx.memory.find_exact_request(
                str(ctx.blackboard["request_fingerprint"]),
                operation=operation,
                current_task_id=ctx.task_id,
            )
            if record is not None:
                answer = record.payload.get("answer")
                plan = {
                    "operation": record.payload.get("operation", operation),
                    "operands": list(
                        record.payload.get("operands") or task.payload.get("operands", [])
                    ),
                    "defect": record.payload.get("defect", task.payload.get("defect", "")),
                }
                ctx.memory.mark_used(record.memory_id)
                # Exact-cache lookup is a real Memory retrieval, but it remains separate from
                # semantic query/query-hit counters through the dedicated fast-path metrics.
                ctx.metrics.record_memory(retrieved=1, used=1)
                ctx.metrics.record_memory_fast_path(hits=1, tasks=1, skipped_stages=3)
                ctx.blackboard.update(
                    {
                        "plan": plan,
                        "signature": record.payload.get("signature"),
                        "execution": answer,
                        "solution_used": record.payload.get("solution"),
                        "memory_candidate_id": record.memory_id,
                        "memory_candidate_mode": "answer",
                        "memory_adopted_id": record.memory_id,
                        "reused_memory_ids": [record.memory_id],
                        "memory_fast_path": True,
                        "memory_fast_path_summary": record.summary,
                        "memory_reuse_contribution": {
                            "memory_id": record.memory_id,
                            "mode": "answer",
                            "value": answer,
                        },
                        "execution_provenance": {
                            "source": "memory_answer",
                            "memory_id": record.memory_id,
                            "status": "completed",
                            "fast_path": True,
                        },
                    }
                )
                stage_inputs["reviewer"] = StageInput(
                    params={"value": answer, "topic": task.topic, "memory_fast_path": True}
                )
                start_index = self.PIPELINE.index("reviewer")
                prev_source = "memory"
                last_result = None
                ctx.record_event(
                    "memory.fast_path",
                    payload={
                        "memory_id": record.memory_id,
                        "skipped_stages": ["planner", "retriever", "executor"],
                    },
                )

        aborted = False
        paused = False
        last_checkpoint: WorkflowCheckpoint | None = None

        for idx in range(start_index, len(PIPELINE_STAGES)):
            stage, _capability = PIPELINE_STAGES[idx]
            name = self._resolved[stage]
            agent = self.agents[name]
            si = stage_inputs[stage]
            inbound = AgentMessage(
                trace_id=ctx.trace_id,
                task_id=ctx.task_id,
                step_id=f"s{idx}",
                context_id=task.context_id or task.parent_task_id or task.task_id,
                parent_message_id=(
                    last_inbound.message_id
                    if ctx.config.pipeline_topology == "forward" and last_inbound is not None
                    else (last_result.message_id if last_result is not None else None)
                ),
                causation_id=(
                    last_inbound.message_id
                    if ctx.config.pipeline_topology == "forward" and last_inbound is not None
                    else (last_result.message_id if last_result is not None else None)
                ),
                sequence_no=(idx * 2) + 1,
                deadline_at=task.deadline_at,
                source_agent=prev_source,
                target_agent=name,
                action_type=_ACTION_FOR_STAGE[stage],
                capability=agent.capabilities[0] if agent.capabilities else "",
                input_parameters=dict(si.params),
                artifact_references=list(si.artifact_refs),
                state_reference=si.state_ref,
                status=MessageStatus.PENDING,
            )
            ctx.record_event(
                "stage.started",
                stage=stage,
                payload={"agent": name, "message_id": inbound.message_id},
            )
            ctx.channel.send(inbound)
            last_inbound = inbound
            ctx.record_event(
                "message.sent",
                stage=stage,
                payload={
                    "message_id": inbound.message_id,
                    "parent_message_id": inbound.parent_message_id,
                    "causation_id": inbound.causation_id,
                    "source": inbound.source_agent,
                    "target": inbound.target_agent,
                    "action": inbound.action_type.value,
                    "status": inbound.status.value,
                },
            )

            cached = idem.get(inbound)
            if cached is not None:
                result = rebind_cached_result(cached, inbound, trace_id=ctx.trace_id)
                self._apply_replayed_side_effects(stage, result, ctx)
                ctx.metrics.record_idempotency_replay()
                ctx.record_event(
                    "idempotency.replayed",
                    stage=stage,
                    payload={"message_id": inbound.message_id},
                )
            else:
                if getattr(ctx.config, "agent_processes", False):
                    result = self._run_in_agent_process(name, agent, inbound, ctx)
                else:
                    result = await agent.run(inbound, ctx)
                idem.remember(inbound, result)

            # In forward topology the result remains available to the orchestrator and is
            # persisted in checkpoints, but intermediate response frames are not sent over the
            # channel. The next request carries the propagated state/artifact references.
            emit_reply = (
                ctx.config.pipeline_topology == "request_reply"
                or idx == len(PIPELINE_STAGES) - 1
                or result.status in (MessageStatus.ERROR, MessageStatus.TIMEOUT)
            )
            if emit_reply:
                if ctx.config.pipeline_topology == "forward":
                    result.target_agent = "orchestrator"
                ctx.channel.send(result)
            last_result = result
            ctx.metrics.sample_resources()
            ctx.record_event(
                "message.sent" if emit_reply else "stage.result",
                stage=stage,
                payload={
                    "message_id": result.message_id,
                    "parent_message_id": result.parent_message_id,
                    "causation_id": result.causation_id,
                    "source": result.source_agent,
                    "target": result.target_agent,
                    "action": result.action_type.value,
                    "status": result.status.value,
                    "forwarded": not emit_reply,
                },
            )

            if result.status in (MessageStatus.ERROR, MessageStatus.TIMEOUT):
                ctx.record_event(
                    "stage.failed",
                    stage=stage,
                    payload={"message_id": result.message_id, "error": result.error or {}},
                )
                aborted = True
                break

            self._propagate(stage, result, stage_inputs, ctx, record_metrics=True)
            stage_results.append(result)
            ctx.record_event(
                "stage.completed",
                stage=stage,
                payload={"message_id": result.message_id, "status": result.status.value},
            )
            prev_source = name

            if checkpoint_store is not None or pause_after_stage == stage:
                last_checkpoint = self._make_checkpoint(
                    task=task,
                    ctx=ctx,
                    next_stage_index=idx + 1,
                    stage_results=stage_results,
                )
                if checkpoint_store is not None:
                    checkpoint_store.save(last_checkpoint)
                    ctx.record_event(
                        "checkpoint.saved",
                        stage=stage,
                        payload={"checkpoint_id": last_checkpoint.checkpoint_id},
                    )

            if pause_after_stage == stage:
                paused = True
                break

        # 暂停不是错误，也不发送伪“最终结果”；恢复后再继续流水线。
        if not paused and last_result is not None and ctx.config.pipeline_topology == "request_reply":
            final = AgentMessage(
                trace_id=ctx.trace_id,
                task_id=ctx.task_id,
                step_id="final",
                context_id=task.context_id or task.parent_task_id or task.task_id,
                parent_message_id=last_result.message_id,
                causation_id=last_result.message_id,
                sequence_no=len(PIPELINE_STAGES) * 2 + 1,
                deadline_at=task.deadline_at,
                source_agent=prev_source,
                target_agent="orchestrator",
                action_type=ActionType.RESULT,
                result=last_result.result,
                status=last_result.status,
            )
            ctx.channel.send(final)
            ctx.record_event(
                "message.sent",
                payload={
                    "message_id": final.message_id,
                    "parent_message_id": final.parent_message_id,
                    "causation_id": final.causation_id,
                    "source": final.source_agent,
                    "target": final.target_agent,
                    "action": final.action_type.value,
                    "status": final.status.value,
                },
            )

        for agent in self.agents.values():
            await agent.on_stop(ctx)
        ctx.blackboard.pop("_runtime_started_agents", None)

        # sota1 LLM commit barrier: every deferrable agent LLM call of this task is issued as
        # one vectored provider request (see runtime/llm_gateway.py).  It runs inside the
        # task's latency window, so reported task latency includes it.
        flush_llm = getattr(ctx, "flush_llm", None)
        if callable(flush_llm):
            try:
                flushed = flush_llm()
            except Exception as exc:  # noqa: BLE001 - advisory calls must not fail a verified task
                ctx.metrics.snapshot.degradation_count += 1
                ctx.record_event("llm.flush_failed", payload={"error_type": type(exc).__name__, "message": str(exc)[:256]})
            else:
                if flushed:
                    ctx.record_event("llm.coalesced_flush", payload={"provider_requests": flushed})

        # fable7 commit barrier: every pipelined control frame must be verified by the router
        # before the task outcome is committed; a missing/mismatching ACK fails the task.
        barrier = getattr(ctx.channel, "commit_barrier", None)
        barrier_error = barrier() if callable(barrier) else None
        if barrier_error:
            ctx.metrics.set_result(False, 0.0)
            ctx.metrics.snapshot.error_count += 1
            aborted = True
            if last_result is not None:
                last_result = last_result.model_copy(
                    update={"error": {"type": "control_plane_verification", "message": barrier_error[:256]}}
                )
            ctx.record_event("control.barrier_failed", payload={"message": barrier_error[:256]})

        if ctx.artifact_store is not None:
            ctx.metrics.snapshot.artifact_bytes += int(getattr(ctx.artifact_store, "materialized_bytes", 0))
        ctx.metrics.stop()

        if ctx.state_exchange is not None:
            with contextlib.suppress(Exception):
                ctx.state_exchange.close()
        if ctx.state_fd_transport is not None:
            with contextlib.suppress(Exception):
                ctx.state_fd_transport.close()
        if ctx.artifact_store is not None and ctx.artifact_store_owned:
            with contextlib.suppress(Exception):
                ctx.artifact_store.close()
        if ctx.memory is not None and ctx.memory_owned:
            with contextlib.suppress(Exception):
                ctx.memory.close()
        with contextlib.suppress(Exception):
            ctx.channel.close()

        ctx.record_event(
            "task.paused" if paused else ("task.failed" if aborted else "task.completed"),
            payload={
                "success": bool(ctx.metrics.snapshot.task_success) if not paused else False,
                "quality": float(ctx.metrics.snapshot.task_quality) if not paused else 0.0,
            },
        )
        if isinstance(idem, PersistentIdempotencyCache):
            idem.close()
        if hasattr(ctx, "_active_idempotency_store"):
            delattr(ctx, "_active_idempotency_store")

        outputs = {"answer": ctx.blackboard.get("answer")}
        snapshot = ctx.metrics.snapshot
        error = last_result.error if (aborted and last_result) else None
        if aborted and not error:
            error = {"type": "aborted", "stage": prev_source}
        return RunResult(
            task_id=ctx.task_id,
            mode=ctx.mode,
            success=snapshot.task_success if not paused else False,
            quality=snapshot.task_quality if not paused else 0.0,
            outputs=outputs,
            metrics=snapshot,
            error=error,
            messages=list(ctx.channel.history),
            paused=paused,
            checkpoint=last_checkpoint,
        )

    # ------------------------------------------------------------------ fable10 agent processes
    def _agent_host(self):
        host = getattr(self, "_host", None)
        if host is None:
            from agent_runtime.runtime.agent_host import AgentProcessHost

            host = AgentProcessHost(self.agents)
            self._host = host
        return host

    def _run_in_agent_process(self, name: str, agent: Any, inbound: AgentMessage, ctx: RunContext) -> AgentMessage:
        """Execute one stage in the Agent's own OS process; a crashed process is respawned and
        the stage retried once, then surfaced as an explicit ``agent_process_crash`` error."""
        from agent_runtime.runtime.agent_host import AgentProcessCrash

        host = self._agent_host()
        for attempt in (1, 2):
            try:
                return host.run(name, inbound, ctx)
            except AgentProcessCrash as exc:
                ctx.metrics.snapshot.agent_process_crashes += 1
                ctx.metrics.record_error()
                ctx.record_event("agent.process_crash", stage=name, payload={"agent": name, "attempt": attempt, "error": str(exc)[:200]})
        ctx.metrics.record_degradation()
        return agent._error_message(inbound, ctx, {"type": "agent_process_crash", "agent": name})

    def close_agent_processes(self) -> None:
        host = getattr(self, "_host", None)
        if host is not None:
            host.close()
            self._host = None

    @staticmethod
    def _apply_replayed_side_effects(
        stage: str, result: AgentMessage, ctx: RunContext
    ) -> None:
        """Restore local side effects represented by a cached completed response.

        A persistent idempotency hit deliberately skips the agent handler, so the
        reviewer would otherwise not call ``metrics.set_result`` or populate the final
        answer. Durable external side effects are *not* repeated here.
        """
        if stage != "reviewer":
            return
        payload = result.result or {}
        success = bool(payload.get("success", result.status == MessageStatus.OK))
        quality = float(payload.get("quality", 1.0 if success else 0.0))
        ctx.metrics.set_result(success, quality)
        ctx.blackboard["answer"] = payload.get("answer")

    def _new_stage_inputs(self, task: Task, ctx: RunContext) -> dict[str, StageInput]:
        return {
            "planner": self._initial_planner_input(task, ctx),
            "retriever": StageInput(),
            "executor": StageInput(),
            "reviewer": StageInput(),
        }

    def _runtime_signature(self, ctx: RunContext) -> dict[str, Any]:
        """影响恢复语义的非敏感配置签名。API key 不进入 checkpoint。"""
        cfg = ctx.config
        return {
            "mode": ctx.mode,
            "use_state_exchange": ctx.use_state_exchange,
            "use_memory": ctx.use_memory,
            "sandbox_enabled": ctx.sandbox is not None,
            "llm_provider": cfg.llm.provider,
            "llm_model": cfg.llm.model,
            "embedding_provider": cfg.embedding.provider,
            "embedding_model": cfg.embedding.model,
            "embedding_dim": cfg.embedding.dim,
            "vector_backend": cfg.vector_backend,
            # 计量/记忆决策也属于恢复语义：恢复后不能悄悄换 tokenizer 口径
            # 或关闭兼容门控，否则 lineage 指标与行为都不可比。
            "tokenizer_backend": cfg.tokenizer_backend,
            "memory_compatibility_gate": cfg.memory_compatibility_gate,
            "random_seed": cfg.random_seed,
            "state_shared_memory": cfg.state_shared_memory,
            "state_fd_handoff": cfg.state_fd_handoff,
            "fd_handoff_min_bytes": cfg.fd_handoff_min_bytes,
            "state_precision": cfg.state_precision,
            "state_deduplicate": cfg.state_deduplicate,
            "state_versioned_delta": cfg.state_versioned_delta,
            "reference_first_data_plane": cfg.reference_first_data_plane,
            "artifact_persistent": cfg.artifact_persistent,
            "artifact_verify_reads": cfg.artifact_verify_reads,
            "idempotency_persistent": cfg.idempotency_persistent,
            "idempotency_ttl_sec": cfg.idempotency_ttl_sec,
            "task_retry_budget": cfg.task_retry_budget,
            "agent_circuit_failure_threshold": cfg.agent_circuit_failure_threshold,
            "agent_circuit_reset_sec": cfg.agent_circuit_reset_sec,
            "memory_max_records": cfg.memory_max_records,
            "memory_recency_half_life_sec": cfg.memory_recency_half_life_sec,
            "sandbox_backend": cfg.sandbox.backend,
            "sandbox_timeout_sec": cfg.sandbox.timeout_sec,
            "sandbox_mem_mb": cfg.sandbox.mem_mb,
            "sandbox_cpu_sec": cfg.sandbox.cpu_sec,
            "sandbox_network": cfg.sandbox.network,
        }

    def _make_checkpoint(
        self,
        *,
        task: Task,
        ctx: RunContext,
        next_stage_index: int,
        stage_results: list[AgentMessage],
    ) -> WorkflowCheckpoint:
        blackboard = {
            key: ctx.blackboard[key]
            for key in _CHECKPOINT_BLACKBOARD_KEYS
            if key in ctx.blackboard
        }
        return WorkflowCheckpoint.create(
            task=task,
            trace_id=ctx.trace_id,
            next_stage_index=next_stage_index,
            pipeline_signature=self._resolved,
            runtime_signature=self._runtime_signature(ctx),
            stage_results=[m.model_dump(mode="json") for m in stage_results],
            blackboard=blackboard,
            metrics_state=ctx.metrics.export_state(),
            messages=[m.model_dump(mode="json") for m in ctx.channel.history],
        )

    def _restore_checkpoint(
        self,
        task: Task,
        ctx: RunContext,
        checkpoint: WorkflowCheckpoint,
    ) -> tuple[int, str, AgentMessage | None, list[AgentMessage], dict[str, StageInput]]:
        validate_checkpoint(
            checkpoint,
            task=task,
            pipeline_signature=self._resolved,
            runtime_signature=self._runtime_signature(ctx),
        )
        if not 0 <= checkpoint.next_stage_index <= len(PIPELINE_STAGES):
            raise ValueError("checkpoint next_stage_index out of range")
        if len(checkpoint.stage_results) < checkpoint.next_stage_index:
            raise ValueError("checkpoint missing completed stage results")

        # 保持同一 trace lineage；logger adapter 的静态上下文字段可能仍是新 context 的
        # 初始 trace，因此真正的事件关联以 AgentMessage.trace_id / RunContext.trace_id 为准。
        ctx.trace_id = checkpoint.trace_id
        # LoggerAdapter 在 context 构造时绑定 trace；恢复时同步更新，保持日志与消息同 lineage。
        if getattr(ctx.logger, "extra", None) is not None:
            ctx.logger.extra["trace_id"] = checkpoint.trace_id
            ctx.logger.extra["task_id"] = task.task_id
        ctx.blackboard["task"] = task
        ctx.blackboard.update(checkpoint.blackboard)
        ctx.metrics.restore_state(checkpoint.metrics_state)
        ctx.channel.history = [AgentMessage.model_validate(m) for m in checkpoint.messages]

        stage_results = [
            AgentMessage.model_validate(m)
            for m in checkpoint.stage_results[: checkpoint.next_stage_index]
        ]
        stage_inputs = self._new_stage_inputs(task, ctx)

        # 旧 state:// / artifact:// 句柄不能跨进程/重启直接复用；根据已完成阶段输出
        # 在新 context 中重新物化下一阶段所需引用，但不重复计入历史指标。
        for idx, result in enumerate(stage_results):
            stage, _ = PIPELINE_STAGES[idx]
            self._propagate(stage, result, stage_inputs, ctx, record_metrics=False)

        last_result = stage_results[-1] if stage_results else None
        prev_source = last_result.source_agent if last_result is not None else "orchestrator"
        return (
            checkpoint.next_stage_index,
            prev_source,
            last_result,
            stage_results,
            stage_inputs,
        )

    def _initial_planner_input(self, task: Task, ctx: RunContext) -> StageInput:
        """Planner 入站内容。"""
        if ctx.use_state_exchange:
            return StageInput(
                params={
                    "description": task.description,
                    "question": task.payload.get("question"),
                    "operands": task.payload.get("operands", []),
                    "defect": task.payload.get("defect", ""),
                }
            )
        return StageInput(params={"description": task.description, "payload": task.payload})

    def _propagate(
        self,
        name: str,
        result: AgentMessage,
        stage_inputs: dict[str, StageInput],
        ctx: RunContext,
        *,
        record_metrics: bool = True,
    ) -> None:
        """把当前阶段产出填充到下一阶段；恢复重放时可禁止重复计量。

        在引用模式下优先复用 producer 已物化的 ``state://`` / ``artifact://``。
        checkpoint 或持久幂等在新进程恢复时，旧的进程内引用可能失效；此时利用
        AgentMessage 中保留的完整结果重新物化，但不需要重新执行 Agent/工具。
        """
        res = result.result or {}
        task: Task = ctx.blackboard["task"]
        optimized = (
            ctx.use_state_exchange
            and ctx.state_exchange is not None
            and ctx.artifact_store is not None
        )

        if name == "planner":
            plan = res.get("plan", {})
            facts = task.payload.get("facts", {})
            ctx.blackboard["plan"] = plan
            if optimized:
                assert ctx.state_exchange is not None and ctx.artifact_store is not None

                state_ref = None
                candidate = result.state_reference or ctx.blackboard.get("plan_state_ref")
                if candidate and ctx.state_exchange.exists(candidate):
                    state_ref = ctx.state_exchange.get_ref(candidate)
                if state_ref is None:
                    state_ref = ctx.state_exchange.put_plan_state(plan)
                    # Keep the local response coherent after stale-reference recovery so
                    # later checkpoint/idempotency serialization sees the live URI.
                    result.state_reference = state_ref.uri

                if record_metrics:
                    ctx.metrics.record_state_transfer(
                        state_ref.materialized_bytes,
                        logical_bytes=state_ref.logical_nbytes,
                        deduplicated=state_ref.deduplicated,
                        precision=state_ref.precision,
                        transport=state_ref.transport,
                    )

                # fable14 normalisation: the collaboration state REFERENCES the immutable plan
                # state (already materialised on the state plane) instead of embedding a second
                # copy of it -- one source of truth, fewer materialised bytes.
                workflow_ref = self._advance_workflow_state(
                    "planner",
                    {"plan_ref": state_ref.uri} if getattr(ctx.config, "state_normalize", True) else {"plan": plan},
                    ctx,
                    record_metrics=record_metrics,
                )

                facts_ref = ctx.artifact_store.put(facts, summary=f"facts(n={len(facts)})")
                ctx.blackboard["facts_artifact_ref"] = facts_ref.uri
                params = {"plan_ref": state_ref.uri, "facts_ref": facts_ref.uri}
                if workflow_ref is not None:
                    params["workflow_state_ref"] = workflow_ref
                stage_inputs["retriever"] = StageInput(
                    params=params,
                    artifact_refs=[facts_ref.uri],
                    state_ref=state_ref.uri,
                )
            else:
                stage_inputs["retriever"] = StageInput(params={"plan": plan, "facts": facts})

        elif name == "retriever":
            plan = ctx.blackboard.get("plan", {})
            evidence = res.get("evidence", {})
            ctx.blackboard["evidence"] = evidence
            if optimized:
                assert ctx.state_exchange is not None and ctx.artifact_store is not None

                # Reuse the immutable plan state forwarded by Retriever.  On a fresh-process
                # replay the old URI is expected to be missing and is rematerialized from
                # the full local result/blackboard retained for recovery.
                state_ref = None
                candidate = result.state_reference or ctx.blackboard.get("plan_state_ref")
                if candidate and ctx.state_exchange.exists(candidate):
                    state_ref = ctx.state_exchange.get_ref(candidate)
                if state_ref is None:
                    state_ref = ctx.state_exchange.put_plan_state(plan)
                    result.state_reference = state_ref.uri
                    forwarded_materialized = state_ref.materialized_bytes
                    forwarded_dedup = state_ref.deduplicated
                else:
                    # Same authoritative object, another logical consumer.  No data-plane
                    # rematerialization occurs; count the handoff as a deduplicated transfer.
                    forwarded_materialized = 0
                    forwarded_dedup = True

                if record_metrics:
                    ctx.metrics.record_state_transfer(
                        forwarded_materialized,
                        logical_bytes=state_ref.logical_nbytes,
                        deduplicated=forwarded_dedup,
                        precision=state_ref.precision,
                        transport=state_ref.transport,
                    )

                ev_ref = None
                evidence_candidates = [
                    *result.artifact_references,
                    *([ctx.blackboard["evidence_artifact_ref"]]
                      if ctx.blackboard.get("evidence_artifact_ref") else []),
                ]
                for uri in evidence_candidates:
                    if ctx.artifact_store.exists(uri):
                        ev_ref = ctx.artifact_store.get_ref(uri)
                        if ev_ref is not None:
                            break
                if ev_ref is None:
                    ev_ref = _put_evidence(ctx, evidence)
                    result.artifact_references = [ev_ref.uri]

                workflow_ref = self._advance_workflow_state(
                    "retriever",
                    {
                        "evidence": {
                            "count": len(evidence) if isinstance(evidence, dict) else 0,
                            "keys": sorted(str(k) for k in evidence) if isinstance(evidence, dict) else [],
                        }
                    },
                    ctx,
                    record_metrics=record_metrics,
                )
                params = {"plan_ref": state_ref.uri, "evidence_ref": ev_ref.uri}
                if workflow_ref is not None:
                    params["workflow_state_ref"] = workflow_ref
                stage_inputs["executor"] = StageInput(
                    params=params,
                    artifact_refs=[ev_ref.uri],
                    state_ref=state_ref.uri,
                )
            else:
                stage_inputs["executor"] = StageInput(params={"plan": plan, "evidence": evidence})

        elif name == "executor":
            value = res.get("value")
            ctx.blackboard["execution"] = value
            workflow_ref = self._advance_workflow_state(
                "executor",
                {
                    "execution": {
                        "value_type": type(value).__name__,
                        "has_value": value is not None,
                    }
                },
                ctx,
                record_metrics=record_metrics,
            )
            params = {"value": value, "topic": task.topic}
            if workflow_ref is not None:
                params["workflow_state_ref"] = workflow_ref
            stage_inputs["reviewer"] = StageInput(params=params)

        elif name == "reviewer":
            self._advance_workflow_state(
                "reviewer",
                {
                    "review": {
                        "success": bool(res.get("success", result.status == MessageStatus.OK)),
                        "quality": float(res.get("quality", 0.0)),
                    }
                },
                ctx,
                record_metrics=record_metrics,
            )

    @staticmethod
    def _advance_workflow_state(
        stage: str,
        updates: dict[str, Any],
        ctx: RunContext,
        *,
        record_metrics: bool,
    ) -> str | None:
        """Advance the main pipeline's immutable versioned collaboration state.

        v0.6 implemented the delta ledger but only exercised it in a side benchmark.  v0.6.1
        makes it part of normal state-enabled execution: Planner creates the first snapshot and
        every later stage advances the same lineage.  The next Agent receives the resulting
        ``vstate://`` reference and resolves it before handling business data.

        The state deliberately contains compact coordination metadata rather than duplicating
        large evidence/artifact payloads.  Those continue to live on the existing data plane.
        """

        if not ctx.use_state_exchange or ctx.state_exchange is None:
            return None

        previous = ctx.blackboard.get("workflow_state")
        if isinstance(previous, dict):
            state: dict[str, Any] = dict(previous)
            state.update(updates)
            state["stage"] = stage
            base_uri = ctx.blackboard.get("workflow_state_ref")
            if (
                ctx.config.state_versioned_delta
                and isinstance(base_uri, str)
                and ctx.state_exchange.get_versioned_ref(base_uri)
            ):
                ref = ctx.state_exchange.update_versioned_state(
                    base_uri,
                    state,
                    require_current_head=True,
                )
            else:
                # Fresh-process checkpoint/idempotency replay reconstructs the lineage from
                # completed stage results, so a missing old URI becomes a new verified snapshot.
                ref = ctx.state_exchange.put_versioned_state(state)
        else:
            # With normalisation the task identity is carried by the ledger namespace (the
            # ``vstate://<task_id>/...`` URI), so it is not repeated inside the document.
            state = {"stage": stage, **updates} if getattr(ctx.config, "state_normalize", True) else {
                "task_id": ctx.task_id,
                "stage": stage,
                **updates,
            }
            ref = ctx.state_exchange.put_versioned_state(state)

        ctx.blackboard["workflow_state"] = state
        ctx.blackboard["workflow_state_ref"] = ref.uri
        if record_metrics:
            ctx.metrics.record_versioned_state_transfer(
                ref.materialized_bytes,
                logical_bytes=ref.logical_bytes,
                kind=ref.kind,
                deduplicated=ref.deduplicated,
            )
        ctx.record_event(
            "versioned_state.advanced",
            stage=stage,
            payload={
                "uri": ref.uri,
                "kind": ref.kind,
                "base_uri": ref.base_uri,
                "materialized_bytes": ref.materialized_bytes,
                "logical_bytes": ref.logical_bytes,
                "delta_ops": ref.delta_ops,
            },
        )
        return ref.uri


def _put_evidence(ctx: RunContext, evidence: Any):
    """Materialize an exact evidence subset as a compact projection of facts when cheaper."""
    facts_uri = ctx.blackboard.get("facts_artifact_ref")
    store = ctx.artifact_store
    if isinstance(evidence, dict) and facts_uri and store is not None and store.exists(facts_uri):
        try:
            facts = store.get_json(facts_uri)
        except Exception:  # noqa: BLE001
            facts = None
        if isinstance(facts, dict) and all(k in facts and facts[k] == v for k, v in evidence.items()):
            import json as _json
            if evidence == facts:
                return store.put(evidence, summary=f"evidence(n={len(evidence)})")
            full = len(_json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
            view = len(facts_uri) + sum(len(str(k)) + 3 for k in evidence) + 40
            if view < full:
                return store.put_projection(facts_uri, list(evidence), summary=f"evidence(n={len(evidence)})")
    return store.put(evidence, summary=f"evidence(n={len(evidence) if isinstance(evidence, dict) else 0})")


_ACTION_FOR_STAGE = {
    "planner": ActionType.PLAN,
    "retriever": ActionType.RETRIEVE,
    "executor": ActionType.EXECUTE,
    "reviewer": ActionType.REVIEW,
}


__all__ = ["Orchestrator", "RunResult"]
