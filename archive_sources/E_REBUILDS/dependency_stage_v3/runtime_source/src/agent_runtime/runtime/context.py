"""运行上下文：在一次任务运行中贯穿所有模块的共享依赖与服务。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from agent_runtime.config import Config
from agent_runtime.observability.metrics import MetricsCollector
from agent_runtime.providers.base import EmbeddingProvider, LLMProvider, LLMResponse
from agent_runtime.runtime.channel import Channel


# sota1: the same contract expressed once for a coalesced (vectored) request.
_COALESCED_HINTS = {
    "none": "",
    "concise": "each answer at most 2 short sentences.",
    "json": 'each answer is a compact JSON object {"decision": str, "reason": str} on one line; reason at most 15 words.',
}

_OUTPUT_CONTRACTS = {
    "none": "",
    "concise": (
        "\n\nAnswer in at most 2 short sentences."
    ),
    "json": (
        "\n\nRespond ONLY with a compact JSON object "
        '{"decision": str, "reason": str} where reason has at most 15 words. '
        "No markdown, no extra text."
    ),
}

if TYPE_CHECKING:  # 避免循环导入；这些服务在后续阶段注入
    from agent_runtime.artifact_store.store import ArtifactStore
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.sandbox.executor import CodeActExecutor
    from agent_runtime.state_exchange.exchange import StateExchange
    from agent_runtime.runtime.event_log import EventJournal
    from agent_runtime.runtime.resilience import RetryBudget


@dataclass
class RunContext:
    """一次任务运行的上下文。"""

    config: Config
    llm: LLMProvider
    embedding: EmbeddingProvider
    metrics: MetricsCollector
    channel: Channel
    mode: str  # "text" | "structured"
    task_id: str
    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex[:12]}")
    rng: np.random.Generator = field(default_factory=lambda: np.random.default_rng(42))
    logger: Any = None

    # 跨 Agent 共享的黑板（步骤结果）。
    blackboard: dict[str, Any] = field(default_factory=dict)

    # 实验开关
    use_memory: bool = False
    use_state_exchange: bool = False

    # 可选服务（后续阶段注入）
    artifact_store: ArtifactStore | None = None
    artifact_store_owned: bool = False
    memory: MemoryStore | None = None
    memory_owned: bool = False
    state_exchange: StateExchange | None = None
    sandbox: CodeActExecutor | None = None
    event_journal: EventJournal | None = None
    retry_budget: RetryBudget | None = None
    state_fd_transport: Any = None
    # Explicitly owned by the caller across tasks, never closed by per-task cleanup.
    candidate_session: Any = None
    # sota1: per-task LLM gateway (call coalescing).  ``None`` = direct calls only.
    llm_gateway: Any = None

    def record_event(
        self,
        event_type: str,
        *,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append a runtime event when journaling is enabled."""
        if self.event_journal is None:
            return
        self.event_journal.append(
            event_type,
            trace_id=self.trace_id,
            task_id=self.task_id,
            stage=stage,
            payload=payload or {},
        )

    def call_llm(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        deferrable: bool = False,
        grade: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """调用 LLM 并自动记录 token 指标。

        ``deferrable=True`` declares that the caller does not read the returned text on its
        critical path.  With coalescing enabled the call is queued in the per-task
        :class:`LLMGateway` and issued together with the other queued calls at the task
        commit barrier (see :meth:`flush_llm`); the returned placeholder has empty text.
        """
        if getattr(self.config, "llm_context_mode", "none") == "inbound":
            inbound_id = getattr(self, "current_inbound_id", None)
            view = getattr(self.channel, "llm_view", None)
            wire = (
                view(inbound_id) if callable(view) else getattr(self.channel, "last_wire_by_id", {}).get(inbound_id)
            ) if inbound_id else None
            if wire and getattr(self.config, "llm_view", "wire") == "semantic":
                from agent_runtime.protocol.llm_view import semantic_view

                wire = semantic_view(wire)
            if wire:
                prompt = f"Inbound message:\n{wire}\n\nTask: {prompt}"
        eff_temperature = temperature if temperature is not None else self.config.llm.temperature
        eff_max_tokens = max_tokens if max_tokens is not None else self.config.llm.max_tokens
        gateway = self.llm_gateway
        if deferrable and gateway is not None and getattr(gateway, "enabled", False):
            # The contract is stated once for the whole vectored request, not per item.
            return gateway.submit(
                prompt, system=system, temperature=eff_temperature, max_tokens=eff_max_tokens,
                grade=grade, sink=self.metrics if grade else None,
            )
        if (
            not deferrable
            and gateway is not None
            and getattr(gateway, "enabled", False)
            and getattr(gateway, "pending", 0)
            and getattr(self.config, "llm_piggyback", True)
        ):
            # OS-SOTA1.1 piggybacking: a critical-path call does not pay its own hidden
            # per-request overhead *and* leave queued advisory items for another request --
            # it carries them (like a piggybacked TCP ACK).  The critical answer is returned
            # synchronously; queued items are answered in the same provider request.
            return self._piggyback_call(prompt, system, eff_temperature, eff_max_tokens, grade)
        contract = _OUTPUT_CONTRACTS[self.config.llm.output_contract]
        prompt = f"{prompt}{contract}"
        resp = self.llm.complete(
            prompt,
            system=system,
            temperature=eff_temperature,
            max_tokens=eff_max_tokens,
        )
        self._record_provider_response(resp, logical_requests=1)
        if grade:
            apply_grade(grade, resp.text, self.metrics)
        return resp

    def _record_provider_response(self, resp: LLMResponse, *, logical_requests: int, repair_items: int = 0) -> None:
        md = resp.metadata or {}
        self.metrics.record_llm(
            resp.prompt_tokens,
            resp.completion_tokens,
            int(md.get("cached_prompt_tokens", 0) or 0),
            logical_requests=logical_requests,
            reasoning_tokens=int(md.get("reasoning_tokens", 0) or 0),
            cap_violation=bool(md.get("cap_violation", False)),
            visible_prompt_tokens=md.get("visible_prompt_tokens", md.get("runtime_visible_prompt_tokens")),
            projected_latency_sec=float(md.get("projected_latency_sec", 0.0) or 0.0),
            repair_items=int(md.get("repair_items", 0) or 0) if "repair_items" in md else repair_items,
        )

    def _piggyback_call(self, prompt, system, temperature, max_tokens, grade) -> LLMResponse:
        gateway = self.llm_gateway
        placeholder = gateway.submit(prompt, system=system, temperature=temperature, max_tokens=max_tokens,
                                     grade=grade, sink=self.metrics if grade else None)
        ticket = placeholder.metadata["ticket"]
        responses = gateway.flush()
        for resp in responses:
            md = resp.metadata or {}
            self._record_provider_response(resp, logical_requests=int(md.get("coalesced_items", 1)),
                                           repair_items=int(md.get("repair_items", 0) or 0))
            if md.get("coalesce_parse_misses"):
                self.metrics.record_llm_parse_misses(int(md["coalesce_parse_misses"]))
        self.metrics.snapshot.llm_piggybacked_calls += 1
        last = responses[-1] if responses else placeholder
        return LLMResponse(text=gateway.results.get(ticket, ""), prompt_tokens=0, completion_tokens=0,
                           model=last.model, metadata={"piggybacked": True, "provider_requests": len(responses)})

    def flush_llm(self) -> int:
        """Issue every queued deferrable LLM call (task commit barrier); returns requests made."""
        gateway = self.llm_gateway
        if gateway is None or not getattr(gateway, "pending", 0):
            return 0
        if getattr(gateway, "mode", "task") == "session":
            return 0  # group commit: the session owner flushes (llm_gateway.flush_session)
        responses = gateway.flush()
        for resp in responses:
            md = resp.metadata or {}
            self._record_provider_response(resp, logical_requests=int(md.get("coalesced_items", 1)),
                                           repair_items=int(md.get("repair_items", 0) or 0))
            misses = int((resp.metadata or {}).get("coalesce_parse_misses", 0) or 0)
            if misses:
                self.metrics.record_llm_parse_misses(misses)
        return len(responses)

    def record_tool(self, signature: str) -> None:
        """记录一次工具调用（用于重复计算统计）。"""
        self.metrics.record_tool_call(signature)


def grade_text(grade: dict[str, Any], text: str) -> bool:
    """Declarative grading of an LLM answer (``kind=label``: first token equals expected)."""
    if grade.get("kind") != "label":
        raise ValueError(f"unknown grade kind: {grade.get('kind')!r}")
    words = (text or "").strip().split()
    predicted = words[0].strip(".,;:\"'`*()[]").lower() if words else ""
    return predicted == str(grade.get("expected", "")).strip().lower()


def apply_grade(grade: dict[str, Any], text: str, metrics: MetricsCollector) -> None:
    metrics.record_llm_judgement(correct=grade_text(grade, text))


__all__ = ["RunContext", "apply_grade", "grade_text"]
