"""Agent 基类：生命周期、状态机、超时、重试与失败隔离。"""

from __future__ import annotations

import abc
import asyncio
import math
import time
from dataclasses import dataclass
from enum import Enum

from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.runtime.context import RunContext
from agent_runtime.runtime.resilience import CircuitBreaker, CircuitState


class AgentState(str, Enum):
    """Agent 生命周期状态。"""

    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    TERMINATED = "terminated"


@dataclass
class RetryPolicy:
    """重试策略（确定性：默认零退避以保证可复现）。"""

    max_retries: int = 2
    backoff_sec: float = 0.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if not math.isfinite(self.backoff_sec) or self.backoff_sec < 0:
            raise ValueError("backoff_sec must be a finite non-negative number")


class BaseAgent(abc.ABC):
    """所有 Agent 的抽象基类。

    子类实现 `handle`；外部统一通过 `run` 调用，由 `run` 负责状态转换、超时控制、
    重试与失败隔离（失败时返回 error 消息，而非向上抛出导致整个运行时崩溃）。
    """

    def __init__(
        self,
        name: str,
        capabilities: list[str],
        *,
        capability_specs: list | None = None,
        actions: list[ActionType] | None = None,
        timeout_sec: float = 10.0,
        retry: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        if not math.isfinite(timeout_sec) or timeout_sec <= 0:
            raise ValueError("timeout_sec must be a finite positive number")
        self.name = name
        self.capabilities = capabilities
        self.capability_specs = list(capability_specs or [])
        self.actions = actions or []
        self.timeout_sec = timeout_sec
        self.retry = retry or RetryPolicy()
        self.circuit_breaker = circuit_breaker
        self._state = AgentState.CREATED

    @property
    def state(self) -> AgentState:
        return self._state

    def card(self):
        """生成用于注册/握手/能力发现的 Agent 名片。"""
        from agent_runtime.registry.capability import AgentCard

        return AgentCard(
            agent_name=self.name,
            capabilities=list(self.capabilities),
            capability_specs=list(self.capability_specs),
            actions=[a.value for a in self.actions],
            description=self.__class__.__doc__ or "",
        )

    # ----- 生命周期钩子（子类可覆盖） -----
    async def on_start(self, ctx: RunContext) -> None:
        self._state = AgentState.READY

    async def on_stop(self, ctx: RunContext) -> None:
        self._state = AgentState.TERMINATED

    # ----- 业务处理（子类实现） -----
    @abc.abstractmethod
    async def handle(self, message: AgentMessage, ctx: RunContext) -> AgentMessage:
        """处理一条入站消息并返回结果消息。"""
        raise NotImplementedError

    # ----- 统一执行包装 -----
    async def run(self, message: AgentMessage, ctx: RunContext) -> AgentMessage:
        """带 deadline、局部 retry、任务级 retry budget 与 failure circuit 执行 handle。"""
        attempt = 0
        last_error: dict[str, str] | None = None
        ctx.current_inbound_id = message.message_id
        while True:
            # Circuit is checked before touching business logic so a known-bad dependency
            # cannot keep consuming scarce retry/deadline budget.
            if self.circuit_breaker is not None and not self.circuit_breaker.allow_request():
                ctx.metrics.record_circuit_rejection()
                ctx.metrics.record_degradation()
                self._state = AgentState.FAILED
                ctx.record_event(
                    "agent.circuit_rejected",
                    stage=self.name,
                    payload={"agent": self.name, "attempt": attempt},
                )
                return self._error_message(
                    message,
                    ctx,
                    {"type": "circuit_open", "agent": self.name},
                )

            # Message deadline is an end-to-end budget, unlike the per-agent timeout.
            # Recompute before every retry so backoff/retries cannot silently exceed it.
            timeout_sec = self.timeout_sec
            if message.deadline_at is not None:
                remaining = float(message.deadline_at) - time.time()
                if remaining <= 0:
                    ctx.metrics.record_error()
                    ctx.metrics.record_degradation()
                    self._state = AgentState.TIMEOUT
                    return self._error_message(
                        message,
                        ctx,
                        {"type": "deadline_exceeded", "agent": self.name},
                    )
                timeout_sec = min(timeout_sec, remaining)

            self._state = AgentState.RUNNING
            start = time.perf_counter()
            try:
                result = await asyncio.wait_for(self.handle(message, ctx), timeout=timeout_sec)
                ctx.metrics.record_step_latency(time.perf_counter() - start)
                ctx.metrics.sample_resources()
                if self.circuit_breaker is not None:
                    self.circuit_breaker.record_success()
                self._state = AgentState.SUCCEEDED
                return result
            except asyncio.TimeoutError:
                ctx.metrics.record_error()
                self._state = AgentState.TIMEOUT
                last_error = {"type": "timeout", "agent": self.name}
            except Exception as exc:  # noqa: BLE001 - 失败隔离：捕获所有异常
                ctx.metrics.record_error()
                self._state = AgentState.FAILED
                last_error = {"type": exc.__class__.__name__, "msg": str(exc)}

            if self.circuit_breaker is not None:
                state = self.circuit_breaker.record_failure()
                if state is CircuitState.OPEN:
                    ctx.metrics.record_degradation()
                    ctx.record_event(
                        "agent.circuit_opened",
                        stage=self.name,
                        payload={
                            "agent": self.name,
                            "failure_threshold": self.circuit_breaker.failure_threshold,
                        },
                    )
                    error = dict(last_error or {})
                    error["resilience"] = "circuit_open"
                    return self._error_message(message, ctx, error)

            attempt += 1
            if attempt > self.retry.max_retries:
                ctx.metrics.record_degradation()
                return self._error_message(message, ctx, last_error or {})

            if ctx.retry_budget is not None and not ctx.retry_budget.consume():
                ctx.metrics.record_retry_budget_exhausted()
                ctx.metrics.record_degradation()
                ctx.record_event(
                    "agent.retry_budget_exhausted",
                    stage=self.name,
                    payload={
                        "agent": self.name,
                        "used": ctx.retry_budget.used,
                        "max_retries": ctx.retry_budget.max_retries,
                    },
                )
                error = dict(last_error or {})
                error["resilience"] = "retry_budget_exhausted"
                return self._error_message(message, ctx, error)

            ctx.metrics.record_retry()
            ctx.metrics.record_degradation()
            ctx.record_event(
                "agent.retry_scheduled",
                stage=self.name,
                payload={"agent": self.name, "attempt": attempt},
            )
            if self.retry.backoff_sec > 0:
                # Never sleep past an end-to-end deadline.
                if message.deadline_at is not None:
                    remaining = float(message.deadline_at) - time.time()
                    if remaining <= 0:
                        return self._error_message(
                            message,
                            ctx,
                            {"type": "deadline_exceeded", "agent": self.name},
                        )
                    await asyncio.sleep(min(self.retry.backoff_sec, remaining))
                else:
                    await asyncio.sleep(self.retry.backoff_sec)

    def _error_message(
        self, inbound: AgentMessage, ctx: RunContext, error: dict[str, str]
    ) -> AgentMessage:
        status = (
            MessageStatus.TIMEOUT
            if error.get("type") in {"timeout", "deadline_exceeded"}
            else MessageStatus.ERROR
        )
        return AgentMessage(
            trace_id=ctx.trace_id,
            task_id=ctx.task_id,
            step_id=inbound.step_id,
            context_id=inbound.context_id,
            parent_message_id=inbound.message_id,
            causation_id=inbound.message_id,
            sequence_no=(inbound.sequence_no + 1) if inbound.sequence_no is not None else None,
            deadline_at=inbound.deadline_at,
            source_agent=self.name,
            target_agent=inbound.source_agent or "orchestrator",
            action_type=ActionType.ERROR,
            capability="",
            status=status,
            error=error,
            confidence=0.0,
        )

    def reply(
        self,
        inbound: AgentMessage,
        ctx: RunContext,
        *,
        action_type: ActionType,
        result: dict | None = None,
        status: MessageStatus = MessageStatus.OK,
        capability: str = "",
        confidence: float | None = None,
        artifact_references: list[str] | None = None,
        evidence_references: list[str] | None = None,
        state_reference: str | None = None,
    ) -> AgentMessage:
        """构造一条回复消息（填充 trace/task/step 等公共字段）。"""
        return AgentMessage(
            trace_id=ctx.trace_id,
            task_id=ctx.task_id,
            step_id=inbound.step_id,
            context_id=inbound.context_id,
            parent_message_id=inbound.message_id,
            causation_id=inbound.message_id,
            sequence_no=(inbound.sequence_no + 1) if inbound.sequence_no is not None else None,
            deadline_at=inbound.deadline_at,
            source_agent=self.name,
            target_agent=inbound.source_agent or "orchestrator",
            action_type=action_type,
            capability=capability or (self.capabilities[0] if self.capabilities else ""),
            result=result,
            status=status,
            confidence=confidence,
            artifact_references=artifact_references or [],
            evidence_references=evidence_references or [],
            state_reference=state_reference,
        )

    def consume_workflow_state(
        self,
        inbound: AgentMessage,
        ctx: RunContext,
        *,
        expected_stage: str | None = None,
    ) -> dict | None:
        """Resolve the versioned control-state reference carried by the main pipeline.

        The versioned ledger is intentionally separate from the numerical ``state://`` plan
        representation: it tracks evolving collaboration metadata across stage boundaries.
        Consumers verify the latest immutable version before business logic continues.  A stale
        or corrupt reference degrades gracefully to the existing plan/artifact path rather than
        pretending the versioned state was consumed.
        """

        uri = inbound.input_parameters.get("workflow_state_ref")
        if not uri or ctx.state_exchange is None:
            return None
        try:
            value = ctx.state_exchange.get_versioned_state(str(uri))
        except Exception:  # noqa: BLE001 - integrity/key errors are a controlled degradation
            ctx.metrics.record_degradation()
            ctx.record_event(
                "versioned_state.resolve_failed",
                stage=self.name,
                payload={"uri": str(uri)},
            )
            return None
        if not isinstance(value, dict):
            ctx.metrics.record_degradation()
            return None
        if expected_stage is not None and value.get("stage") != expected_stage:
            ctx.metrics.record_degradation()
            ctx.record_event(
                "versioned_state.stage_mismatch",
                stage=self.name,
                payload={
                    "uri": str(uri),
                    "expected": expected_stage,
                    "actual": value.get("stage"),
                },
            )
            return None
        ctx.blackboard["workflow_state_last_consumed"] = {
            "agent": self.name,
            "uri": str(uri),
            "stage": value.get("stage"),
        }
        return value


__all__ = ["BaseAgent", "AgentState", "RetryPolicy"]
