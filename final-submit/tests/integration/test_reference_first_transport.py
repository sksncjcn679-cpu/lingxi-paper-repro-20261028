"""Adaptive reference-first control/data-plane separation regression tests."""

from __future__ import annotations

import asyncio
import json

from agent_runtime.config import load_config
from agent_runtime.protocol.message import ActionType
from agent_runtime.protocol.serialization import decode_structured, encode_structured, render_text
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.runtime.task import Task
from agent_runtime.scenarios.demo import make_demo_task


def _run(task: Task | None = None, *, state: bool = True, reference_first: bool = True):
    cfg = load_config(
        load_env=False,
        reference_first_data_plane=reference_first,
        pipeline_topology="request_reply",
    )
    task = task or make_demo_task()
    ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=state)
    result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
    return result, ctx


def _large_task(n: int = 128) -> Task:
    facts = {f"k{i}": i for i in range(n)}
    operands = list(facts)
    return Task(
        topic="large-sum",
        description="Sum a large list of referenced facts.",
        payload={"facts": facts, "question": "sum", "operands": operands},
        expected={"answer": sum(facts.values())},
    )


def test_all_agent_replies_are_accounted_as_messages():
    result, _ = _run()
    assert result.success is True
    assert result.metrics.message_count == 9  # 4 requests + 4 replies + final


def test_small_producer_result_stays_inline_when_reference_overhead_is_larger():
    _, ctx = _run()
    decision = ctx.blackboard["plan_reply_placement"]
    assert decision["reference"] is False
    assert decision["reference_bytes"] >= decision["inline_bytes"]
    reply = next(
        m for m in ctx.channel.history
        if m.source_agent == "planner" and m.action_type is ActionType.PLAN and m.result
    )
    assert reply.state_reference is None
    assert "plan" in json.loads(encode_structured(reply))["rs"]
    # The downstream request still consumes the materialized state plane.
    retriever_request = next(
        m for m in ctx.channel.history
        if m.target_agent == "retriever" and m.action_type is ActionType.RETRIEVE
    )
    assert retriever_request.state_reference


def test_large_plan_and_evidence_switch_to_reference_first_wire():
    _, ctx = _run(_large_task())
    assert ctx.blackboard["plan_reply_placement"]["reference"] is True
    assert ctx.blackboard["evidence_reply_placement"]["reference"] is True

    planner_reply = next(
        m for m in ctx.channel.history
        if m.source_agent == "planner" and m.action_type is ActionType.PLAN and m.result
    )
    assert planner_reply.state_reference
    planner_wire = encode_structured(planner_reply)
    compact = json.loads(planner_wire)
    assert compact["sr"].startswith("~s")
    assert len(compact["sr"]) < len(planner_reply.state_reference)
    assert decode_structured(planner_wire).state_reference == planner_reply.state_reference
    assert "plan" not in compact["rs"]

    retriever_reply = next(
        m for m in ctx.channel.history
        if m.source_agent == "retriever" and m.action_type is ActionType.RETRIEVE and m.result
    )
    assert retriever_reply.artifact_references
    retriever_wire = encode_structured(retriever_reply)
    compact = json.loads(retriever_wire)
    assert all(ref.startswith("~a") for ref in compact["ar"])
    assert all(
        len(wire_ref) < len(local_ref)
        for wire_ref, local_ref in zip(compact["ar"], retriever_reply.artifact_references)
    )
    assert decode_structured(retriever_wire).artifact_references == retriever_reply.artifact_references
    assert "evidence" not in compact["rs"]
    # Local/durable object stays complete despite wire projection.
    assert len(retriever_reply.result["evidence"]) == 128


def test_text_mode_renderer_never_hides_local_result():
    _, ctx = _run(_large_task())
    reply = next(
        m for m in ctx.channel.history
        if m.source_agent == "retriever" and m.action_type is ActionType.RETRIEVE and m.result
    )
    text = render_text(reply)
    assert "k0" in text and "k127" in text


def test_adaptive_reference_first_never_increases_small_demo_wire():
    legacy, _ = _run(reference_first=False)
    adaptive, _ = _run(reference_first=True)
    assert adaptive.success == legacy.success is True
    assert adaptive.metrics.wire_bytes <= legacy.metrics.wire_bytes
    # Session-JSON frames carry wall-clock timestamp deltas (microseconds), whose digit count
    # differs between two runs; allow that ±1% jitter while still forbidding real regressions.
    assert adaptive.metrics.text_tokens <= legacy.metrics.text_tokens * 1.01
