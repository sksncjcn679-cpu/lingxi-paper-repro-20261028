"""fable9: Plasma-style shared-memory arena for small immutable state objects."""

from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pytest

from agent_runtime.state_exchange.shared_buffer import SharedArrayDescriptor, SharedVectorBuffer


def _shm_ok():
    try:
        b = SharedVectorBuffer(use_shared_memory=True)
        b.put("x", np.zeros(4, dtype=np.int8))
        b.close()
        return not b.degraded
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _shm_ok(), reason="POSIX shared memory unavailable")


def test_many_small_objects_share_one_segment_and_stay_isolated():
    buf = SharedVectorBuffer(use_shared_memory=True, arena=True)
    arrays = {f"k{i}": (np.arange(i + 1, dtype=np.float32) * (i + 1)) for i in range(200)}
    for k, a in arrays.items():
        buf.put(k, a)
    assert buf.segments_created <= 2
    for k, a in arrays.items():
        v = buf.get_view(k)
        assert np.array_equal(v, a) and not v.flags.writeable
        d = buf.descriptor(k)
        assert d.offset % 64 == 0
    with pytest.raises(KeyError):
        buf.put("k3", arrays["k3"])
    buf.close()


def test_large_objects_keep_dedicated_segments_and_legacy_mode_unchanged():
    buf = SharedVectorBuffer(use_shared_memory=True, arena=True)
    big = np.ones(SharedVectorBuffer.ARENA_CHUNK, dtype=np.int8)
    buf.put("big", big)
    assert buf.descriptor("big").offset == 0 and np.array_equal(buf.get_view("big"), big)
    buf.close()
    legacy = SharedVectorBuffer(use_shared_memory=True, arena=False)
    for i in range(5):
        legacy.put(f"k{i}", np.zeros(8, dtype=np.int8))
    assert legacy.segments_created == 5
    legacy.close()


def test_chunk_is_released_when_its_last_object_is_deleted():
    buf = SharedVectorBuffer(use_shared_memory=True, arena=True)
    n = SharedVectorBuffer.ARENA_CHUNK // 4
    buf.put("a", np.zeros(n, dtype=np.int8))
    buf.put("b", np.zeros(n, dtype=np.int8))
    buf.put("c", np.zeros(n, dtype=np.int8))
    buf.put("d", np.zeros(n, dtype=np.int8))  # exactly fills the first chunk
    assert buf.segments_created == 1
    buf.put("e", np.ones(n, dtype=np.int8))  # forces a second chunk
    assert buf.segments_created == 2
    for k in "abcd":
        buf.delete(k)
    assert buf._chunks[0][0] is None  # first chunk unlinked, second (current) kept
    assert np.array_equal(buf.get_view("e"), np.ones(n, dtype=np.int8))
    buf.close()


def test_offset_descriptor_attaches_in_a_fresh_process():
    buf = SharedVectorBuffer(use_shared_memory=True, arena=True)
    buf.put("first", np.arange(10, dtype=np.int8))
    payload = np.linspace(-1, 1, 33, dtype=np.float32)
    buf.put("second", payload)
    d = buf.descriptor("second")
    assert d.offset > 0
    code = (
        "import json,sys,numpy as np\n"
        "from agent_runtime.state_exchange.shared_buffer import SharedArrayDescriptor as D, SharedVectorBuffer as B\n"
        "d=D(**json.loads(sys.argv[1])); d=D(d.name, tuple(d.shape), d.dtype, d.nbytes, d.offset)\n"
        "with B.attach_readonly(d) as v: print(json.dumps([float(x) for x in v]))\n"
    )
    out = subprocess.run([sys.executable, "-c", code, json.dumps(d.__dict__)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert np.allclose(json.loads(out.stdout), payload)
    buf.close()


def test_attach_rejects_bad_offsets():
    buf = SharedVectorBuffer(use_shared_memory=True, arena=True)
    buf.put("x", np.zeros(16, dtype=np.float32))
    d = buf.descriptor("x")
    for bad in (SharedArrayDescriptor(d.name, d.shape, d.dtype, d.nbytes, -64),
                SharedArrayDescriptor(d.name, d.shape, d.dtype, d.nbytes, 2),
                SharedArrayDescriptor(d.name, d.shape, d.dtype, d.nbytes, SharedVectorBuffer.ARENA_CHUNK)):
        with pytest.raises(ValueError):
            with SharedVectorBuffer.attach_readonly(bad):
                pass
    buf.close()
