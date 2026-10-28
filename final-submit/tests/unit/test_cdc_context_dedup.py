"""fable11: content-defined chunking + context chunk pool + long-horizon workload."""

from __future__ import annotations

import hashlib
import os

import pytest

from agent_runtime.artifact_store.cdc import ChunkPool, chunk_boundaries, split


def _log(n: int) -> bytes:
    return "\n".join(f"line {i} trace={hashlib.sha256(str(i).encode()).hexdigest()}" for i in range(n)).encode()


def test_split_roundtrip_and_bounds():
    for n in (0, 1, 100, 2000, 20000):
        data = os.urandom(n)
        parts = split(data)
        assert b"".join(parts) == data
        assert all(len(p) <= 8192 for p in parts)
        assert all(len(p) >= 512 for p in parts[:-1])


def test_append_keeps_earlier_chunks_identical():
    base = _log(400)
    grown = base + b"\n" + _log(420)[len(_log(400)):]
    a, b = split(base), split(grown)
    common = sum(1 for x, y in zip(a, b) if x == y)
    assert common >= len(a) - 1  # only the tail chunk may differ


def test_artifact_store_cdc_dedups_across_tasks_and_verifies():
    from agent_runtime.artifact_store.store import ArtifactStore
    from agent_runtime.runtime.errors import ReferenceError_

    pool = ChunkPool()
    s1, s2 = ArtifactStore(task_id="t1"), ArtifactStore(task_id="t2")
    s1.default_chunk_pool = s2.default_chunk_pool = pool
    doc1 = {"journal": _log(900).decode()}
    doc2 = {"journal": _log(950).decode()}
    r1 = s1.put(doc1)
    r2 = s2.put(doc2)
    assert s1.cdc_blobs == 1 and s2.cdc_blobs == 1
    assert s2.materialized_bytes < 0.5 * s1.materialized_bytes  # mostly shared chunks
    assert s1.get_json(r1.uri) == doc1 and s2.get_json(r2.uri) == doc2
    # corrupt one shared chunk -> reassembly must fail closed
    d = next(iter(pool._chunks))
    pool._chunks[d] = b"ARR1" + b"garbage"
    with pytest.raises(ReferenceError_):
        s2.get_bytes(r2.uri)


def test_small_blobs_and_magic_prefix_are_safe():
    from agent_runtime.artifact_store.store import ArtifactStore, decode_physical, encode_physical

    raw = b"ARC1" + b"x" * 10
    assert decode_physical(encode_physical(raw)) == raw
    s = ArtifactStore(task_id="t")
    s.default_chunk_pool = ChunkPool()
    ref = s.put({"small": "x"})
    assert s.cdc_blobs == 0 and s.get_json(ref.uri) == {"small": "x"}


def test_long_horizon_all_arms_succeed_and_scale():
    from agent_runtime.evaluation.long_horizon_benchmark import run_long_horizon_benchmark

    r = run_long_horizon_benchmark((2, 6))["results"]
    for k in ("2", "6"):
        for arm in ("T", "S", "SS", "SS_cdc"):
            assert r[k][arm]["success"] == 1.0
    assert r["6"]["SS"]["tokens"] / 6 < 1.5 * r["2"]["SS"]["tokens"] / 2     # ~flat per round
    assert r["6"]["T"]["tokens"] / 6 > 1.8 * r["2"]["T"]["tokens"] / 2       # grows with history
