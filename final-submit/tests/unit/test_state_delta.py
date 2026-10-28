from __future__ import annotations

import pytest

from agent_runtime.state_exchange.delta import (
    StateConflictError,
    StateIntegrityError,
    VersionedStateLedger,
    apply_patch,
    diff_state,
)


def _dag(nodes: int = 64) -> dict:
    return {
        "operation": "pipeline",
        "nodes": [
            {"id": i, "kind": "tool", "status": "pending", "weight": i % 7}
            for i in range(nodes)
        ],
        "edges": [[i, i + 1] for i in range(nodes - 1)],
        "meta": {"owner": "planner", "revision": 1},
    }


def test_patch_roundtrip_nested_dict_and_list() -> None:
    old = {"a": [1, {"x": 2}], "remove": True}
    new = {"a": [1, {"x": 3}, 4], "add": "ok"}
    ops = diff_state(old, new)
    assert apply_patch(old, ops) == new


def test_small_plan_update_is_delta_and_saves_bytes() -> None:
    ledger = VersionedStateLedger(namespace="t")
    base = _dag(128)
    first = ledger.put_snapshot(base)
    updated = _dag(128)
    updated["nodes"][17]["status"] = "done"
    updated["meta"]["revision"] = 2

    ref = ledger.compare_and_update(first.uri, updated)
    assert ref.kind == "delta"
    assert ref.delta_ops == 2
    assert ref.materialized_bytes < ref.logical_bytes
    assert ref.saving_ratio > 0.90
    assert ledger.resolve(ref.uri) == updated


def test_large_change_falls_back_to_snapshot() -> None:
    ledger = VersionedStateLedger(delta_ratio_threshold=0.80)
    first = ledger.put_snapshot(_dag(16))
    replacement = {"totally": "different", "payload": list(range(200))}
    ref = ledger.update(first.uri, replacement)
    assert ref.kind == "snapshot"
    # fable15: the stored form is the compact encoding, never larger than canonical JSON.
    assert 0 < ref.materialized_bytes <= ref.logical_bytes
    assert ledger.resolve(ref.uri) == replacement


def test_compare_and_update_detects_stale_writer() -> None:
    ledger = VersionedStateLedger()
    first = ledger.put_snapshot({"v": 0, "payload": [0] * 64})
    second = ledger.compare_and_update(first.uri, {"v": 1, "payload": [0] * 64})
    assert ledger.head_uri == second.uri
    with pytest.raises(StateConflictError):
        ledger.compare_and_update(first.uri, {"v": 2, "payload": [0] * 64})


def test_chain_depth_forces_periodic_full_snapshot() -> None:
    ledger = VersionedStateLedger(max_delta_depth=2)
    ref = ledger.put_snapshot(_dag(32))
    saw_snapshot_reset = False
    for revision in range(2, 7):
        value = ledger.resolve(ref.uri)
        value["meta"]["revision"] = revision
        ref = ledger.compare_and_update(ref.uri, value)
        if revision > 2 and ref.kind == "snapshot":
            saw_snapshot_reset = True
            assert ref.depth == 0
    assert saw_snapshot_reset


def test_integrity_verification_detects_tampered_patch() -> None:
    ledger = VersionedStateLedger()
    first = ledger.put_snapshot(_dag(64))
    updated = _dag(64)
    updated["nodes"][0]["status"] = "done"
    ref = ledger.update(first.uri, updated)
    assert ref.kind == "delta"
    # Deliberately corrupt the private test fixture to prove hash verification is enforced.
    entry = ledger._entries[ref.uri]  # noqa: SLF001
    tampered = diff_state(_dag(64), {**_dag(64), "extra": "tampered"})
    if entry.blob is not None:  # fable15 compact storage: tamper the stored bytes
        from agent_runtime.state_exchange.compact import encode_ops

        entry.blob = encode_ops(tampered, ledger.namespace)
    else:
        entry.ops = tampered
    with pytest.raises(StateIntegrityError):
        ledger.resolve(ref.uri)


def test_deduplicated_put_snapshot_moves_head_consistently():
    ledger = VersionedStateLedger()
    first = ledger.put_snapshot({"v": 1})
    second = ledger.compare_and_update(first.uri, {"v": 2})
    assert ledger.head_uri == second.uri

    selected = ledger.put_snapshot({"v": 1})
    assert selected.deduplicated is True
    assert selected.materialized_bytes == 0
    assert ledger.head_uri == first.uri

    third = ledger.compare_and_update(first.uri, {"v": 3})
    assert ledger.resolve(third.uri) == {"v": 3}


def test_versioned_state_rejects_non_finite_json_numbers():
    import math
    import pytest

    ledger = VersionedStateLedger(namespace="finite")
    with pytest.raises(ValueError):
        ledger.put_snapshot({"x": math.nan})
    base = ledger.put_snapshot({"x": 1.0})
    with pytest.raises(ValueError):
        ledger.update(base.uri, {"x": math.inf})


def test_fable15_compact_storage_roundtrips_and_detects_corruption():
    import random

    from agent_runtime.state_exchange.compact import decode_ops, decode_snapshot, encode_ops, encode_snapshot
    from agent_runtime.state_exchange.delta import VersionedStateLedger, diff_state

    rng = random.Random(3)
    ns = "task_abc"
    ref = f"state://{ns}/" + "ab" * 16
    for _ in range(200):
        v = {"stage": rng.choice(["planner", "x"]), "plan_ref": ref, "n": rng.randint(-10**12, 10**12),
             "f": rng.random(), "big": 2 ** 70, "keys": [rng.choice("abc") for _ in range(3)], "nested": {"count": 1, "y": None}}
        assert decode_snapshot(encode_snapshot(v, ns), ns) == v
        w = dict(v, stage="reviewer", extra=[1, 2])
        ops = diff_state(v, w)
        assert decode_ops(encode_ops(ops, ns), ns) == ops
    led = VersionedStateLedger(namespace=ns)
    a = led.put_snapshot({"stage": "planner", "plan_ref": ref})
    b = led.update(a.uri, {"stage": "retriever", "plan_ref": ref, "evidence": {"count": 3, "keys": ["x", "y", "z"]}})
    assert a.materialized_bytes < a.logical_bytes and led.resolve(b.uri)["evidence"]["count"] == 3
    led._entries[a.uri].blob = b"\x01\xff"  # noqa: SLF001 - corrupt stored bytes
    led._verified.clear()  # noqa: SLF001
    with pytest.raises(Exception):
        led.resolve(b.uri)
