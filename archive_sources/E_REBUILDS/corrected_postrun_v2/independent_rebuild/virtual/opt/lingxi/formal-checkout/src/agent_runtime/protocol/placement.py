"""Cost-aware control/data-plane placement for producer results.

A reference is not automatically cheaper than a small inline payload.  This module makes the
choice from the *actual compact JSON marginal bytes* of the two legal representations instead
of a hard-coded payload threshold.  Materialization and placement are deliberately separated:
a state/artifact may still be produced for the next agent even when the producer->orchestrator
reply is cheaper inline.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from agent_runtime.protocol.message import ActionType


@dataclass(frozen=True)
class PlacementDecision:
    use_reference: bool
    inline_bytes: int
    reference_bytes: int

    @property
    def saving_bytes(self) -> int:
        return max(0, self.inline_bytes - self.reference_bytes)

    @property
    def saving_ratio(self) -> float:
        return (self.saving_bytes / self.inline_bytes) if self.inline_bytes else 0.0


def _wire_bytes(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _plan_summary(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {"operation": None, "operands": 0}
    return {
        "operation": plan.get("operation"),
        "operands": len(plan.get("operands", [])),
    }


def _evidence_summary(evidence: Any) -> dict[str, Any]:
    return {"items": len(evidence) if isinstance(evidence, dict) else 0}


def choose_reference_placement(
    *,
    action_type: ActionType,
    result: dict[str, Any],
    state_reference: str | None = None,
    artifact_reference: str | None = None,
) -> PlacementDecision:
    """Return the cheaper exact marginal structured-wire representation.

    The comparison covers the compact fields that differ between inline and reference-first
    producer replies.  Common message metadata cancels out, so no model/tokenizer heuristic is
    involved.  Ties stay inline to avoid adding an unnecessary data-plane dependency.
    """
    inline = {"rs": result}

    if action_type is ActionType.PLAN and state_reference and "plan" in result:
        referenced = {
            "sr": state_reference,
            "rs": {"summary": _plan_summary(result.get("plan"))},
        }
    elif action_type is ActionType.RETRIEVE and artifact_reference and "evidence" in result:
        referenced = {
            "ar": [artifact_reference],
            "rs": {"summary": _evidence_summary(result.get("evidence"))},
        }
    else:
        n = _wire_bytes(inline)
        return PlacementDecision(False, n, n)

    inline_bytes = _wire_bytes(inline)
    reference_bytes = _wire_bytes(referenced)
    return PlacementDecision(
        use_reference=reference_bytes < inline_bytes,
        inline_bytes=inline_bytes,
        reference_bytes=reference_bytes,
    )


__all__ = ["PlacementDecision", "choose_reference_placement"]
