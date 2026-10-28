"""多 Agent 运行时公共 API。

公共符号采用惰性导入，避免 ``runtime.errors`` 等低层模块被导入时反向拉起
builder/agents/protocol，形成包级循环依赖。对调用方仍保持原有导入方式：
``from agent_runtime.runtime import Orchestrator, Task``。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_runtime.runtime.agent import AgentState, BaseAgent, RetryPolicy
    from agent_runtime.runtime.builder import build_context, build_default_agents, run_task
    from agent_runtime.runtime.checkpoint import FileCheckpointStore, WorkflowCheckpoint
    from agent_runtime.runtime.context import RunContext
    from agent_runtime.runtime.orchestrator import Orchestrator, RunResult
    from agent_runtime.runtime.task import Task, VerdictResult

_EXPORTS = {
    "AgentState": ("agent_runtime.runtime.agent", "AgentState"),
    "BaseAgent": ("agent_runtime.runtime.agent", "BaseAgent"),
    "RetryPolicy": ("agent_runtime.runtime.agent", "RetryPolicy"),
    "RunContext": ("agent_runtime.runtime.context", "RunContext"),
    "WorkflowCheckpoint": ("agent_runtime.runtime.checkpoint", "WorkflowCheckpoint"),
    "FileCheckpointStore": ("agent_runtime.runtime.checkpoint", "FileCheckpointStore"),
    "Orchestrator": ("agent_runtime.runtime.orchestrator", "Orchestrator"),
    "RunResult": ("agent_runtime.runtime.orchestrator", "RunResult"),
    "Task": ("agent_runtime.runtime.task", "Task"),
    "VerdictResult": ("agent_runtime.runtime.task", "VerdictResult"),
    "build_context": ("agent_runtime.runtime.builder", "build_context"),
    "build_default_agents": ("agent_runtime.runtime.builder", "build_default_agents"),
    "run_task": ("agent_runtime.runtime.builder", "run_task"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = list(_EXPORTS)
