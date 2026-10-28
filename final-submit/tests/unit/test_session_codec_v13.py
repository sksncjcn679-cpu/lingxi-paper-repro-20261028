from __future__ import annotations

import asyncio
import socket

import numpy as np
import pytest

from agent_runtime.config import load_config
from agent_runtime.memory.store import MemoryStore
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.protocol.serialization import decode_structured, encode_structured
from agent_runtime.protocol.session_codec import SessionWireCodec
from agent_runtime.providers.factory import build_embedding
from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task
from agent_runtime.state_exchange.exchange import StateExchange


def _sequence() -> list[AgentMessage]:
    trace = "trace_0123456789ab"
    task = "task_0123456789"
    first = AgentMessage(
        message_id="msg_000000000001",
        trace_id=trace,
        task_id=task,
        context_id=task,
        step_id="s0",
        source_agent="orchestrator",
        target_agent="planner",
        action_type=ActionType.PLAN,
        capability="task_planning",
        input_parameters={"question": "sum", "operands": ["a", "b"]},
    )
    second = AgentMessage(
        message_id="msg_000000000002",
        trace_id=trace,
        task_id=task,
        context_id=task,
        step_id="s0",
        parent_message_id=first.message_id,
        causation_id=first.message_id,
        source_agent="planner",
        target_agent="orchestrator",
        action_type=ActionType.PLAN,
        capability="task_planning",
        result={"plan": {"operation": "sum", "operands": ["a", "b"]}},
        state_reference="state://task_0123456789/0123456789abcdef0123456789abcdef",
        status=MessageStatus.OK,
    )
    third = AgentMessage(
        message_id="msg_000000000003",
        trace_id=trace,
        task_id=task,
        context_id=task,
        step_id="s1",
        parent_message_id=second.message_id,
        causation_id=second.message_id,
        source_agent="planner",
        target_agent="retriever",
        action_type=ActionType.RETRIEVE,
        capability="retrieval",
        input_parameters={
            "plan_ref": "state://task_0123456789/0123456789abcdef0123456789abcdef"
        },
    )
    return [first, second, third]


def test_protocol13_session_msgpack_roundtrips_sequence_and_reduces_wire():
    enc = SessionWireCodec()
    dec = SessionWireCodec()
    session_total = 0
    compact_total = 0
    for message in _sequence():
        token_wire, physical = enc.encode_dual(message)
        restored = dec.decode_msgpack(physical)
        expected = decode_structured(encode_structured(message))
        assert restored.model_dump(exclude={"timestamp"}) == expected.model_dump(exclude={"timestamp"})
        assert token_wire
        session_total += len(physical)
        compact_total += len(encode_structured(message).encode("utf-8"))
    assert session_total < compact_total * 0.7


def test_adaptive_state_selects_int8_when_quality_threshold_is_met():
    ex = StateExchange(
        MockEmbedding(dim=256, seed=7),
        task_id="adaptive",
        precision="adaptive",
        adaptive_cosine_threshold=0.999,
    )
    try:
        ref = ex.embed_text("openEuler migration compatibility")
        assert ref.precision == "int8"
        state = ex.get_vector(ref.uri)
        logical = np.asarray(MockEmbedding(dim=256, seed=7).embed(["openEuler migration compatibility"])[0])
        actual = np.asarray(state, dtype=np.float32)
        cosine = float(np.dot(logical, actual) / (np.linalg.norm(logical) * np.linalg.norm(actual)))
        assert cosine >= 0.999
        assert ref.nbytes < ref.logical_nbytes * 0.3
    finally:
        ex.close()


def test_exact_memory_fast_path_skips_three_stages_and_reduces_messages():
    cfg = load_config(load_env=False, control_transport="inproc", pipeline_topology="forward")
    memory = MemoryStore(build_embedding(cfg))
    try:
        first_task = make_demo_task()
        first_ctx = build_context(
            cfg,
            "structured",
            task_id=first_task.task_id,
            use_memory=True,
            use_state_exchange=True,
            memory=memory,
        )
        first = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(first_task, first_ctx))
        assert first.success

        second_task = make_demo_task()
        second_ctx = build_context(
            cfg,
            "structured",
            task_id=second_task.task_id,
            use_memory=True,
            use_state_exchange=True,
            memory=memory,
        )
        second = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(second_task, second_ctx))
        assert second.success
        assert second.metrics.memory_fast_path_hits == 1
        assert second.metrics.memory_fast_path_skipped_stages == 3
        assert second.metrics.message_count == 2
        assert second.metrics.tool_calls == 0
        assert second.metrics.llm_calls == 0
    finally:
        memory.close()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")
def test_channel_session_msgpack_traverses_real_uds_router_process():
    cfg = load_config(
        load_env=False,
        structured_wire_codec="session_msgpack",
        control_transport="uds",
    )
    task = make_demo_task()
    ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
    result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
    assert result.success
    assert result.metrics.ipc_frames == result.metrics.message_count
    assert result.metrics.binary_frames == result.metrics.message_count
    assert result.metrics.session_frames == result.metrics.message_count
    # fable8: cumulative-ACK barrier requests are empty 4-byte frames and are charged too.
    assert result.metrics.ipc_framed_bytes == (
        result.metrics.wire_bytes + 4 * result.metrics.message_count + 4 * result.metrics.ipc_barrier_requests
    )
    assert result.metrics.ipc_roundtrip_sec > 0
