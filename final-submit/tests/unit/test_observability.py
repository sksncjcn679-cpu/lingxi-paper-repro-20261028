"""可观测性关联字段与事件字段合并回归测试。"""

from __future__ import annotations

import logging

from agent_runtime.config import load_config
from agent_runtime.observability.logging import ContextLoggerAdapter
from agent_runtime.runtime.builder import build_context


def test_logger_adapter_merges_context_and_event_fields():
    adapter = ContextLoggerAdapter(logging.getLogger("test"), {"trace_id": "trace_x", "task_id": "t"})
    _, kwargs = adapter.process("event", {"extra": {"tokens": 7, "chars": 21}})
    assert kwargs["extra"] == {
        "trace_id": "trace_x",
        "task_id": "t",
        "tokens": 7,
        "chars": 21,
    }


def test_build_context_uses_same_trace_id_in_logger_and_messages_context():
    ctx = build_context(load_config(load_env=False), "structured", task_id="task_obs")
    assert ctx.trace_id.startswith("trace_")
    assert ctx.logger.extra["trace_id"] == ctx.trace_id
    assert ctx.logger.extra["task_id"] == "task_obs"
