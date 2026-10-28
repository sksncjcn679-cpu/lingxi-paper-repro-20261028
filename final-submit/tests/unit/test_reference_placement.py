from agent_runtime.protocol.message import ActionType
from agent_runtime.protocol.placement import choose_reference_placement


def test_small_plan_stays_inline():
    d = choose_reference_placement(
        action_type=ActionType.PLAN,
        result={"plan": {"operation": "sum", "operands": ["a", "b"]}},
        state_reference="state://task/0123456789abcdef",
    )
    assert d.use_reference is False
    assert d.saving_bytes == 0


def test_large_plan_uses_reference_without_fixed_threshold():
    d = choose_reference_placement(
        action_type=ActionType.PLAN,
        result={"plan": {"operation": "sum", "operands": [f"k{i}" for i in range(256)]}},
        state_reference="state://task/0123456789abcdef",
    )
    assert d.use_reference is True
    assert d.reference_bytes < d.inline_bytes
    assert d.saving_bytes == d.inline_bytes - d.reference_bytes


def test_large_evidence_uses_artifact_reference():
    d = choose_reference_placement(
        action_type=ActionType.RETRIEVE,
        result={"evidence": {f"k{i}": i for i in range(128)}},
        artifact_reference="artifact://task/t/0123456789abcdef",
    )
    assert d.use_reference is True
    assert d.saving_ratio > 0.5


def test_cost_estimator_rejects_non_json_payload_instead_of_stringifying() -> None:
    import pytest

    from agent_runtime.protocol.message import ActionType
    from agent_runtime.protocol.placement import choose_reference_placement

    with pytest.raises(TypeError):
        choose_reference_placement(
            action_type=ActionType.PLAN,
            result={"plan": {"operation": "x", "operands": [object()]}},
            state_reference="state://sha256:" + "a" * 64,
        )
