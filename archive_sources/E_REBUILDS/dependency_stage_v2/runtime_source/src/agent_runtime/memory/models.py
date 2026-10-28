"""共享记忆数据模型。"""

from __future__ import annotations

import time
import uuid
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class MemoryType(str, Enum):
    """记忆类型。"""

    WORKING = "working"  # 当前任务的临时上下文
    EPISODIC = "episodic"  # 具体任务经历（轨迹、证据）
    SEMANTIC = "semantic"  # 抽象事实/知识
    PROCEDURAL = "procedural"  # 可复用的策略/补丁模板/流程


def _new_memory_id() -> str:
    return f"mem_{uuid.uuid4().hex[:12]}"


class MemoryRecord(BaseModel):
    """统一记忆单元。"""

    # ----- 必选基本元数据（赛题要求） -----
    memory_id: str = Field(default_factory=_new_memory_id, min_length=1)
    source_agent: str = Field(min_length=1)
    created_at: float = Field(default_factory=time.time, allow_inf_nan=False)
    task_topic: str = Field(min_length=1)
    summary: str = Field(min_length=1)

    # ----- 建议元数据 -----
    memory_type: MemoryType = MemoryType.EPISODIC
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    embedding_ref: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, allow_inf_nan=False)
    quality_score: float = Field(default=0.8, ge=0.0, le=1.0, allow_inf_nan=False)
    # 与 query 无关的保留价值信号；默认中性。
    importance_score: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)
    reuse_count: int = Field(default=0, ge=0)
    # Feedback-calibrated reuse reliability. These counters describe observed downstream
    # outcomes after this memory was actually adopted, not mere retrieval hits.
    effective_reuse_count: int = Field(default=0, ge=0)
    harmful_reuse_count: int = Field(default=0, ge=0)
    quarantined: bool = False
    last_accessed_at: float | None = Field(default=None, allow_inf_nan=False)
    version: int = Field(default=1, ge=1)
    parent_version: str | None = None
    expiration_policy: Literal["none", "ttl", "task"] = "none"
    # ttl 策略使用绝对 Unix 时间，避免进程重启后 TTL 语义漂移。
    expires_at: float | None = Field(default=None, allow_inf_nan=False)
    provenance: dict[str, Any] = Field(default_factory=dict)
    task_id: str = ""
    success_feedback: bool | None = None

    # ----- 载荷（结构化结论/策略，便于复用时直接取用） -----
    payload: dict[str, Any] = Field(default_factory=dict)
    # Layered-memory provenance. Defaults keep old SQLite records loadable.
    derived_from: list[str] = Field(default_factory=list)
    conflict: bool = False

    @model_validator(mode="after")
    def _validate_json_payloads(self) -> "MemoryRecord":
        """Keep durable memory on the same strict JSON contract as protocol/task data."""
        for name in ("provenance", "payload"):
            value = getattr(self, name)
            try:
                json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{name} must contain JSON-native values with finite numbers"
                ) from exc
        return self

    def content_signature(self) -> str:
        """用于去重的内容签名（规范化 topic + summary）。"""
        return f"{self.task_topic.strip().lower()}|{self.summary.strip().lower()}"


__all__ = ["MemoryRecord", "MemoryType"]
