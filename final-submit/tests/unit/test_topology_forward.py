from __future__ import annotations

import asyncio

from agent_runtime.config import load_config
from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.protocol.message import MessageStatus
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator


def _run(topology: str):
    cfg = load_config(
        load_env=False,
        pipeline_topology=topology,
        structured_wire_codec="session_msgpack",
        state_shared_memory=False,
    )
    rows = []
    for task in _self_contained_tasks():
        ctx = build_context(
            cfg,
            "structured",
            task_id=task.task_id,
            use_memory=False,
            use_state_exchange=True,
            use_sandbox=True,
        )
        result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
        rows.append(result)
    return rows


def test_forward_has_four_requests_plus_final_result_and_preserves_causality():
    rows = _run("forward")
    for result in rows:
        assert result.success
        assert result.metrics.message_count == 5
        history_ids = {message.message_id for message in result.messages}
        requests = [
            message
            for message in result.messages
            if message.target_agent in {"planner", "retriever", "executor", "reviewer"}
        ]
        assert len(requests) == 4
        assert requests[0].parent_message_id is None
        assert all(message.parent_message_id in history_ids for message in requests[1:])
        assert result.messages[-1].target_agent == "orchestrator"


def test_request_reply_remains_nine_messages_per_task():
    rows = _run("request_reply")
    assert all(result.success for result in rows)
    assert all(result.metrics.message_count == 9 for result in rows)


def test_forward_delivers_an_intermediate_failure_to_orchestrator():
    cfg = load_config(load_env=False, pipeline_topology="forward", control_transport="inproc")
    task = _self_contained_tasks()[0]
    ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
    agents = build_default_agents(cfg)

    async def fail(_message, _ctx):
        raise RuntimeError("injected retrieval failure")

    agents["retriever"].handle = fail
    result = asyncio.run(Orchestrator(agents).run_task(task, ctx))
    assert not result.success
    assert result.error is not None
    assert result.messages[-1].status == MessageStatus.ERROR
    assert result.messages[-1].target_agent == "orchestrator"
    assert result.messages[-1].parent_message_id == result.messages[-2].message_id
