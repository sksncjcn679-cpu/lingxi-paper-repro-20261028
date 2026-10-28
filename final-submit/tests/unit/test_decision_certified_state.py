"""fable9: decision-certified precision ladder for non-text state (int4 -> int8 -> fp16 -> fp32)."""

from __future__ import annotations

import numpy as np
import pytest

from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.state_exchange.exchange import StateExchange, _int4_pack, _int4_unpack


@pytest.mark.parametrize("dim", [1, 2, 7, 256, 257])
def test_int4_pack_roundtrip_error_bound(dim):
    rng = np.random.default_rng(dim)
    x = rng.normal(size=dim).astype(np.float32)
    packed, scale = _int4_pack(x)
    assert packed.dtype == np.uint8 and packed.size == (dim + 1) // 2
    y = _int4_unpack(packed, scale, dim)
    assert y.shape == x.shape and np.max(np.abs(x - y)) <= scale / 2 + 1e-6


def test_int4_unpack_rejects_size_mismatch():
    packed, scale = _int4_pack(np.ones(10, dtype=np.float32))
    from agent_runtime.runtime.errors import ReferenceError_

    with pytest.raises(ReferenceError_):
        _int4_unpack(packed, scale, 12)


def _probe_for(emb, operands):
    vecs = np.asarray(emb.embed(operands), dtype=np.float32)
    return lambda v: tuple(int(i) for i in np.argsort(-(vecs @ np.asarray(v, dtype=np.float32)), kind="stable"))


def test_certified_state_preserves_receiver_decision_exactly():
    emb = MockEmbedding(dim=256)
    ex = StateExchange(emb, task_id="t", precision="adaptive")
    plan = {"operation": "diagnose_service", "operands": ["service_status", "port_probe", "journal_excerpt", "strategy"]}
    probe = _probe_for(emb, plan["operands"])
    ref = ex.put_plan_state(plan, decision_probe=probe)
    assert ref.precision in {"int4", "int8", "float16", "float32"}
    state = ex.get_plan_state(ref.uri)
    import json

    text = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    full = np.asarray(emb.embed([text])[0], dtype=np.float32)
    assert probe(state["embedding"]) == probe(full)  # the receiver's decision is identical
    ex.close()


def test_ladder_falls_back_when_the_decision_would_change():
    emb = MockEmbedding(dim=256)
    ex = StateExchange(emb, task_id="t", precision="adaptive")
    calls = []

    def fragile(v):  # flips unless the vector is bit-exact float32
        calls.append(1)
        return bytes(np.asarray(v, dtype=np.float32).tobytes())

    ref = ex.put_plan_state({"operation": "x", "operands": ["a", "b"]}, decision_probe=fragile)
    assert ref.precision == "float32" and ex.certificate_rejections == 1
    ex.close()


def test_without_probe_or_when_disabled_legacy_adaptive_is_used(monkeypatch):
    emb = MockEmbedding(dim=256)
    ex = StateExchange(emb, task_id="t", precision="adaptive")
    assert ex.put_plan_state({"operation": "x", "operands": ["a", "b"]}).precision in {"int8", "float16"}
    ex.close()
    monkeypatch.setenv("AGENT_DECISION_CERTIFIED", "0")
    ex2 = StateExchange(emb, task_id="t", precision="adaptive")
    probe = _probe_for(emb, ["a", "b"])
    assert ex2.put_plan_state({"operation": "y", "operands": ["a", "b"]}, decision_probe=probe).precision in {"int8", "float16"}
    ex2.close()


def test_contest_workload_uses_certified_int4_and_keeps_success():
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.evaluation.runner import ExperimentConfig, run_once

    cfg = ExperimentConfig("SS", "x", "structured", True, False, True, structured_wire_codec="session_msgpack",
                           control_transport="inproc", state_shared_memory=True, state_precision="adaptive")
    row = run_once(cfg, _self_contained_tasks, 42)
    assert row["task_success"] == 1.0
    # Certified precision depends on the embedding: every transfer is certified at the cheapest
    # decision-preserving level (int4 or int8).
    assert row["state_decision_certificates"] >= 1
    assert row["state_int4_transfers"] + row["state_int8_transfers"] >= 1
