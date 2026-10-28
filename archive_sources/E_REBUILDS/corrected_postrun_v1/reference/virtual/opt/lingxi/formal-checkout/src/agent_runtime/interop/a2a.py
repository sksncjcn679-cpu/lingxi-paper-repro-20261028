"""A2A 1.0 *data-model* compatibility adapter.

This module maps Agent Runtime's internal low-overhead objects to JSON-friendly
structures shaped like the A2A 1.0 canonical Message/Artifact/Task model.
It is intentionally **not** an A2A network server or a conformance claim: no
HTTP+JSON/JSON-RPC/gRPC binding, AgentCard discovery, authentication, streaming,
or protocol error surface is implemented here.

The split mirrors A2A's semantics: Messages carry interaction, while durable task
outputs are represented as Artifacts. Internal ``artifact://`` and ``state://``
references remain local URIs unless an external binding translates them to a
remotely dereferenceable URL.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from agent_runtime.artifact_store.store import ArtifactRef
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.registry.capability import AgentCard

A2ARole = Literal["ROLE_USER", "ROLE_AGENT"]


class A2ATaskState(str, Enum):
    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    REJECTED = "TASK_STATE_REJECTED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"


def _protocol_data(message: AgentMessage) -> dict[str, Any]:
    data: dict[str, Any] = {
        "protocolVersion": message.protocol_version,
        "sourceAgent": message.source_agent,
        "targetAgent": message.target_agent,
        "stepId": message.step_id,
        "actionType": message.action_type.value,
        "capability": message.capability,
        "status": message.status.value,
    }
    if message.input_parameters:
        data["inputParameters"] = message.input_parameters
    if message.result is not None:
        data["result"] = message.result
    if message.error is not None:
        data["error"] = message.error
    return data


def to_a2a_message(message: AgentMessage, *, role: A2ARole) -> dict[str, Any]:
    """Map an internal message to an A2A-1.0-shaped JSON Message.

    A structured ``data`` Part carries runtime-specific fields. Artifact/evidence/
    state references become URL Parts so they remain references instead of being
    inlined. Runtime-only lineage is kept in Message metadata.
    """
    if role not in {"ROLE_USER", "ROLE_AGENT"}:
        raise ValueError("role must be ROLE_USER or ROLE_AGENT")

    parts: list[dict[str, Any]] = [
        {"data": _protocol_data(message), "mediaType": "application/json"}
    ]
    for uri in message.artifact_references:
        parts.append({
            "url": uri,
            "mediaType": "application/vnd.agent-runtime.artifact-ref",
        })
    for uri in message.evidence_references:
        parts.append({
            "url": uri,
            "mediaType": "application/vnd.agent-runtime.evidence-ref",
        })
    if message.state_reference:
        parts.append({
            "url": message.state_reference,
            "mediaType": "application/vnd.agent-runtime.state-ref",
        })

    metadata: dict[str, Any] = {"traceId": message.trace_id}
    if message.parent_message_id is not None:
        metadata["parentMessageId"] = message.parent_message_id
    if message.causation_id is not None:
        metadata["causationId"] = message.causation_id
    if message.sequence_no is not None:
        metadata["sequenceNo"] = message.sequence_no
    if message.deadline_at is not None:
        metadata["deadlineAtUnix"] = message.deadline_at
    if message.dependencies:
        metadata["dependencies"] = list(message.dependencies)
    if message.confidence is not None:
        metadata["confidence"] = message.confidence
    if message.idempotency_key is not None:
        metadata["idempotencyKey"] = message.idempotency_key

    out: dict[str, Any] = {
        "messageId": message.message_id,
        "taskId": message.task_id,
        "role": role,
        "parts": parts,
        "metadata": metadata,
    }
    if message.context_id is not None:
        out["contextId"] = message.context_id
    return out


def from_a2a_message(document: dict[str, Any]) -> AgentMessage:
    """Restore an internal message produced by :func:`to_a2a_message`.

    This parser intentionally accepts only the adapter's own runtime extension data,
    not arbitrary A2A messages. That boundary avoids pretending to be a full A2A
    implementation.
    """
    if not isinstance(document, dict):
        raise ValueError("A2A message must be an object")
    parts = document.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("A2A message requires at least one Part")
    data_part = next((part for part in parts if isinstance(part, dict) and "data" in part), None)
    if data_part is None or not isinstance(data_part.get("data"), dict):
        raise ValueError("runtime A2A adapter requires a structured data Part")
    data = data_part["data"]
    metadata = document.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("A2A message metadata must be an object")

    artifact_refs: list[str] = []
    evidence_refs: list[str] = []
    state_ref: str | None = None
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("url"), str):
            continue
        mt = part.get("mediaType")
        if mt == "application/vnd.agent-runtime.artifact-ref":
            artifact_refs.append(part["url"])
        elif mt == "application/vnd.agent-runtime.evidence-ref":
            evidence_refs.append(part["url"])
        elif mt == "application/vnd.agent-runtime.state-ref":
            state_ref = part["url"]

    try:
        action = ActionType(data.get("actionType", "result"))
    except ValueError as exc:
        raise ValueError(f"unsupported runtime actionType: {data.get('actionType')!r}") from exc
    try:
        status = MessageStatus(data.get("status", "pending"))
    except ValueError as exc:
        raise ValueError(f"unsupported runtime status: {data.get('status')!r}") from exc

    return AgentMessage(
        protocol_version=str(data.get("protocolVersion", "1.0")),
        message_id=str(document.get("messageId", "")),
        trace_id=str(metadata.get("traceId", "unknown")),
        task_id=str(document.get("taskId", "")),
        step_id=str(data.get("stepId", "")),
        context_id=document.get("contextId"),
        parent_message_id=metadata.get("parentMessageId"),
        causation_id=metadata.get("causationId"),
        sequence_no=metadata.get("sequenceNo"),
        deadline_at=metadata.get("deadlineAtUnix"),
        source_agent=str(data.get("sourceAgent", "unknown")),
        target_agent=str(data.get("targetAgent", "unknown")),
        action_type=action,
        capability=str(data.get("capability", "")),
        input_parameters=data.get("inputParameters") or {},
        result=data.get("result"),
        status=status,
        dependencies=list(metadata.get("dependencies") or []),
        artifact_references=artifact_refs,
        evidence_references=evidence_refs,
        state_reference=state_ref,
        confidence=metadata.get("confidence"),
        error=data.get("error"),
        idempotency_key=metadata.get("idempotencyKey"),
    )


def artifact_ref_to_a2a(ref: ArtifactRef, *, name: str | None = None) -> dict[str, Any]:
    """Map a content-addressed ArtifactRef to an A2A-shaped Artifact."""
    return {
        "artifactId": ref.content_hash,
        "name": name or ref.uri.rsplit("/", 1)[-1],
        "description": ref.summary,
        "parts": [
            {
                "url": ref.uri,
                "mediaType": ref.media_type,
            }
        ],
        "metadata": {
            "sha256": ref.content_hash,
            "sizeBytes": ref.size,
            "scope": ref.scope,
        },
    }


def agent_card_to_a2a(
    card: AgentCard,
    *,
    interfaces: list[dict[str, str]],
    agent_version: str,
) -> dict[str, Any]:
    """Export an internal discovery card to an A2A-1.0-shaped AgentCard.

    ``interfaces`` are explicit because the core runtime does not invent a network endpoint.
    Supplying this metadata does not start or validate an A2A binding.
    """
    if not interfaces:
        raise ValueError("A2A AgentCard requires at least one supported interface")
    normalized_interfaces: list[dict[str, str]] = []
    for interface in interfaces:
        url = interface.get("url")
        binding = interface.get("protocolBinding")
        version = interface.get("protocolVersion", "1.0")
        if not url or not binding:
            raise ValueError("each A2A interface requires url and protocolBinding")
        normalized_interfaces.append(
            {"url": url, "protocolBinding": binding, "protocolVersion": version}
        )

    skills = []
    for capability in card.capabilities:
        spec = card.describe(capability)
        skill = {
            "id": capability,
            "name": capability.replace("_", " ").title(),
            "description": spec.description if spec is not None else f"Internal runtime capability: {capability}",
            "tags": sorted(set([capability, *card.actions])),
        }
        if spec is not None:
            skill["metadata"] = {
                "inputSchema": spec.input_schema,
                "outputSchema": spec.output_schema,
            }
        skills.append(skill)
    return {
        "name": card.agent_name,
        "description": card.description or f"Agent Runtime agent {card.agent_name}",
        "supportedInterfaces": normalized_interfaces,
        "version": agent_version,
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": skills,
    }


def task_view(
    *,
    task_id: str,
    context_id: str | None,
    state: A2ATaskState,
    history: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an A2A-shaped Task snapshot from runtime state."""
    out: dict[str, Any] = {
        "id": task_id,
        "status": {"state": state.value},
        "artifacts": list(artifacts or []),
        "history": list(history or []),
        "metadata": dict(metadata or {}),
    }
    if context_id is not None:
        out["contextId"] = context_id
    return out


__all__ = [
    "A2ATaskState",
    "to_a2a_message",
    "from_a2a_message",
    "artifact_ref_to_a2a",
    "agent_card_to_a2a",
    "task_view",
]
