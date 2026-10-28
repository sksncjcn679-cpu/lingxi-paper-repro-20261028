from __future__ import annotations

import pytest

from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.state_exchange.delta import StateConflictError
from agent_runtime.state_exchange.exchange import StateExchange


def test_state_exchange_exposes_versioned_delta_updates() -> None:
    ex = StateExchange(MockEmbedding(), task_id="workflow")
    base = {
        "nodes": [{"id": i, "status": "pending", "payload": "x" * 32} for i in range(64)],
        "revision": 1,
    }
    first = ex.put_versioned_state(base)
    changed = {**base, "nodes": [dict(n) for n in base["nodes"]], "revision": 2}
    changed["nodes"][5]["status"] = "done"
    second = ex.update_versioned_state(first.uri, changed)
    assert second.kind == "delta"
    assert second.materialized_bytes < second.logical_bytes
    assert ex.get_versioned_state(second.uri) == changed

    stale = {**base, "revision": 3}
    with pytest.raises(StateConflictError):
        ex.update_versioned_state(first.uri, stale)
