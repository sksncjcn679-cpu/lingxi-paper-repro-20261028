"""Interoperability adapters. These are data-model bridges, not network bindings."""

from agent_runtime.interop.a2a import (
    A2ATaskState,
    agent_card_to_a2a,
    artifact_ref_to_a2a,
    from_a2a_message,
    task_view,
    to_a2a_message,
)

__all__ = [
    "A2ATaskState",
    "agent_card_to_a2a",
    "artifact_ref_to_a2a",
    "from_a2a_message",
    "task_view",
    "to_a2a_message",
]
