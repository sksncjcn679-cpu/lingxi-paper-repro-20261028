from agent_runtime.artifact_store.store import ArtifactStore
from agent_runtime.interop.a2a import (
    A2ATaskState,
    artifact_ref_to_a2a,
    from_a2a_message,
    task_view,
    to_a2a_message,
)
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus


def _message() -> AgentMessage:
    return AgentMessage(
        message_id="msg-1",
        trace_id="trace-1",
        task_id="task-1",
        step_id="execute",
        context_id="ctx-1",
        parent_message_id="msg-0",
        causation_id="msg-0",
        sequence_no=3,
        deadline_at=1234.5,
        source_agent="planner",
        target_agent="executor",
        action_type=ActionType.EXECUTE,
        capability="code_execution",
        input_parameters={"x": 1},
        result={"ok": True},
        status=MessageStatus.OK,
        dependencies=["plan"],
        artifact_references=["artifact://task/task-1/a"],
        evidence_references=["artifact://task/task-1/e"],
        state_reference="state://task-1/s",
        confidence=0.9,
        idempotency_key="idem-1",
    )


def test_a2a_message_adapter_roundtrip_keeps_runtime_semantics():
    msg = _message()
    doc = to_a2a_message(msg, role="ROLE_AGENT")
    assert doc["messageId"] == "msg-1"
    assert doc["contextId"] == "ctx-1"
    assert doc["role"] == "ROLE_AGENT"
    assert any(p.get("url") == msg.state_reference for p in doc["parts"])

    back = from_a2a_message(doc)
    for field in (
        "message_id", "trace_id", "task_id", "step_id", "context_id",
        "parent_message_id", "causation_id", "sequence_no", "source_agent",
        "target_agent", "action_type", "capability", "status",
        "artifact_references", "evidence_references", "state_reference",
        "idempotency_key",
    ):
        assert getattr(back, field) == getattr(msg, field)
    assert back.deadline_at == msg.deadline_at
    assert back.input_parameters == msg.input_parameters
    assert back.result == msg.result


def test_a2a_artifact_adapter_keeps_hash_uri_and_media_type():
    store = ArtifactStore(task_id="task-1")
    ref = store.put({"answer": 42}, summary="answer")
    doc = artifact_ref_to_a2a(ref, name="result.json")
    assert doc["artifactId"] == ref.content_hash
    assert doc["parts"][0]["url"] == ref.uri
    assert doc["parts"][0]["mediaType"] == ref.media_type
    assert doc["metadata"]["sha256"] == ref.content_hash


def test_a2a_task_view_separates_history_from_artifacts():
    history = [to_a2a_message(_message(), role="ROLE_AGENT")]
    task = task_view(
        task_id="task-1",
        context_id="ctx-1",
        state=A2ATaskState.COMPLETED,
        history=history,
        artifacts=[{"artifactId": "abc", "parts": [{"text": "done"}]}],
    )
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["history"][0]["messageId"] == "msg-1"
    assert task["artifacts"][0]["artifactId"] == "abc"


def test_a2a_adapter_rejects_arbitrary_message_without_runtime_data_part():
    try:
        from_a2a_message({"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "hi"}]})
    except ValueError as exc:
        assert "structured data Part" in str(exc)
    else:
        raise AssertionError("adapter must not pretend to parse arbitrary A2A messages")


def test_a2a_agent_card_export_requires_explicit_network_interface():
    import pytest
    from agent_runtime.interop.a2a import agent_card_to_a2a
    from agent_runtime.registry.capability import AgentCard

    card = AgentCard(
        agent_name="executor",
        capabilities=["execute"],
        actions=["execute", "result"],
        description="Executes code safely",
    )
    with pytest.raises(ValueError, match="interface"):
        agent_card_to_a2a(card, interfaces=[], agent_version="0.4.0")

    out = agent_card_to_a2a(
        card,
        interfaces=[{
            "url": "https://example.invalid/a2a",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }],
        agent_version="0.4.0",
    )
    assert out["name"] == "executor"
    assert out["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert out["skills"][0]["id"] == "execute"
    assert out["capabilities"]["streaming"] is False
