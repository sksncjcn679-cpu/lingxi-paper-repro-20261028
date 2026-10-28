from __future__ import annotations

import asyncio

import pytest

from agent_runtime.config import load_config
from agent_runtime.runtime.agent import AgentState
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task


def test_infrastructure_crash_runs_lifecycle_and_state_cleanup(monkeypatch, tmp_path):
    config = load_config(
        load_env=False,
        data_dir=str(tmp_path / "data"),
        idempotency_persistent=True,
    )
    task = make_demo_task()
    ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
    agents = build_default_agents(config)
    orch = Orchestrator(agents)

    closed = {"state": 0}
    original_close = ctx.state_exchange.close

    def tracked_close():
        closed["state"] += 1
        return original_close()

    monkeypatch.setattr(ctx.state_exchange, "close", tracked_close)

    def crash_send(_message):
        raise RuntimeError("simulated channel infrastructure failure")

    monkeypatch.setattr(ctx.channel, "send", crash_send)
    with pytest.raises(RuntimeError, match="simulated channel"):
        asyncio.run(orch.run_task(task, ctx))

    assert closed["state"] >= 1
    assert all(agent.state is AgentState.TERMINATED for agent in agents.values())
    assert not hasattr(ctx, "_active_idempotency_store")
    # stop() is idempotent, but the crash guard must leave no active timer.
    assert ctx.metrics._start is None
