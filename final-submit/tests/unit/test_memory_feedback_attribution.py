"""Memory reliability feedback must be based on actual adoption and causal evidence."""

from __future__ import annotations

import asyncio

from agent_runtime.agents.codeact_executor import CodeActExecutorAgent
from agent_runtime.agents.reviewer import ReviewerSummarizerAgent
from agent_runtime.config import load_config
from agent_runtime.memory.models import MemoryRecord, MemoryType
from agent_runtime.memory.store import MemoryStore
from agent_runtime.protocol.message import ActionType, AgentMessage
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context
from agent_runtime.runtime.task import Task, VerdictResult


def _record(memory: MemoryStore, *, answer=11, solution="guard_zero_division") -> MemoryRecord:
    rec = MemoryRecord(
        source_agent="reviewer",
        task_topic="test",
        summary="reusable test memory",
        memory_type=MemoryType.PROCEDURAL,
        keywords=["codefix", "zero_division"],
        payload={
            "operation": "codefix",
            "answer": answer,
            "solution": solution,
            "signature": "other-signature",
            "defect": "zero_division",
        },
    )
    memory.write(rec)
    return rec


def _message(task_id: str, value) -> AgentMessage:
    return AgentMessage(
        trace_id="trace_test",
        task_id=task_id,
        step_id="s3",
        source_agent="executor",
        target_agent="reviewer",
        action_type=ActionType.REVIEW,
        input_parameters={"value": value},
    )


def test_selected_memory_is_not_used_when_task_evidence_wins():
    cfg = load_config(load_env=False)
    memory = MemoryStore(build_embedding(cfg))
    rec = _record(memory)
    ctx = build_context(cfg, "structured", task_id="t", use_memory=True, use_sandbox=True, memory=memory)
    ctx.blackboard["memory_candidate_id"] = rec.memory_id
    ctx.blackboard["memory_candidate_mode"] = "solution"
    ctx.blackboard["reused_solution"] = "guard_zero_division"

    executor = CodeActExecutorAgent()
    evidence = {
        "module": "def safe_divide(a, b):\n    return a / b\n",
        "test": "assert safe_divide(6, 3) == 2\nassert safe_divide(1, 0) is None\n",
        # Explicit task evidence takes precedence; retrieved memory is not adopted.
        "strategy": "guard_zero_division",
    }
    ok = asyncio.run(executor._handle_codefix(evidence, ctx))
    assert ok is True
    stored = memory.get(rec.memory_id)
    assert stored is not None
    assert stored.reuse_count == 0
    assert ctx.metrics.snapshot.memory_used == 0
    assert ctx.blackboard.get("reused_memory_ids") in (None, [])


def test_external_verifier_failure_does_not_poison_adopted_memory():
    cfg = load_config(load_env=False)
    memory = MemoryStore(build_embedding(cfg))
    rec = _record(memory, answer=10)
    memory.mark_used(rec.memory_id)
    before = memory.get(rec.memory_id)
    assert before is not None

    def external_failure(outputs):
        assert outputs["answer"] == 10
        return VerdictResult(False, 0.0, {"cause": "external_timeout"})

    task = Task(
        topic="external-verifier",
        description="verifier fails for an unrelated external reason",
        expected={"answer": 10},
        verifier=external_failure,
        task_id="t_external",
    )
    ctx = build_context(cfg, "structured", task_id=task.task_id, use_memory=True, memory=memory)
    ctx.blackboard.update(
        {
            "task": task,
            "reused_memory_ids": [rec.memory_id],
            "memory_reuse_contribution": {"memory_id": rec.memory_id, "mode": "answer", "value": 10},
            "execution_provenance": {"source": "memory_answer", "status": "completed"},
            "plan": {"operation": "noop", "operands": []},
        }
    )

    asyncio.run(ReviewerSummarizerAgent().handle(_message(task.task_id, 10), ctx))
    after = memory.get(rec.memory_id)
    assert after is not None
    assert after.harmful_reuse_count == before.harmful_reuse_count == 0
    assert after.effective_reuse_count == before.effective_reuse_count == 0
    assert after.quality_score == before.quality_score
    assert ctx.metrics.snapshot.memory_unattributed == 1
    assert ctx.blackboard["memory_feedback_attribution"] == "unattributed"


def test_directly_wrong_reused_answer_is_attributed_harmful():
    cfg = load_config(load_env=False)
    memory = MemoryStore(build_embedding(cfg))
    rec = _record(memory, answer=11)
    memory.mark_used(rec.memory_id)
    task = Task(
        topic="wrong-answer",
        description="wrong reused answer",
        expected={"answer": 10},
        task_id="t_wrong",
    )
    ctx = build_context(cfg, "structured", task_id=task.task_id, use_memory=True, memory=memory)
    ctx.blackboard.update(
        {
            "task": task,
            "reused_memory_ids": [rec.memory_id],
            "memory_reuse_contribution": {"memory_id": rec.memory_id, "mode": "answer", "value": 11},
            "execution_provenance": {"source": "memory_answer", "status": "completed"},
            "plan": {"operation": "noop", "operands": []},
        }
    )

    asyncio.run(ReviewerSummarizerAgent().handle(_message(task.task_id, 11), ctx))
    after = memory.get(rec.memory_id)
    assert after is not None
    assert after.harmful_reuse_count == 1
    assert ctx.metrics.snapshot.memory_harmful == 1
    assert ctx.metrics.snapshot.memory_unattributed == 0
    assert ctx.blackboard["memory_feedback_attribution"] == "harmful"
