from __future__ import annotations

import asyncio
import time

from agent_runtime.agents.planner import PlannerAgent
from agent_runtime.config import load_config
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.event_log import EventJournal, journal_path_for
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task


def test_full_run_emits_verifiable_causal_event_trace(tmp_path):
    cfg = load_config(
        load_env=False,
        pipeline_topology="forward",
        event_journal_enabled=True,
        event_journal_durable=False,
        data_dir=str(tmp_path),
    )
    task = make_demo_task()
    ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
    result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
    assert result.success is True

    journal = EventJournal(journal_path_for(tmp_path / "events", task.task_id), durable=False)
    events = journal.verify()
    replay = journal.replay(task.task_id, trace_id=ctx.trace_id)
    assert replay.stages_completed == ("planner", "retriever", "executor", "reviewer")
    assert replay.terminal_event == "task.completed"

    message_events = [e for e in events if e.event_type == "message.sent"]
    assert len(message_events) == 5  # 4 inbound + final result; intermediate replies stay local
    assert sum(e.event_type == "stage.result" for e in events) == 3
    ids = {e.payload["message_id"] for e in message_events}
    for event in message_events:
        parent = event.payload.get("parent_message_id")
        if parent is not None:
            assert parent in ids


def test_expired_deadline_is_rejected_before_agent_handle(tmp_path):
    cfg = load_config(load_env=False, data_dir=str(tmp_path))
    ctx = build_context(cfg, "structured", task_id="deadline-task")
    agent = PlannerAgent()
    msg = AgentMessage(
        trace_id=ctx.trace_id,
        task_id=ctx.task_id,
        step_id="s0",
        context_id="ctx-deadline",
        sequence_no=1,
        deadline_at=time.time() - 1,
        source_agent="orchestrator",
        target_agent="planner",
        action_type=ActionType.PLAN,
        input_parameters={"description": "must not execute"},
    )
    reply = asyncio.run(agent.run(msg, ctx))
    assert reply.status == MessageStatus.TIMEOUT
    assert reply.error == {"type": "deadline_exceeded", "agent": "planner"}
    assert reply.parent_message_id == msg.message_id
    assert reply.causation_id == msg.message_id
    assert reply.sequence_no == 2
    assert ctx.metrics.snapshot.llm_calls == 0


def test_checkpoint_resume_continues_same_hash_chain_and_trace(tmp_path):
    from agent_runtime.runtime.checkpoint import FileCheckpointStore

    cfg = load_config(
        load_env=False,
        event_journal_enabled=True,
        event_journal_durable=False,
        data_dir=str(tmp_path),
    )
    task = make_demo_task()
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints")

    first_ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
    paused = asyncio.run(
        Orchestrator(build_default_agents(cfg)).run_task(
            task,
            first_ctx,
            checkpoint_store=checkpoints,
            pause_after_stage="retriever",
        )
    )
    assert paused.checkpoint is not None

    second_ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
    resumed = asyncio.run(
        Orchestrator(build_default_agents(cfg)).run_task(
            task,
            second_ctx,
            checkpoint_store=checkpoints,
            resume_from=checkpoints.load(paused.checkpoint.checkpoint_id),
        )
    )
    assert resumed.success is True
    assert second_ctx.trace_id == first_ctx.trace_id == paused.checkpoint.trace_id

    journal = EventJournal(journal_path_for(tmp_path / "events", task.task_id), durable=False)
    events = journal.verify()
    replay = journal.replay(task.task_id, trace_id=first_ctx.trace_id)
    assert replay.stages_started == ("planner", "retriever", "executor", "reviewer")
    assert replay.stages_completed == ("planner", "retriever", "executor", "reviewer")
    assert replay.terminal_event == "task.completed"
    assert sum(e.event_type == "task.started" for e in events) == 2
    assert any(e.event_type == "task.paused" for e in events)
    assert any(e.event_type == "checkpoint.saved" for e in events)
    # Full verification here proves resume appended to—not replaced or forked from—the
    # original journal hash chain.
    assert events[-1].event_type == "task.completed"
