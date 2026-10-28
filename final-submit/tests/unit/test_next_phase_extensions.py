from agent_runtime.evaluation.memory_consolidation import run_memory_consolidation
from agent_runtime.evaluation.memory_hard_set import run_memory_hard_set
from agent_runtime.protocol.message import ActionType, AgentMessage
from agent_runtime.protocol.session_codec import SessionWireCodec


def _message(value: str) -> AgentMessage:
    return AgentMessage(
        trace_id="trace-extension",
        task_id="task-extension",
        step_id="s0",
        source_agent="planner",
        target_agent="retriever",
        action_type=ActionType.PLAN,
        capability="task_planning",
        input_parameters={"long_value": value},
    )


def test_layered_memory_consolidates_three_records_and_marks_conflict():
    result = run_memory_consolidation()
    assert result["consolidation"]["created"] is True
    assert result["consolidation"]["source_count"] == 3
    assert result["conflict_resolution"]["conflict_marked"] is True
    assert result["conflict_resolution"]["top_answer"] == "safe"


def test_session_value_dictionary_is_lossless_and_shortens_repeated_wire():
    value = "journal evidence " * 10
    enc = SessionWireCodec(value_dictionary=True)
    dec = SessionWireCodec(value_dictionary=True)
    first = enc.encode_msgpack(_message(value))
    second = enc.encode_msgpack(_message(value))
    assert len(second) < len(first)
    assert dec.decode_msgpack(first).input_parameters["long_value"] == value
    assert dec.decode_msgpack(second).input_parameters["long_value"] == value


def test_hard_set_records_mock_and_lexical_provider_results():
    result = run_memory_hard_set()
    assert "mock" in result["summary"]
    assert "lexical" in result["summary"]
    assert 0.0 <= result["summary"]["lexical"]["mrr"] <= 1.0
