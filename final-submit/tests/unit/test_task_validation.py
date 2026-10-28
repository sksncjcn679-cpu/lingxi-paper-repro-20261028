from __future__ import annotations

import math

import pytest

from agent_runtime.runtime.task import Task, VerdictResult


def test_task_rejects_empty_identity_and_non_finite_deadline():
    with pytest.raises(ValueError, match="topic"):
        Task(topic="", description="x")
    with pytest.raises(ValueError, match="description"):
        Task(topic="x", description="")
    with pytest.raises(ValueError, match="task_id"):
        Task(topic="x", description="y", task_id="")
    with pytest.raises(ValueError, match="deadline_at"):
        Task(topic="x", description="y", deadline_at=math.nan)


def test_verdict_quality_is_bounded_and_finite():
    with pytest.raises(ValueError, match="quality"):
        VerdictResult(True, math.nan)
    with pytest.raises(ValueError, match="quality"):
        VerdictResult(True, 1.1)


def test_task_rejects_non_json_or_non_finite_payloads_and_bad_tags():
    with pytest.raises(ValueError, match="payload"):
        Task(topic="x", description="y", payload={"n": math.nan})
    with pytest.raises(ValueError, match="payload"):
        Task(topic="x", description="y", payload={"obj": object()})
    with pytest.raises(ValueError, match="tags"):
        Task(topic="x", description="y", tags=[""])
    with pytest.raises(ValueError, match="context_id"):
        Task(topic="x", description="y", context_id=" ")
