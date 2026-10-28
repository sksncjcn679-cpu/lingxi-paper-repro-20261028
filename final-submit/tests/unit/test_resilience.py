from __future__ import annotations

import asyncio

from agent_runtime.config import load_config
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.runtime.agent import BaseAgent, RetryPolicy
from agent_runtime.runtime.builder import build_context
from agent_runtime.runtime.resilience import CircuitBreaker, CircuitState, RetryBudget


class _AlwaysFailAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__("failer", ["fail"], retry=kwargs.pop("retry", RetryPolicy(max_retries=5)), **kwargs)
        self.calls = 0

    async def handle(self, message, ctx):
        self.calls += 1
        raise RuntimeError("boom")


def _msg():
    return AgentMessage(
        trace_id="trace-test",
        task_id="task-test",
        step_id="s1",
        source_agent="orchestrator",
        target_agent="failer",
        action_type=ActionType.EXECUTE,
        status=MessageStatus.PENDING,
    )


def test_retry_budget_caps_retries_across_agent_policy():
    cfg = load_config(load_env=False, task_retry_budget=1)
    ctx = build_context(cfg, "structured", task_id="task-test")
    agent = _AlwaysFailAgent(retry=RetryPolicy(max_retries=5))
    result = asyncio.run(agent.run(_msg(), ctx))
    assert result.status is MessageStatus.ERROR
    assert result.error and result.error.get("resilience") == "retry_budget_exhausted"
    assert agent.calls == 2  # initial + one budgeted retry
    assert ctx.metrics.snapshot.retry_attempts == 1
    assert ctx.metrics.snapshot.retry_budget_exhausted == 1
    assert ctx.retry_budget is not None and ctx.retry_budget.remaining == 0


def test_retry_budget_validation():
    try:
        RetryBudget(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative retry budget must fail")


def test_circuit_breaker_open_half_open_success_cycle():
    now = [10.0]
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_sec=5.0, clock=lambda: now[0])
    assert breaker.allow_request() is True
    assert breaker.record_failure() is CircuitState.CLOSED
    assert breaker.allow_request() is True
    assert breaker.record_failure() is CircuitState.OPEN
    assert breaker.allow_request() is False
    now[0] = 15.0
    assert breaker.allow_request() is True
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow_request() is False  # only one probe in flight
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_agent_circuit_stops_retry_storm_and_rejects_next_delivery():
    cfg = load_config(load_env=False)
    ctx = build_context(cfg, "structured", task_id="task-test")
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_sec=60.0)
    agent = _AlwaysFailAgent(circuit_breaker=breaker, retry=RetryPolicy(max_retries=5))
    first = asyncio.run(agent.run(_msg(), ctx))
    assert first.error and first.error.get("resilience") == "circuit_open"
    assert agent.calls == 1
    second = asyncio.run(agent.run(_msg(), ctx))
    assert second.error and second.error.get("type") == "circuit_open"
    assert agent.calls == 1
    assert ctx.metrics.snapshot.circuit_rejections == 1


def test_retry_policy_and_agent_timeout_reject_non_finite_values():
    import math
    import pytest
    from agent_runtime.runtime.agent import BaseAgent, RetryPolicy

    with pytest.raises(ValueError, match="backoff_sec"):
        RetryPolicy(backoff_sec=math.nan)
    with pytest.raises(ValueError, match="max_retries"):
        RetryPolicy(max_retries=-1)

    class DummyAgent(BaseAgent):
        async def handle(self, message, ctx):
            raise AssertionError("not reached")

    with pytest.raises(ValueError, match="timeout_sec"):
        DummyAgent("dummy", ["x"], timeout_sec=math.inf)
    with pytest.raises(ValueError, match="timeout_sec"):
        DummyAgent("dummy", ["x"], timeout_sec=0)


def test_circuit_breaker_rejects_non_finite_reset_timeout():
    import math
    import pytest
    from agent_runtime.runtime.resilience import CircuitBreaker

    with pytest.raises(ValueError, match="finite"):
        CircuitBreaker(reset_timeout_sec=math.nan)
    with pytest.raises(ValueError, match="finite"):
        CircuitBreaker(reset_timeout_sec=math.inf)
