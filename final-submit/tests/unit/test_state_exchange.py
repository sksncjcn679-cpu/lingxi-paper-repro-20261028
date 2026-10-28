"""非文本状态交换单元测试。"""

from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pytest

from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.runtime.errors import ReferenceError_
from agent_runtime.state_exchange.exchange import StateExchange
from agent_runtime.state_exchange.shared_buffer import SharedVectorBuffer


def _exchange(use_shm=False):
    return StateExchange(MockEmbedding(dim=64, seed=1), task_id="t1", use_shared_memory=use_shm)


def test_put_get_plan_state():
    ex = _exchange()
    plan = {"operation": "sum", "operands": ["a", "b", "c"]}
    ref = ex.put_plan_state(plan)
    assert ref.uri.startswith("state://t1/")
    assert ref.nbytes > 0
    state = ex.get_plan_state(ref.uri)
    assert state["dag"] == plan
    assert state["embedding"].shape == (64,)
    assert state["vector"].shape == (3,)


def test_embed_text_vector_ref():
    ex = _exchange()
    ref = ex.embed_text("fix async resource leak")
    assert ref.uri.startswith("vector://t1/")
    vec = ex.get_vector(ref.uri)
    assert vec.shape == (64,)


def test_invalidate_state_raises():
    ex = _exchange()
    ref = ex.put_plan_state({"operation": "sum", "operands": ["a"]})
    ex.invalidate(ref.uri)
    with pytest.raises(ReferenceError_):
        ex.get_plan_state(ref.uri)


def test_shared_buffer_inproc_roundtrip():
    buf = SharedVectorBuffer(use_shared_memory=False)
    arr = np.arange(10, dtype=np.float32)
    n = buf.put("k", arr)
    assert n == arr.nbytes
    assert np.array_equal(buf.get("k"), arr)
    buf.close()


def test_shared_buffer_shm_or_degrade():
    buf = SharedVectorBuffer(use_shared_memory=True)
    arr = np.arange(8, dtype=np.float32)
    buf.put("k", arr)
    # 无论使用共享内存还是降级，取回的数据都应正确。
    assert np.array_equal(buf.get("k"), arr)
    buf.close()


def test_default_float16_state_reduces_materialized_bytes_and_is_read_only():
    ex = StateExchange(
        MockEmbedding(dim=64, seed=1),
        task_id="t-f16",
        use_shared_memory=False,
        precision="float16",
        deduplicate=True,
    )
    plan = {"operation": "sum", "operands": ["a", "b"]}
    ref = ex.put_plan_state(plan)
    # logical = 64*4-byte float32 embedding + 3*4-byte compact vector
    assert ref.logical_nbytes == 64 * 4 + 3 * 4
    # physical = 64*2-byte float16 embedding + unchanged float32 compact vector
    assert ref.nbytes == 64 * 2 + 3 * 4
    assert ref.materialized_bytes == ref.nbytes
    assert ref.dtype == "float16"
    state = ex.get_plan_state(ref.uri)
    assert state["embedding"].dtype == np.float16
    assert state["embedding"].flags.writeable is False
    with pytest.raises(ValueError):
        state["embedding"][0] = 1.0
    ex.close()



def test_float16_transport_preserves_mock_embedding_direction():
    provider = MockEmbedding(dim=256, seed=7)
    ex = StateExchange(
        provider,
        task_id="t-fidelity",
        precision="float16",
        deduplicate=True,
    )
    plan = {"operation": "sum", "operands": ["alpha", "beta", "gamma"]}
    ref = ex.put_plan_state(plan)
    stored = ex.get_plan_state(ref.uri)["embedding"].astype(np.float32)
    plan_text = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    original = provider.embed([plan_text])[0].astype(np.float32)
    cosine = float(np.dot(original, stored) / (np.linalg.norm(original) * np.linalg.norm(stored)))
    assert cosine > 0.9999
    ex.close()

def test_content_addressed_state_dedup_avoids_second_materialization():
    ex = StateExchange(
        MockEmbedding(dim=64, seed=1),
        task_id="t-dedup",
        precision="float32",
        deduplicate=True,
    )
    plan = {"operation": "sum", "operands": ["a", "b"]}
    first = ex.put_plan_state(plan)
    second = ex.put_plan_state(plan)
    assert first.uri == second.uri
    assert first.materialized_bytes == first.nbytes > 0
    assert second.materialized_bytes == 0
    assert second.deduplicated is True
    ex.close()


def test_no_dedup_mode_re_materializes_without_leaking_or_changing_uri():
    ex = StateExchange(
        MockEmbedding(dim=64, seed=1),
        task_id="t-no-dedup",
        precision="float32",
        deduplicate=False,
    )
    plan = {"operation": "sum", "operands": ["a"]}
    first = ex.put_plan_state(plan)
    second = ex.put_plan_state(plan)
    assert first.uri == second.uri
    assert first.materialized_bytes == second.materialized_bytes == first.nbytes
    assert second.deduplicated is False
    assert ex.get_plan_state(second.uri)["dag"] == plan
    ex.close()


def test_shared_memory_descriptor_can_be_attached_repeatedly_from_fresh_processes():
    buf = SharedVectorBuffer(use_shared_memory=True)
    arr = np.arange(32, dtype=np.float32)
    buf.put("cross", arr)
    if buf.degraded:
        buf.close()
        pytest.skip("shared_memory unavailable in this environment")
    desc = buf.descriptor("cross")
    assert desc is not None
    code = """
import json, sys
from agent_runtime.state_exchange.shared_buffer import SharedArrayDescriptor, SharedVectorBuffer
d = SharedArrayDescriptor(
    name=sys.argv[1],
    shape=tuple(json.loads(sys.argv[2])),
    dtype=sys.argv[3],
    nbytes=int(sys.argv[4]),
)
with SharedVectorBuffer.attach_readonly(d) as view:
    print(f"{float(view.sum())}|{view.flags.writeable}")
"""
    argv = [
        sys.executable,
        "-c",
        code,
        desc.name,
        json.dumps(desc.shape),
        desc.dtype,
        str(desc.nbytes),
    ]
    # Regression: on Python 3.13 a standalone consumer with default track=True unlinked
    # the owner's segment at process exit, so the second attach failed with FileNotFoundError.
    first = subprocess.run(argv, text=True, capture_output=True, check=False)
    second = subprocess.run(argv, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout.strip() == second.stdout.strip() == f"{float(arr.sum())}|False"
    # Owner can still access the same payload after both consumers exit.
    assert np.array_equal(buf.get("cross"), arr)
    buf.close()


def test_shared_memory_descriptor_rejects_size_mismatch():
    buf = SharedVectorBuffer(use_shared_memory=True)
    arr = np.arange(8, dtype=np.float32)
    buf.put("cross", arr)
    if buf.degraded:
        buf.close()
        pytest.skip("shared_memory unavailable in this environment")
    desc = buf.descriptor("cross")
    assert desc is not None
    bad = type(desc)(desc.name, desc.shape, desc.dtype, desc.nbytes + 4)
    with pytest.raises(ValueError, match="size mismatch"):
        with SharedVectorBuffer.attach_readonly(bad):
            pass
    buf.close()


def test_int8_transport_counts_scale_and_preserves_direction():
    provider = MockEmbedding(dim=256, seed=7)
    ex = StateExchange(
        provider,
        task_id="t-int8",
        precision="int8",
        deduplicate=True,
    )
    plan = {"operation": "sum", "operands": ["alpha", "beta", "gamma"]}
    ref = ex.put_plan_state(plan)
    # 256 B int8 embedding + 4 B float32 scale + 12 B compact vector.
    assert ref.logical_nbytes == 256 * 4 + 3 * 4
    assert ref.nbytes == 256 + 4 + 3 * 4
    assert ref.dtype == "int8"
    assert ref.quant_scale is not None and ref.quant_scale > 0
    assert ex.get_encoded_vector(ref.uri).dtype == np.int8

    decoded = ex.get_plan_state(ref.uri)["embedding"]
    assert decoded.dtype == np.float32
    assert decoded.flags.writeable is False
    plan_text = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    original = provider.embed([plan_text])[0].astype(np.float32)
    cosine = float(np.dot(original, decoded) / (np.linalg.norm(original) * np.linalg.norm(decoded)))
    assert cosine > 0.9999
    ex.close()


def test_int8_shared_memory_exposes_scale_descriptor_when_available():
    ex = StateExchange(
        MockEmbedding(dim=64, seed=1),
        task_id="t-int8-shm",
        use_shared_memory=True,
        precision="int8",
        deduplicate=True,
    )
    ref = ex.put_plan_state({"operation": "sum", "operands": ["a", "b"]})
    descriptors = ex.get_shared_descriptors(ref.uri)
    if descriptors is None:
        ex.close()
        pytest.skip("shared_memory unavailable in this environment")
    assert set(descriptors) == {"embedding", "vector", "scale"}
    with SharedVectorBuffer.attach_readonly(descriptors["embedding"]) as q, \
         SharedVectorBuffer.attach_readonly(descriptors["scale"]) as scale:
        decoded = q.astype(np.float32) * float(scale[0])
        local = ex.get_plan_state(ref.uri)["embedding"]
        assert np.allclose(decoded, local)
    ex.close()


def test_invalidated_plan_is_not_reported_as_existing():
    from agent_runtime.providers.mock import MockEmbedding
    from agent_runtime.runtime.errors import ReferenceError_
    from agent_runtime.state_exchange.exchange import StateExchange

    exchange = StateExchange(MockEmbedding(dim=256), task_id="invalidated")
    try:
        ref = exchange.put_plan_state({"operation": "sum", "operands": ["a"]})
        assert exchange.exists(ref.uri) is True
        exchange.invalidate(ref.uri)
        assert exchange.exists(ref.uri) is False
        with pytest.raises(ReferenceError_):
            exchange.get_plan_state(ref.uri)
    finally:
        exchange.close()


def test_partial_plan_payload_loss_is_reported_as_reference_error():
    from agent_runtime.providers.mock import MockEmbedding
    from agent_runtime.runtime.errors import ReferenceError_
    from agent_runtime.state_exchange.exchange import StateExchange

    exchange = StateExchange(MockEmbedding(dim=256), task_id="partial")
    try:
        ref = exchange.put_plan_state({"operation": "sum", "operands": ["a"]})
        exchange._buffer.delete(f"{ref.uri}#vec")
        assert exchange.exists(ref.uri) is False
        with pytest.raises(ReferenceError_):
            exchange.get_plan_state(ref.uri)
    finally:
        exchange.close()


def test_shared_memory_descriptor_rejects_negative_shape_before_attach():
    from agent_runtime.state_exchange.shared_buffer import SharedArrayDescriptor

    bad = SharedArrayDescriptor(name="does-not-matter", shape=(-1,), dtype="float32", nbytes=-4)
    with pytest.raises(ValueError, match="non-negative"):
        with SharedVectorBuffer.attach_readonly(bad):
            pass


def test_shared_memory_cleanup_attempts_unlink_even_when_close_fails():
    class BrokenClose:
        def __init__(self):
            self.unlink_called = False

        def close(self):
            raise OSError("simulated close failure")

        def unlink(self):
            self.unlink_called = True

    fake = BrokenClose()
    buf = SharedVectorBuffer(use_shared_memory=False)
    buf._shm["fake"] = (fake, (1,), np.dtype("float32"))
    buf.delete("fake")
    assert fake.unlink_called is True
    assert "fake" not in buf._shm


def test_state_uri_uses_128_bit_content_hash_prefix():
    from agent_runtime.providers.mock import MockEmbedding
    from agent_runtime.state_exchange.exchange import StateExchange

    sx = StateExchange(MockEmbedding(dim=16, seed=1), task_id="hash-task")
    try:
        ref = sx.put_plan_state({"operation": "sum", "operands": [1, 2]})
        suffix = ref.uri.rsplit("/", 1)[-1]
        assert len(suffix) == 32
        assert ref.content_hash.startswith(suffix)
    finally:
        sx.close()


def test_shared_memory_descriptor_rejects_object_dtype_and_size_overflow():
    from agent_runtime.state_exchange.shared_buffer import SharedArrayDescriptor, SharedVectorBuffer

    with pytest.raises(ValueError, match="object dtype"):
        with SharedVectorBuffer.attach_readonly(
            SharedArrayDescriptor(name="unused", shape=(1,), dtype="object", nbytes=8)
        ):
            pass

    huge = 2**62
    with pytest.raises(ValueError, match="overflow"):
        with SharedVectorBuffer.attach_readonly(
            SharedArrayDescriptor(name="unused", shape=(huge, 8), dtype="float64", nbytes=0)
        ):
            pass


class _BadEmbedding:
    def __init__(self, matrix, dim=4):
        self.matrix = np.asarray(matrix)
        self.dim = dim
        self.name = "bad-embedding"

    def embed(self, texts):
        return self.matrix


def test_state_exchange_rejects_nonfinite_embedding_provider_output():
    ex = StateExchange(
        _BadEmbedding([[0.0, 1.0, float("nan"), 2.0]], dim=4),
        task_id="t-bad-emb",
    )
    with pytest.raises(ValueError, match="non-finite"):
        ex.embed_text("hello")
    ex.close()


def test_state_exchange_rejects_wrong_embedding_shape():
    ex = StateExchange(_BadEmbedding([[1.0, 2.0, 3.0]], dim=4), task_id="t-bad-shape")
    with pytest.raises(ValueError, match="returned dim 3, expected 4"):
        ex.embed_text("hello")
    ex.close()


def test_plan_state_rejects_non_json_and_nonfinite_values():
    ex = _exchange()
    with pytest.raises(ValueError, match="canonical JSON"):
        ex.put_plan_state({"operation": "sum", "operands": [object()]})
    with pytest.raises(ValueError, match="canonical JSON"):
        ex.put_plan_state({"operation": "sum", "operands": [float("inf")]})
    ex.close()


def test_plan_state_is_immutable_against_input_and_consumer_mutation():
    provider = MockEmbedding(dim=8, seed=7)
    exchange = StateExchange(provider, task_id="immutable", precision="float16")
    try:
        plan = {"operation": "sum", "operands": ["a", "b"], "nested": {"x": 1}}
        ref = exchange.put_plan_state(plan)
        plan["operands"].append("MUTATED")
        plan["nested"]["x"] = 99

        first = exchange.get_plan_state(ref.uri)
        assert first["dag"] == {"operation": "sum", "operands": ["a", "b"], "nested": {"x": 1}}

        first["dag"]["operands"].append("CONSUMER-MUTATION")
        first["dag"]["nested"]["x"] = -1
        second = exchange.get_plan_state(ref.uri)
        assert second["dag"] == {"operation": "sum", "operands": ["a", "b"], "nested": {"x": 1}}
        assert exchange.get_ref(ref.uri).content_hash == ref.content_hash
    finally:
        exchange.close()
