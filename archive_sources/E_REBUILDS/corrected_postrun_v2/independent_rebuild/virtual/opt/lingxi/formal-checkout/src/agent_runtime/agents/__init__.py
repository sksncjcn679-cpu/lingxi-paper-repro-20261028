"""默认 Agent 实现的公共 API（惰性导入，降低包级耦合）。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_runtime.agents.codeact_executor import CodeActExecutorAgent
    from agent_runtime.agents.planner import PlannerAgent
    from agent_runtime.agents.retriever import RetrieverAgent
    from agent_runtime.agents.reviewer import ReviewerSummarizerAgent

_EXPORTS = {
    "PlannerAgent": ("agent_runtime.agents.planner", "PlannerAgent"),
    "RetrieverAgent": ("agent_runtime.agents.retriever", "RetrieverAgent"),
    "CodeActExecutorAgent": ("agent_runtime.agents.codeact_executor", "CodeActExecutorAgent"),
    "ReviewerSummarizerAgent": ("agent_runtime.agents.reviewer", "ReviewerSummarizerAgent"),
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
