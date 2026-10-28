"""任务模型。

`Task` 描述一次多步骤协作任务的输入与验收方式。场景（scenarios）通过构造
`Task` 来驱动运行时，并提供确定性的自动验收函数，保证实验可复现。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
import math
import json
from typing import Any


@dataclass
class VerdictResult:
    """任务自动验收结果。"""

    success: bool
    quality: float  # 0.0 ~ 1.0
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.quality) or not 0.0 <= self.quality <= 1.0:
            raise ValueError("verdict quality must be finite and within [0, 1]")


# 验收函数：输入最终输出 dict，返回 VerdictResult。
Verifier = Callable[[dict[str, Any]], VerdictResult]


@dataclass
class Task:
    """一次协作任务。"""

    topic: str
    description: str
    payload: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    verifier: Verifier | None = None
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:10]}")
    # 关联任务：用于跨任务记忆复用验证（如 A1->A2）。
    parent_task_id: str | None = None
    # 多轮/多任务共享的逻辑上下文；为空时运行时回退到 parent_task_id/task_id。
    context_id: str | None = None
    # 可选绝对 Unix deadline；运行时逐阶段向消息传播。
    deadline_at: float | None = None
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("task topic must be non-empty")
        if not self.description.strip():
            raise ValueError("task description must be non-empty")
        if not self.task_id.strip():
            raise ValueError("task_id must be non-empty")
        if self.deadline_at is not None and not math.isfinite(self.deadline_at):
            raise ValueError("task deadline_at must be finite when provided")
        for field_name, value in (("payload", self.payload), ("expected", self.expected)):
            try:
                json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"task {field_name} must contain JSON-native values with finite numbers"
                ) from exc
        if any(not isinstance(tag, str) or not tag.strip() for tag in self.tags):
            raise ValueError("task tags must be non-empty strings")
        if self.parent_task_id is not None and not self.parent_task_id.strip():
            raise ValueError("parent_task_id must be non-empty when provided")
        if self.context_id is not None and not self.context_id.strip():
            raise ValueError("context_id must be non-empty when provided")

    def verify(self, outputs: dict[str, Any]) -> VerdictResult:
        """对输出进行自动验收。"""
        if self.verifier is not None:
            return self.verifier(outputs)
        # 默认验收：将 outputs['answer'] 与 expected['answer'] 比较。
        ans = outputs.get("answer")
        exp = self.expected.get("answer")
        if exp is None:
            return VerdictResult(success=bool(ans), quality=1.0 if ans else 0.0)
        ok = ans == exp
        return VerdictResult(
            success=ok,
            quality=1.0 if ok else 0.0,
            detail={"answer": ans, "expected": exp},
        )


__all__ = ["Task", "VerdictResult", "Verifier"]
