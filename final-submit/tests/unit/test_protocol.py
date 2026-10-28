"""协议层单元测试：schema 校验、编解码往返、映射、幂等。"""

from __future__ import annotations

import pytest

from agent_runtime.protocol.idempotency import IdempotencyCache, fingerprint
from agent_runtime.protocol.mapper import from_wire, semantically_equivalent, to_wire
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.protocol.serialization import measure
from agent_runtime.providers.tokenizer import HeuristicTokenizer
from agent_runtime.protocol.schema import (
    is_compatible,
    message_json_schema,
    validate_message,
)
from agent_runtime.runtime.errors import ProtocolError


def _msg(**kw) -> AgentMessage:
    base: dict = {
        "trace_id": "tr1",
        "task_id": "tk1",
        "step_id": "s0",
        "source_agent": "planner",
        "target_agent": "retriever",
        "action_type": ActionType.PLAN,
        "capability": "task_planning",
        "status": MessageStatus.OK,
    }
    base.update(kw)
    return AgentMessage(**base)


def test_json_schema_contains_required_fields():
    schema = message_json_schema()
    props = schema["properties"]
    for field in [
        "protocol_version",
        "message_id",
        "trace_id",
        "task_id",
        "step_id",
        "source_agent",
        "target_agent",
        "action_type",
        "capability",
        "input_parameters",
        "result",
        "status",
        "dependencies",
        "artifact_references",
        "evidence_references",
        "state_reference",
        "confidence",
        "error",
        "metrics",
    ]:
        assert field in props


def test_validate_message_ok_and_version():
    msg = _msg()
    validated = validate_message(msg.model_dump())
    assert validated.action_type == ActionType.PLAN
    assert is_compatible(validated.protocol_version)


def test_validate_message_rejects_incompatible_version():
    data = _msg().model_dump()
    data["protocol_version"] = "99.0"
    with pytest.raises(ProtocolError):
        validate_message(data)


def test_validate_message_rejects_bad_schema():
    with pytest.raises(ProtocolError):
        validate_message({"action_type": "plan"})  # 缺少必填字段


def test_structured_roundtrip_core_fields():
    msg = _msg(
        artifact_references=["artifact://task/tk1/a1"],
        state_reference="state://s1",
        confidence=0.9,
        dependencies=["s0"],
    )
    wire = to_wire(msg, "structured")
    back = from_wire(wire, "structured")
    assert back.source_agent == msg.source_agent
    assert back.target_agent == msg.target_agent
    assert back.action_type == msg.action_type
    assert back.task_id == msg.task_id
    assert back.artifact_references == msg.artifact_references
    assert back.state_reference == msg.state_reference
    assert back.dependencies == msg.dependencies
    assert back.timestamp == msg.timestamp


def test_text_decode_is_lossy_but_keeps_core():
    msg = _msg()
    wire = to_wire(msg, "text")
    back = from_wire(wire, "text")
    # 文本模式可恢复核心字段，但语义等价性仍成立
    assert semantically_equivalent(msg, back)


def test_idempotency_cache():
    cache = IdempotencyCache()
    req = _msg(input_parameters={"x": 1})
    assert not cache.seen(req)
    result = _msg(action_type=ActionType.RESULT)
    cache.remember(req, result)
    assert cache.seen(req)
    assert cache.get(req) is result
    # 相同语义请求指纹一致
    req2 = _msg(input_parameters={"x": 1})
    assert fingerprint(req) == fingerprint(req2)


def test_measure_reports_exact_utf8_wire_bytes():
    msg = _msg(input_parameters={"note": "中文状态✓"})
    wire, tokens, chars, wire_bytes = measure(msg, "structured", HeuristicTokenizer())
    assert tokens > 0
    assert chars == len(wire)
    assert wire_bytes == len(wire.encode("utf-8"))
    assert wire_bytes > chars  # 非 ASCII 文本证明这里不是把字符数冒充字节数


def test_structured_roundtrip_preserves_lineage_and_deadline():
    msg = _msg(
        context_id="ctx-1",
        parent_message_id="msg-parent",
        causation_id="msg-cause",
        sequence_no=7,
        deadline_at=12345.678901,
    )
    wire = to_wire(msg, "structured")
    back = from_wire(wire, "structured")
    assert back.context_id == "ctx-1"
    assert back.parent_message_id == "msg-parent"
    assert back.causation_id == "msg-cause"
    assert back.sequence_no == 7
    assert back.deadline_at == pytest.approx(12345.678901, abs=1e-6)


def test_msgpack_structured_roundtrip_and_size():
    pytest.importorskip("msgpack")
    from agent_runtime.protocol.serialization import (
        decode_structured_msgpack,
        encode_structured,
        encode_structured_msgpack,
    )

    msg = _msg(
        context_id="ctx",
        artifact_references=["artifact://task/tk1/0123456789abcdef"],
        state_reference="state://tk1/0123456789abcdef",
        input_parameters={"description": "x" * 256, "operands": list(range(20))},
    )
    binary = encode_structured_msgpack(msg)
    back = decode_structured_msgpack(binary)
    json_back = from_wire(encode_structured(msg), "structured")
    assert back.model_dump() == json_back.model_dump()
    assert back.timestamp == msg.timestamp
    assert back.context_id == msg.context_id
    assert back.artifact_references == msg.artifact_references
    assert back.state_reference == msg.state_reference
    assert len(binary) < len(encode_structured(msg).encode("utf-8"))


def test_text_roundtrip_preserves_lineage_and_deadline_for_fair_comparison():
    msg = _msg(
        context_id="ctx-text",
        parent_message_id="msg-parent-text",
        causation_id="msg-cause-text",
        sequence_no=11,
        deadline_at=22345.123456,
    )
    wire = to_wire(msg, "text")
    back = from_wire(wire, "text")
    assert semantically_equivalent(msg, back)
    assert back.context_id == msg.context_id
    assert back.parent_message_id == msg.parent_message_id
    assert back.causation_id == msg.causation_id
    assert back.sequence_no == msg.sequence_no
    assert back.deadline_at == pytest.approx(msg.deadline_at, abs=1e-6)


def test_persistent_idempotency_cache_survives_reopen_and_rebinds_lineage(tmp_path):
    from agent_runtime.protocol.idempotency import (
        PersistentIdempotencyCache,
        rebind_cached_result,
    )

    req = _msg(idempotency_key="external-effect-42", context_id="ctx-old", sequence_no=1)
    result = _msg(
        message_id="msg-result-stable",
        action_type=ActionType.RESULT,
        source_agent="retriever",
        target_agent="planner",
        status=MessageStatus.OK,
        result={"answer": 7},
    )
    path = tmp_path / "idem.sqlite3"
    with PersistentIdempotencyCache(path, ttl_sec=60) as cache:
        cache.remember(req, result)
    with PersistentIdempotencyCache(path, ttl_sec=60) as cache:
        assert cache.seen(req)
        cached = cache.get(req)
        assert cached is not None
        new_req = req.model_copy(
            update={
                "message_id": "msg-redelivery",
                "trace_id": "trace-new",
                "context_id": "ctx-new",
                "sequence_no": 9,
            }
        )
        rebound = rebind_cached_result(cached, new_req, trace_id="trace-new")
        assert rebound.message_id == "msg-result-stable"
        assert rebound.trace_id == "trace-new"
        assert rebound.context_id == "ctx-new"
        assert rebound.parent_message_id == "msg-redelivery"
        assert rebound.causation_id == "msg-redelivery"
        assert rebound.sequence_no == 10
        assert rebound.result == {"answer": 7}


def test_protocol_1_1_accepts_1_0_messages_with_optional_fields_absent():
    data = _msg().model_dump()
    data["protocol_version"] = "1.0"
    validated = validate_message(data)
    assert validated.protocol_version == "1.0"
    assert validated.context_id is None
    assert validated.parent_message_id is None
    assert is_compatible("1.0") is True


def test_idempotency_key_is_bound_to_logical_request_payload_and_target():
    base = _msg(idempotency_key="same-key", input_parameters={"x": 1})
    redelivery = base.model_copy(
        update={"message_id": "msg-redelivery", "trace_id": "trace-redelivery"}
    )
    changed_payload = base.model_copy(update={"input_parameters": {"x": 2}})
    changed_target = base.model_copy(update={"target_agent": "different-agent"})
    changed_state = base.model_copy(update={"state_reference": "state://different"})

    assert fingerprint(base) == fingerprint(redelivery)
    assert fingerprint(base) != fingerprint(changed_payload)
    assert fingerprint(base) != fingerprint(changed_target)
    assert fingerprint(base) != fingerprint(changed_state)


def test_structured_roundtrip_does_not_truncate_unreferenced_large_payloads():
    msg = _msg(
        input_parameters={"blob": "X" * 200, "nums": list(range(30))},
        result={"text": "Y" * 180, "items": list(range(20))},
    )
    wire = to_wire(msg, "structured")
    back = from_wire(wire, "structured")
    assert back.input_parameters == msg.input_parameters
    assert back.result == msg.result
    assert '"ref":true' not in wire



def test_wire_decoders_reject_incompatible_protocol_major_version():
    import json

    from agent_runtime.protocol.serialization import decode_structured, decode_structured_msgpack

    msg = _msg(protocol_version="99.0")
    # Build a normal compatible wire first, then alter only the compact protocol field;
    # constructing AgentMessage itself intentionally does not enforce peer compatibility.
    wire = to_wire(_msg(), "structured")
    raw = json.loads(wire)
    raw["v"] = "99.0"
    with pytest.raises(ProtocolError, match="协议版本不兼容"):
        decode_structured(json.dumps(raw, separators=(",", ":")))

    msgpack = pytest.importorskip("msgpack")
    binary = msgpack.packb(raw, use_bin_type=True)
    with pytest.raises(ProtocolError, match="协议版本不兼容"):
        decode_structured_msgpack(binary)

@pytest.mark.parametrize(
    "field,value",
    [
        ("sequence_no", -1),
        ("deadline_at", float("inf")),
        ("deadline_at", float("nan")),
        ("timestamp", float("inf")),
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("confidence", float("nan")),
    ],
)
def test_protocol_rejects_non_interoperable_numeric_values(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _msg(**{field: value})


def test_protocol_boundary_rejects_nested_non_finite_json_numbers():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="input_parameters"):
        _msg(input_parameters={"score": float("nan")})


def test_persistent_idempotency_seen_ignores_and_removes_corrupt_rows(tmp_path):
    from agent_runtime.protocol.idempotency import PersistentIdempotencyCache, fingerprint

    req = _msg(idempotency_key="corrupt-entry", input_parameters={"x": 1})
    path = tmp_path / "idem.sqlite3"
    with PersistentIdempotencyCache(path, ttl_sec=60) as cache:
        now = __import__("time").time()
        cache.conn.execute(
            "INSERT INTO completed_requests(fingerprint,task_id,created_at,expires_at,result_json) VALUES(?,?,?,?,?)",
            (fingerprint(req), req.task_id, now, now + 60, "{broken-json"),
        )
        cache.conn.commit()
        assert cache.seen(req) is False
        assert cache.get(req) is None
        assert len(cache) == 0


@pytest.mark.parametrize(
    "field",
    ["message_id", "trace_id", "task_id", "source_agent", "target_agent"],
)
def test_message_rejects_empty_routing_identity_fields(field):
    from pydantic import ValidationError

    payload = dict(
        trace_id="tr",
        task_id="task",
        source_agent="a",
        target_agent="b",
        action_type=ActionType.EXECUTE,
    )
    payload[field] = ""
    with pytest.raises(ValidationError):
        AgentMessage(**payload)


def test_message_rejects_non_json_or_non_finite_nested_payloads():
    import math
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="input_parameters"):
        _msg(input_parameters={"n": math.nan})
    with pytest.raises(ValidationError, match="result"):
        _msg(result={"obj": object()})


def test_compact_wire_atoms_are_reversible_for_generated_ids_refs_and_time():
    import json

    from agent_runtime.protocol.serialization import decode_structured, encode_structured

    task_id = "task_0123456789"
    msg = _msg(
        message_id="msg_012345abcdef",
        trace_id="trace_fedcba987654",
        task_id=task_id,
        context_id=task_id,
        parent_message_id="msg_111111111111",
        causation_id="msg_222222222222",
        timestamp=1_797_000_000.123456,
        deadline_at=1_797_000_010.654321,
        state_reference=f"state://{task_id}/0123456789abcdef0123456789abcdef",
        artifact_references=[
            f"artifact://task/{task_id}/fedcba9876543210fedcba9876543210"
        ],
        input_parameters={
            "ref": f"vstate://{task_id}/11111111111111111111111111111111"
        },
    )
    wire = encode_structured(msg)
    raw = json.loads(wire)
    assert raw["id"].startswith("~") and len(raw["id"]) < len(msg.message_id)
    assert raw["tr"].startswith("~") and len(raw["tr"]) < len(msg.trace_id)
    assert raw["tk"].startswith("~") and len(raw["tk"]) < len(msg.task_id)
    assert type(raw["ts"]) is int
    assert type(raw["dl"]) is int
    assert raw["sr"].startswith("~s")
    assert raw["ar"][0].startswith("~a")
    assert raw["ip"]["ref"].startswith("~v")

    back = decode_structured(wire)
    assert back.message_id == msg.message_id
    assert back.trace_id == msg.trace_id
    assert back.task_id == msg.task_id
    assert back.context_id == msg.context_id
    assert back.parent_message_id == msg.parent_message_id
    assert back.causation_id == msg.causation_id
    assert back.timestamp == msg.timestamp
    assert back.deadline_at == msg.deadline_at
    assert back.state_reference == msg.state_reference
    assert back.artifact_references == msg.artifact_references
    assert back.input_parameters == msg.input_parameters


def test_compact_id_atom_namespace_escapes_custom_tilde_identifiers():
    from agent_runtime.protocol.serialization import decode_structured, encode_structured

    msg = _msg(
        message_id="~custom-message",
        trace_id="~custom-trace",
        task_id="~custom-task",
        context_id="~custom-context",
        parent_message_id="~custom-parent",
        causation_id="~custom-cause",
    )
    back = decode_structured(encode_structured(msg))
    assert back.message_id == msg.message_id
    assert back.trace_id == msg.trace_id
    assert back.task_id == msg.task_id
    assert back.context_id == msg.context_id
    assert back.parent_message_id == msg.parent_message_id
    assert back.causation_id == msg.causation_id


def test_protocol_1_1_legacy_compact_wire_remains_decodable_after_1_2_atom_upgrade():
    import json

    from agent_runtime.protocol.serialization import decode_structured

    raw = {
        "v": "1.1",
        "id": "msg_012345abcdef",
        "tr": "trace_fedcba987654",
        "tk": "task_0123456789",
        "st": "s3",
        "cx": "task_0123456789",
        "pm": "msg_111111111111",
        "ca": "msg_222222222222",
        "sq": 4,
        "dl": 1797000010.654321,
        "s": "planner",
        "t": "retriever",
        "ts": 1797000000.123456,
        "a": "plan",
        "c": "task_planning",
        "sta": "ok",
        "sr": "state://task_0123456789/0123456789abcdef0123456789abcdef",
        "ip": {"x": 1},
        "rs": {"y": 2},
    }
    msg = decode_structured(json.dumps(raw, separators=(",", ":")))
    assert msg.protocol_version == "1.1"
    assert msg.message_id == raw["id"]
    assert msg.trace_id == raw["tr"]
    assert msg.task_id == raw["tk"]
    assert msg.step_id == "s3"
    assert msg.timestamp == raw["ts"]
    assert msg.deadline_at == raw["dl"]
    assert msg.state_reference == raw["sr"]
    assert msg.input_parameters == {"x": 1}
    assert msg.result == {"y": 2}


def test_protocol_1_2_semantic_atom_codes_are_stable_wire_constants():
    from agent_runtime.protocol.serialization import (
        ACTION_TO_ATOM,
        CAPABILITY_TO_ATOM,
        STATUS_TO_ATOM,
    )

    assert ACTION_TO_ATOM["plan"] == 3
    assert ACTION_TO_ATOM["result"] == 9
    assert STATUS_TO_ATOM["pending"] == 0
    assert STATUS_TO_ATOM["ok"] == 1
    assert CAPABILITY_TO_ATOM["task_planning"] == 0
    assert CAPABILITY_TO_ATOM["codeact"] == 6
    assert CAPABILITY_TO_ATOM["summarize"] == 9
    assert CAPABILITY_TO_ATOM["memorize"] == 10
