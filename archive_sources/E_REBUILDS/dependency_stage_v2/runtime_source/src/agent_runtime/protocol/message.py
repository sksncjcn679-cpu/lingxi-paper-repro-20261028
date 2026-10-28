"""结构化通信协议消息模型。

`AgentMessage` 是 Agent 间通信的**唯一真源**：
- `structured_mode` 直接传输该结构（紧凑、引用化）；
- `text_mode` 通过映射器把同一消息渲染为自然语言文本后透传。

这样保证两种模式在「相同任务、相同逻辑」下公平对比，差异仅来自通信载体。
"""

from __future__ import annotations

import time
import uuid
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from agent_runtime import PROTOCOL_VERSION


class ActionType(str, Enum):
    """动作类型（高密度语义单元，替代自然语言意图描述）。"""

    REGISTER = "register"
    HANDSHAKE = "handshake"
    DISCOVER = "discover"
    PLAN = "plan"
    RETRIEVE = "retrieve"
    EXECUTE = "execute"
    REVIEW = "review"
    SUMMARIZE = "summarize"
    MEMORIZE = "memorize"
    RESULT = "result"
    ERROR = "error"
    ACK = "ack"


class MessageStatus(str, Enum):
    """消息/步骤状态。"""

    PENDING = "pending"
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    RETRY = "retry"
    SKIPPED = "skipped"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class AgentMessage(BaseModel):
    """Agent 间结构化消息（全字段契约）。"""

    protocol_version: str = Field(default=PROTOCOL_VERSION, min_length=1)
    message_id: str = Field(default_factory=lambda: _new_id("msg"), min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    step_id: str = ""

    # 因果/上下文链：兼容 A2A 风格 context 与分布式 tracing 语义。
    context_id: str | None = None
    parent_message_id: str | None = None
    causation_id: str | None = None
    sequence_no: int | None = Field(default=None, ge=0)
    # 绝对 Unix 时间；到期后 Agent 在进入业务 handle 前拒绝执行。
    # NaN/Infinity 会生成非标准 JSON，必须在协议模型边界拒绝。
    deadline_at: float | None = Field(default=None, allow_inf_nan=False)

    source_agent: str = Field(min_length=1)
    target_agent: str = Field(min_length=1)
    timestamp: float = Field(default_factory=time.time, allow_inf_nan=False)

    action_type: ActionType
    capability: str = ""

    input_parameters: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    status: MessageStatus = MessageStatus.PENDING

    dependencies: list[str] = Field(default_factory=list)

    # 引用而非搬运：长内容以引用 + 摘要承载。
    artifact_references: list[str] = Field(default_factory=list)
    evidence_references: list[str] = Field(default_factory=list)
    state_reference: str | None = None

    confidence: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    error: dict[str, Any] | None = None

    # 通信/计算开销度量（tokens/bytes/latency 等）。
    metrics: dict[str, Any] = Field(default_factory=dict)

    # 幂等键：作为逻辑请求命名空间；实际 fingerprint 还绑定业务 payload/引用/目标，
    # 防止“同 key 不同请求”静默回放旧结果。
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def _validate_json_native_payloads(self) -> "AgentMessage":
        """Reject payload values that cannot cross the declared JSON wire contract.

        Nested ``Any`` fields otherwise allow NaN/Infinity or arbitrary Python objects
        to survive model construction and fail only much later during serialization,
        hashing or checkpointing.  Failing at the protocol boundary keeps all transports
        and content-addressed stores on one deterministic JSON meaning.
        """
        # Protocol timestamps have canonical microsecond precision.  Normalizing at model
        # construction keeps JSON/MessagePack/text renderings semantically identical while
        # avoiding run-to-run wire-size jitter from arbitrary float repr lengths.
        self.timestamp = round(float(self.timestamp), 6)
        if self.deadline_at is not None:
            self.deadline_at = round(float(self.deadline_at), 6)
        for name in ("input_parameters", "result", "error", "metrics"):
            value = getattr(self, name)
            if value is None:
                continue
            try:
                # fable16: validation only needs serialisability, not a canonical order.
                json.dumps(value, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must contain JSON-native values with finite numbers") from exc
        return self

    def short(self) -> str:
        """用于日志的简短描述。"""
        return (
            f"{self.source_agent}->{self.target_agent} "
            f"{self.action_type.value} [{self.status.value}] "
            f"task={self.task_id} step={self.step_id}"
        )


__all__ = ["AgentMessage", "ActionType", "MessageStatus"]
