"""消息「上线」表示、编解码与开销计量。

同一条 `AgentMessage` 在两种模式下有不同的传输表示：

- **text_mode**：渲染为冗长自然语言，并把 `input_parameters` / `result` 中的
  完整内容内联进文本（模拟「把全文塞进消息」的传统做法）。
- **structured_mode**：序列化为紧凑结构（短键 + 引用）。只有已经存在权威
  `artifact://` / `state://` 数据面引用时，producer result 才允许投影为“引用 + 摘要”；
  其他 JSON-native input/result 必须完整上线，不能靠截断制造虚假的通信节省。

两者携带等价业务语义。结构化模式对 JSON-native 字段支持无损往返；reference-first
节省来自真实数据面引用，而不是丢弃 payload。
"""

from __future__ import annotations

import base64
import functools
import json
import re
from typing import Any

from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.providers.tokenizer import Tokenizer, count_chars

# 结构化模式：字段 -> 紧凑短键。用于编码与解码（往返）。
FIELD_TO_COMPACT: dict[str, str] = {
    "protocol_version": "v",
    "message_id": "id",
    "trace_id": "tr",
    "task_id": "tk",
    "step_id": "st",
    "context_id": "cx",
    "parent_message_id": "pm",
    "causation_id": "ca",
    "sequence_no": "sq",
    "deadline_at": "dl",
    "source_agent": "s",
    "target_agent": "t",
    "timestamp": "ts",
    "action_type": "a",
    "capability": "c",
    "status": "sta",
    "artifact_references": "ar",
    "evidence_references": "er",
    "state_reference": "sr",
    "dependencies": "dep",
    "confidence": "cf",
    "input_parameters": "ip",
    "result": "rs",
    "error": "e",
    "idempotency_key": "ik",
}
COMPACT_TO_FIELD: dict[str, str] = {v: k for k, v in FIELD_TO_COMPACT.items()}

# Stable semantic atoms for the compact structured wire.  These are transport-only codes:
# AgentMessage remains the canonical public model and decoding restores the original strings.
# Unknown/custom agent or capability names stay as strings, so the codec is extensible rather
# than tied to the default four-Agent topology.
AGENT_TO_ATOM: dict[str, int] = {
    "orchestrator": 0,
    "planner": 1,
    "retriever": 2,
    "executor": 3,
    "reviewer": 4,
}
ATOM_TO_AGENT: dict[int, str] = {v: k for k, v in AGENT_TO_ATOM.items()}
# Protocol-1.2 atom codes are explicit wire constants, not enum/list ordinals.  Reordering or
# extending Python enums in a later release must never silently reinterpret an existing byte code.
ACTION_TO_ATOM: dict[str, int] = {
    "register": 0,
    "handshake": 1,
    "discover": 2,
    "plan": 3,
    "retrieve": 4,
    "execute": 5,
    "review": 6,
    "summarize": 7,
    "memorize": 8,
    "result": 9,
    "error": 10,
    "ack": 11,
}
ATOM_TO_ACTION: dict[int, str] = {v: k for k, v in ACTION_TO_ATOM.items()}
STATUS_TO_ATOM: dict[str, int] = {
    "pending": 0,
    "ok": 1,
    "error": 2,
    "timeout": 3,
    "retry": 4,
    "skipped": 5,
}
ATOM_TO_STATUS: dict[int, str] = {v: k for k, v in STATUS_TO_ATOM.items()}
CAPABILITY_TO_ATOM: dict[str, int] = {
    "task_planning": 0,
    "decompose": 1,
    "retrieval": 2,
    "memory_lookup": 3,
    "semantic_search": 4,
    "execute": 5,
    "codeact": 6,
    "compute": 7,
    "review": 8,
    "summarize": 9,
    "memorize": 10,
    # Compatibility alias for an earlier internal capability spelling.
    "python_codeact": 11,
}
ATOM_TO_CAPABILITY: dict[int, str] = {v: k for k, v in CAPABILITY_TO_ATOM.items()}

_CANONICAL_HEX = re.compile(r"^[0-9a-f]+$")
_REFERENCE_TOKEN = re.compile(r"^~([sva])([A-Za-z0-9_-]{22})$")


def _encode_atom(value: str, table: dict[str, int]) -> str | int:
    return table.get(value, value)


def _decode_atom(value: Any, table: dict[int, str], *, field: str) -> str:
    if type(value) is int:
        try:
            return table[value]
        except KeyError as exc:
            raise ValueError(f"unknown {field} atom: {value}") from exc
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or known atom")
    return value


def _encode_canonical_id(value: str | None, prefix: str, hex_width: int) -> str | None:
    """Encode runtime-generated IDs as fixed-width Base64URL atoms.

    Decimal JSON integers look compact on average, but their serialized length varies with the
    random identifier value.  That makes small A/B wire comparisons noisy.  A reserved ``~``
    token plus unpadded Base64URL is both shorter *and fixed width* for a given ID class.

    The ``~`` namespace is escaped for custom identifiers, making round-trip decoding
    unambiguous for every value emitted by this encoder.
    """

    if value is None:
        return None
    if value.startswith(prefix):
        tail = value[len(prefix):]
        if len(tail) == hex_width and _CANONICAL_HEX.fullmatch(tail):
            raw = bytes.fromhex(tail)
            token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
            return "~" + token
    return "~" + value if value.startswith("~") else value


def _decode_canonical_id(value: Any, prefix: str, hex_width: int, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or canonical id atom")
    if value.startswith("~~"):
        return value[1:]
    if not value.startswith("~"):
        return value

    token = value[1:]
    expected_bytes = hex_width // 2
    # Canonical runtime hex widths are even; the exact decoded size guards the reserved token.
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + padding)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid {field} canonical id atom") from exc
    if len(raw) != expected_bytes:
        raise ValueError(f"invalid {field} canonical id atom width")
    # Re-encode to reject non-canonical alternate spellings accepted by permissive decoders.
    canonical = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if canonical != token:
        raise ValueError(f"invalid {field} canonical id atom")
    return f"{prefix}{raw.hex()}"


def _encode_step(value: str) -> str | int:
    if value == "final":
        return -1
    match = re.fullmatch(r"s(\d+)", value)
    return int(match.group(1)) if match else value


def _decode_step(value: Any) -> str:
    if type(value) is int:
        if value == -1:
            return "final"
        if value >= 0:
            return f"s{value}"
        raise ValueError(f"unknown step atom: {value}")
    if not isinstance(value, str):
        raise ValueError("step_id must be a string or step atom")
    return value


def _encode_unix_time(value: float) -> int:
    """Encode canonical microsecond Unix time as an exact, fixed-scale integer."""
    return int(round(value * 1_000_000))


def _decode_unix_time(value: Any, *, field: str, atom_wire: bool) -> float:
    """Decode timestamps across protocol-minor representations.

    Protocol 1.2 encodes canonical microseconds as an integer.  Protocol <=1.1 used JSON
    seconds as a float.  Major-version compatibility therefore remains real rather than merely
    declared by :func:`is_compatible`.
    """
    if atom_wire:
        if type(value) is not int:
            raise ValueError(f"{field} must be an integer microsecond timestamp in protocol >=1.2")
        return value / 1_000_000.0
    if type(value) not in {int, float}:
        raise ValueError(f"{field} must be a numeric Unix timestamp")
    return float(value)


def _wire_uses_semantic_atoms(version: Any) -> bool:
    """Return whether this compatible protocol minor uses the v1.2 compact atom codec."""
    if not isinstance(version, str):
        return False
    try:
        major, minor, *_ = (int(part) for part in version.split("."))
    except (TypeError, ValueError):
        return False
    return major == 1 and minor >= 2


def _b64encode_hash(hex_digest: str) -> str:
    return base64.urlsafe_b64encode(bytes.fromhex(hex_digest)).decode("ascii").rstrip("=")


def _b64decode_hash(value: str) -> str:
    return base64.urlsafe_b64decode(value + "==").hex()


def _encode_reference_string(value: str, task_id: str) -> str:
    """Tokenize same-task content-addressed URIs without losing their identity.

    ``~`` is reserved on the wire.  User strings beginning with it are escaped as ``~~``;
    therefore reference decoding cannot accidentally reinterpret arbitrary business strings.
    """

    prefixes = (
        ("s", f"state://{task_id}/"),
        ("v", f"vstate://{task_id}/"),
        ("a", f"artifact://task/{task_id}/"),
    )
    for code, prefix in prefixes:
        if value.startswith(prefix):
            digest = value[len(prefix):]
            if len(digest) == 32 and _CANONICAL_HEX.fullmatch(digest):
                return f"~{code}{_b64encode_hash(digest)}"
    return f"~{value}" if value.startswith("~") else value


def _decode_reference_string(value: str, task_id: str) -> str:
    if value.startswith("~~"):
        return value[1:]
    match = _REFERENCE_TOKEN.fullmatch(value)
    if not match:
        return value
    code, digest_token = match.groups()
    digest = _b64decode_hash(digest_token)
    prefix = {
        "s": f"state://{task_id}/",
        "v": f"vstate://{task_id}/",
        "a": f"artifact://task/{task_id}/",
    }[code]
    return prefix + digest


# fable14: the id/reference atom codecs are pure functions of small strings that repeat on every
# frame (trace/task/context ids, the same state/artifact refs).  Memoise them -- identical
# results, including identical exceptions, since only successful string results are cached.
def _memo_str(fn, maxsize: int = 8192):
    cached = functools.lru_cache(maxsize=maxsize)(fn)

    def wrapper(value, *args, **kwargs):
        if isinstance(value, str):
            return cached(value, *args, **kwargs)
        return fn(value, *args, **kwargs)

    wrapper.__wrapped__ = fn
    wrapper.cache_info = cached.cache_info
    return wrapper


_encode_canonical_id = _memo_str(_encode_canonical_id)
_decode_canonical_id = _memo_str(_decode_canonical_id)
_encode_reference_string = _memo_str(_encode_reference_string)
_decode_reference_string = _memo_str(_decode_reference_string)


def _map_reference_strings(value: Any, task_id: str, *, decode: bool = False) -> Any:
    """Recursively encode/decode content-addressed reference values inside JSON payloads."""

    if isinstance(value, str):
        return (
            _decode_reference_string(value, task_id)
            if decode
            else _encode_reference_string(value, task_id)
        )
    if isinstance(value, list):
        return [_map_reference_strings(v, task_id, decode=decode) for v in value]
    if isinstance(value, dict):
        return {k: _map_reference_strings(v, task_id, decode=decode) for k, v in value.items()}
    return value


def _map_reference_fields(payload: dict[str, Any], task_id: str, *, decode: bool = False) -> dict[str, Any]:
    """Map only fields whose values may semantically contain data-plane references.

    Routing IDs use their own reserved ``~`` atom namespace.  Restricting reference mapping to
    reference/payload fields prevents one wire atom codec from accidentally escaping another.
    """

    names = (
        "artifact_references",
        "evidence_references",
        "state_reference",
        "input_parameters",
        "result",
        "error",
    )
    key_map = COMPACT_TO_FIELD if decode else FIELD_TO_COMPACT
    out = dict(payload)
    for name in names:
        key = name if decode else key_map[name]
        if key in out and out[key] is not None:
            out[key] = _map_reference_strings(out[key], task_id, decode=decode)
    return out


def _humanize(value: Any) -> str:
    """把任意值渲染为自然语言友好的字符串（text_mode 用，倾向于冗长完整）。"""
    if isinstance(value, dict):
        return "; ".join(f"{k} is {_humanize(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_humanize(v) for v in value)
    return str(value)


def render_text(message: AgentMessage) -> str:
    """text_mode：渲染为冗长自然语言文本（内联完整内容）。"""
    lines = [
        f"Hello, this is agent '{message.source_agent}' speaking to "
        f"agent '{message.target_agent}'.",
        f"I am performing the action '{message.action_type.value}' "
        f"with capability '{message.capability}'.",
        f"This message belongs to task '{message.task_id}', step "
        f"'{message.step_id}', and trace '{message.trace_id}'.",
        f"The message timestamp is {message.timestamp:.6f} Unix seconds.",
    ]
    if message.context_id is not None:
        lines.append(f"The conversation context identifier is '{message.context_id}'.")
    if message.parent_message_id is not None:
        lines.append(f"The parent message identifier is '{message.parent_message_id}'.")
    if message.causation_id is not None:
        lines.append(f"The causation message identifier is '{message.causation_id}'.")
    if message.sequence_no is not None:
        lines.append(f"The causal sequence number is {message.sequence_no}.")
    if message.deadline_at is not None:
        lines.append(f"The absolute execution deadline is {message.deadline_at:.6f} Unix seconds.")
    if message.input_parameters:
        lines.append(
            "Here are the full input parameters you will need: "
            + _humanize(message.input_parameters)
            + "."
        )
    if message.result is not None:
        lines.append("Here is the complete result of my work: " + _humanize(message.result) + ".")
    if message.dependencies:
        lines.append(
            "This step depends on the following previous steps: "
            + ", ".join(message.dependencies)
            + "."
        )
    lines.append(
        f"My current status is '{message.status.value}'"
        + (f" with confidence {message.confidence}." if message.confidence is not None else ".")
    )
    if message.error:
        lines.append("Unfortunately an error occurred: " + _humanize(message.error) + ".")
    return " ".join(lines)


def render_text_lean(message: AgentMessage) -> str:
    """A stronger concise text baseline carrying the same logical information.

    This renderer exists specifically to avoid overstating the benefit of compact JSON by
    comparing it only against conversationally verbose prose. It is still a UTF-8 textual wire
    representation and still inlines full input/result payloads; only the prose overhead is
    removed. Therefore it is a conservative lower bound for "text collaboration" rather than a
    claim that all text systems naturally look this compact.
    """

    fields: list[str] = [
        f"from={message.source_agent}",
        f"to={message.target_agent}",
        f"action={message.action_type.value}",
        f"capability={message.capability}",
        f"task={message.task_id}",
        f"step={message.step_id}",
        f"trace={message.trace_id}",
        f"timestamp={message.timestamp:.6f}",
        f"status={message.status.value}",
    ]
    optional = (
        ("context", message.context_id),
        ("parent", message.parent_message_id),
        ("cause", message.causation_id),
        ("seq", message.sequence_no),
        ("deadline", f"{message.deadline_at:.6f}" if message.deadline_at is not None else None),
        ("confidence", message.confidence),
    )
    fields.extend(f"{key}={value}" for key, value in optional if value is not None)
    if message.input_parameters:
        fields.append(
            "input="
            + json.dumps(
                message.input_parameters,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    if message.result is not None:
        fields.append(
            "result="
            + json.dumps(
                message.result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    if message.dependencies:
        fields.append("dependencies=" + ",".join(message.dependencies))
    if message.artifact_references:
        fields.append("artifact_refs=" + ",".join(message.artifact_references))
    if message.evidence_references:
        fields.append("evidence_refs=" + ",".join(message.evidence_references))
    if message.state_reference:
        fields.append(f"state_ref={message.state_reference}")
    if message.error:
        fields.append(
            "error="
            + json.dumps(
                message.error,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return " | ".join(fields)


def _compact_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return the complete JSON-native payload for structured transport.

    Earlier versions replaced long strings/lists with summary dictionaries even when no
    authoritative data-plane reference existed.  Because the in-process Channel still
    delivered the original :class:`AgentMessage`, tests passed while a real encode/decode
    hop would silently lose business data and under-count wire bytes.

    Field-name compaction belongs at the message envelope level.  Payload compaction is
    only legal in :func:`_transport_result` when a real state/artifact reference exists.
    """
    return dict(params)




def _transport_result(message: AgentMessage) -> dict[str, Any] | None:
    """Project large producer results onto the control plane when an authoritative ref exists.

    The local :class:`AgentMessage` intentionally retains the complete result for durable
    checkpoint/idempotency replay.  Only the structured wire representation is projected.
    This makes ``state://`` / ``artifact://`` the data plane rather than sending the full
    payload once and replacing it with a reference only afterwards.

    Text mode is unchanged and always renders the complete result, preserving a fair
    text-vs-structured comparison.  Structured messages without authoritative references
    are also unchanged.
    """
    result = message.result
    if not isinstance(result, dict):
        return result

    if (
        message.action_type is ActionType.PLAN
        and message.state_reference
        and "plan" in result
    ):
        plan = result.get("plan")
        n = len(plan.get("operands", [])) if isinstance(plan, dict) else 0
        operation = plan.get("operation") if isinstance(plan, dict) else None
        return {"summary": {"operation": operation, "operands": n}}

    if (
        message.action_type is ActionType.RETRIEVE
        and message.artifact_references
        and "evidence" in result
    ):
        evidence = result.get("evidence")
        n = len(evidence) if isinstance(evidence, dict) else 0
        return {"summary": {"items": n}}

    return _compact_params(result)


def compact_payload(
    message: AgentMessage, *, reference_first: bool = True
) -> dict[str, Any]:
    """Build the canonical compact structured payload before wire encoding."""
    c = FIELD_TO_COMPACT
    compact: dict[str, Any] = {
        c["protocol_version"]: message.protocol_version,
        c["message_id"]: _encode_canonical_id(message.message_id, "msg_", 12),
        c["trace_id"]: _encode_canonical_id(message.trace_id, "trace_", 12),
        c["task_id"]: _encode_canonical_id(message.task_id, "task_", 10),
        c["step_id"]: _encode_step(message.step_id),
        c["source_agent"]: _encode_atom(message.source_agent, AGENT_TO_ATOM),
        c["target_agent"]: _encode_atom(message.target_agent, AGENT_TO_ATOM),
        # AgentMessage already normalizes protocol time to microseconds.  An integer count
        # removes float-repr length jitter while preserving the exact protocol timestamp.
        c["timestamp"]: _encode_unix_time(message.timestamp),
        c["action_type"]: ACTION_TO_ATOM[message.action_type.value],
    }
    if message.capability:
        compact[c["capability"]] = _encode_atom(message.capability, CAPABILITY_TO_ATOM)
    # PENDING is the AgentMessage default and dominates requests; omission is semantically
    # lossless and makes the structured envelope smaller than a lean key=value text baseline.
    if message.status is not MessageStatus.PENDING:
        compact[c["status"]] = STATUS_TO_ATOM[message.status.value]
    if message.context_id is not None:
        compact[c["context_id"]] = _encode_canonical_id(message.context_id, "task_", 10)
    if message.parent_message_id is not None:
        compact[c["parent_message_id"]] = _encode_canonical_id(message.parent_message_id, "msg_", 12)
    if message.causation_id is not None:
        compact[c["causation_id"]] = _encode_canonical_id(message.causation_id, "msg_", 12)
    if message.sequence_no is not None:
        compact[c["sequence_no"]] = message.sequence_no
    if message.deadline_at is not None:
        compact[c["deadline_at"]] = _encode_unix_time(message.deadline_at)
    if message.artifact_references:
        compact[c["artifact_references"]] = message.artifact_references
    if message.evidence_references:
        compact[c["evidence_references"]] = message.evidence_references
    if message.state_reference:
        compact[c["state_reference"]] = message.state_reference
    if message.dependencies:
        compact[c["dependencies"]] = message.dependencies
    if message.confidence is not None:
        compact[c["confidence"]] = round(message.confidence, 3)
    if message.input_parameters:
        compact[c["input_parameters"]] = _compact_params(message.input_parameters)
    if message.result is not None:
        compact[c["result"]] = (
            _transport_result(message)
            if reference_first
            else _compact_params(message.result)
        )
    if message.error:
        compact[c["error"]] = message.error
    if message.idempotency_key:
        compact[c["idempotency_key"]] = message.idempotency_key
    return _map_reference_fields(compact, message.task_id)


def encode_structured(message: AgentMessage, *, reference_first: bool = True) -> str:
    """structured_mode: compact JSON with optional reference-first wire projection."""
    return json.dumps(
        compact_payload(message, reference_first=reference_first),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def encode_structured_msgpack(
    message: AgentMessage, *, reference_first: bool = True
) -> bytes:
    """Optional binary encoding of the same compact payload using MessagePack.

    This is a machine-to-machine transport option.  It deliberately has no token metric;
    experiments compare real wire bytes and codec latency instead.
    """
    try:
        import msgpack
    except ImportError as exc:  # pragma: no cover - depends on optional environment
        raise RuntimeError(
            'MessagePack codec requested but msgpack is not installed; install "agent-runtime[serialize]"'
        ) from exc
    return msgpack.packb(
        compact_payload(message, reference_first=reference_first), use_bin_type=True
    )


def decode_structured(wire: str) -> AgentMessage:
    """Restore AgentMessage from compact JSON."""
    raw = json.loads(wire)
    if not isinstance(raw, dict):
        raise ValueError("structured payload must decode to an object")
    return _message_from_compact(raw)


def _message_from_compact(raw: dict[str, Any]) -> AgentMessage:
    # Reject an incompatible major before interpreting minor-version-specific atom encodings.
    # Otherwise a v1.2 atom payload with only its version field tampered could fail as a schema
    # error first, obscuring the actual protocol-negotiation failure.
    wire_version = raw.get(FIELD_TO_COMPACT["protocol_version"])
    if isinstance(wire_version, str):
        from agent_runtime import PROTOCOL_VERSION
        from agent_runtime.protocol.schema import is_compatible
        from agent_runtime.runtime.errors import ProtocolError

        if not is_compatible(wire_version):
            raise ProtocolError(
                f"协议版本不兼容: 收到 {wire_version}, 期望 {PROTOCOL_VERSION}"
            )

    data: dict[str, Any] = {}
    atom_wire = _wire_uses_semantic_atoms(wire_version)
    for ck, value in raw.items():
        field = COMPACT_TO_FIELD.get(ck)
        if field is None:
            continue
        if field == "message_id":
            data[field] = (
                _decode_canonical_id(value, "msg_", 12, field=field) if atom_wire else value
            )
        elif field == "trace_id":
            data[field] = (
                _decode_canonical_id(value, "trace_", 12, field=field) if atom_wire else value
            )
        elif field in {"task_id", "context_id"}:
            data[field] = (
                _decode_canonical_id(value, "task_", 10, field=field) if atom_wire else value
            )
        elif field in {"parent_message_id", "causation_id"}:
            data[field] = (
                _decode_canonical_id(value, "msg_", 12, field=field) if atom_wire else value
            )
        elif field == "step_id":
            data[field] = _decode_step(value) if atom_wire else value
        elif field in {"source_agent", "target_agent"}:
            data[field] = _decode_atom(value, ATOM_TO_AGENT, field=field)
        elif field == "action_type":
            data[field] = ActionType(_decode_atom(value, ATOM_TO_ACTION, field=field))
        elif field == "capability":
            data[field] = _decode_atom(value, ATOM_TO_CAPABILITY, field=field)
        elif field == "status":
            data[field] = MessageStatus(_decode_atom(value, ATOM_TO_STATUS, field=field))
        elif field in {"timestamp", "deadline_at"}:
            data[field] = _decode_unix_time(value, field=field, atom_wire=atom_wire)
        else:
            data[field] = value

    task_id = data.get("task_id")
    if atom_wire and isinstance(task_id, str):
        data = _map_reference_fields(data, task_id, decode=True)

    # Wire decode is a protocol trust boundary.  Enforce major-version compatibility
    # here instead of relying on callers to remember a separate validate_message() step.
    from agent_runtime.protocol.schema import validate_message

    return validate_message(data)


def decode_structured_msgpack(wire: bytes) -> AgentMessage:
    try:
        import msgpack
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            'MessagePack codec requested but msgpack is not installed; install "agent-runtime[serialize]"'
        ) from exc
    raw = msgpack.unpackb(wire, raw=False, strict_map_key=False)
    if not isinstance(raw, dict):
        raise ValueError("MessagePack structured payload must decode to a map")
    return _message_from_compact(raw)


def render_structured(message: AgentMessage) -> str:
    """向后兼容别名。"""
    return encode_structured(message)


def measure(
    message: AgentMessage,
    mode: str,
    tokenizer: Tokenizer,
    *,
    reference_first: bool = True,
    text_style: str = "verbose",
) -> tuple[str, int, int, int]:
    """返回 ``(wire, tokens, chars, utf8_bytes)``。

    ``utf8_bytes`` 是与 tokenizer 无关的实际序列化字节数，作为通信开销的
    模型无关辅助口径。
    """
    if mode == "text":
        if text_style == "verbose":
            wire = render_text(message)
        elif text_style == "lean":
            wire = render_text_lean(message)
        else:
            raise ValueError(f"unknown text style: {text_style}")
    else:
        wire = encode_structured(message, reference_first=reference_first)
    return wire, tokenizer.count(wire), count_chars(wire), len(wire.encode("utf-8"))


__all__ = [
    "render_text",
    "render_text_lean",
    "render_structured",
    "encode_structured",
    "decode_structured",
    "measure",
    "FIELD_TO_COMPACT",
    "COMPACT_TO_FIELD",
    "compact_payload",
    "encode_structured_msgpack",
    "decode_structured_msgpack",
    "AGENT_TO_ATOM",
    "ACTION_TO_ATOM",
    "STATUS_TO_ATOM",
    "CAPABILITY_TO_ATOM",
]
