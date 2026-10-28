"""Communication claims must survive a concise full-payload text baseline."""

from __future__ import annotations

from agent_runtime.evaluation.runner import MAIN_SUITE, run_suite
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.protocol.serialization import encode_structured, render_text, render_text_lean


def _message() -> AgentMessage:
    return AgentMessage(
        trace_id="trace_fair",
        task_id="task_fair",
        step_id="s0",
        context_id="ctx_fair",
        parent_message_id="parent",
        causation_id="cause",
        sequence_no=3,
        source_agent="planner",
        target_agent="retriever",
        action_type=ActionType.PLAN,
        capability="task_planning",
        input_parameters={"description": "x" * 80, "operands": list(range(12))},
        result={"plan": {"operation": "sum", "operands": list(range(12))}},
        status=MessageStatus.OK,
        confidence=0.9,
    )


def test_lean_text_removes_prose_overhead_without_dropping_payload():
    msg = _message()
    verbose = render_text(msg)
    lean = render_text_lean(msg)
    assert len(lean.encode("utf-8")) < len(verbose.encode("utf-8"))
    assert "input=" in lean and "result=" in lean
    assert "description" in lean and "operands" in lean


def test_main_suite_uses_conservative_lean_text_baseline():
    assert MAIN_SUITE[0].id == "A"
    assert MAIN_SUITE[0].mode == "text"
    assert MAIN_SUITE[0].text_style == "lean"


def test_state_data_plane_still_beats_lean_text_on_demo():
    results = run_suite("main", repeat=1, seed=123)["results"]
    a = results["A"]["summary"]["wire_bytes"]["mean"]
    c = results["C"]["summary"]["wire_bytes"]["mean"]
    assert c < a
    # Deliberately do not assert B < A: protocol-only savings are renderer-sensitive.
    assert encode_structured(_message())
