"""Versioned delta state must participate in the normal four-agent pipeline."""

from __future__ import annotations

import asyncio

from agent_runtime.config import load_config
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task


def test_normal_pipeline_advances_and_consumes_versioned_state():
    config = load_config(load_env=False)
    task = make_demo_task()
    ctx = build_context(
        config,
        "structured",
        task_id=task.task_id,
        use_state_exchange=True,
    )
    result = asyncio.run(Orchestrator(build_default_agents(config)).run_task(task, ctx))

    assert result.success is True
    assert result.metrics.versioned_state_transfers == 4
    assert result.metrics.versioned_state_snapshots >= 1
    assert result.metrics.versioned_state_delta_updates >= 2

    uri = ctx.blackboard.get("workflow_state_ref")
    assert isinstance(uri, str) and uri.startswith("vstate://")
    final_state = ctx.state_exchange.get_versioned_state(uri)
    assert final_state["stage"] == "reviewer"
    # fable14 normalisation: the collaboration state references the immutable plan state.
    assert "plan" not in final_state and final_state["plan_ref"].startswith("state://")
    assert final_state["plan_ref"] == ctx.blackboard.get("plan_state_ref")  # the plan state the run used
    assert ctx.blackboard["plan"]["operation"] == "sum"
    assert final_state["evidence"]["count"] == 3
    assert final_state["review"]["success"] is True

    # The reviewer consumed the executor revision before producing the final revision.
    consumed = ctx.blackboard.get("workflow_state_last_consumed")
    assert consumed == {
        "agent": "reviewer",
        "uri": consumed["uri"],
        "stage": "executor",
    }


def test_versioned_state_bytes_are_included_in_generic_state_accounting():
    config = load_config(load_env=False)
    task = make_demo_task()
    ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
    result = asyncio.run(Orchestrator(build_default_agents(config)).run_task(task, ctx))

    m = result.metrics
    assert m.versioned_state_bytes > 0
    assert m.versioned_state_logical_bytes >= m.versioned_state_bytes
    assert m.state_bytes >= m.versioned_state_bytes
    assert m.state_logical_bytes >= m.versioned_state_logical_bytes


def test_versioned_pipeline_ablation_saves_real_pipeline_materialization():
    from agent_runtime.evaluation.runner import run_suite

    payload = run_suite("versioned_pipeline", repeat=1, seed=31)
    v0 = payload["results"]["V0"]["summary"]
    v1 = payload["results"]["V1"]["summary"]
    assert v0["task_success"]["mean"] == v1["task_success"]["mean"] == 1.0
    assert v1["versioned_state_delta_updates"]["mean"] > 0
    assert v0["versioned_state_delta_updates"]["mean"] == 0
    assert v1["versioned_state_bytes"]["mean"] < v0["versioned_state_bytes"]["mean"]
    # This is the actual Orchestrator path on the large-plan workload, not the standalone ledger.
    saving = 1 - v1["versioned_state_bytes"]["mean"] / v0["versioned_state_bytes"]["mean"]
    assert saving > 0.50
