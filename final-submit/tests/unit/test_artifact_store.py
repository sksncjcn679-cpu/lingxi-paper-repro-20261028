"""Artifact 存储单元测试：内容寻址、引用计数、失效。"""

from __future__ import annotations

import pytest

from agent_runtime.artifact_store.store import ArtifactStore
from agent_runtime.runtime.errors import ReferenceError_


def test_put_get_roundtrip():
    store = ArtifactStore(task_id="t1")
    ref = store.put({"a": 1, "b": 2})
    assert ref.uri.startswith("artifact://task/t1/")
    assert store.get_json(ref.uri) == {"a": 1, "b": 2}


def test_content_addressing_dedup():
    store = ArtifactStore(task_id="t1")
    r1 = store.put({"x": 1})
    r2 = store.put({"x": 1})
    # 相同内容 -> 相同哈希与 URI（去重）
    assert r1.content_hash == r2.content_hash
    assert r1.uri == r2.uri
    assert len(store) == 1


def test_refcount_and_gc():
    store = ArtifactStore()
    ref = store.put({"x": 1})  # refcount=1
    store.decref(ref.uri)  # refcount=0
    removed = store.collect()
    assert removed == 1
    assert not store.exists(ref.uri)


def test_invalidate_triggers_reference_error():
    store = ArtifactStore()
    ref = store.put({"x": 1})
    store.invalidate(ref.uri)
    with pytest.raises(ReferenceError_):
        store.get_json(ref.uri)


def test_global_scope_uri():
    store = ArtifactStore(task_id="t1")
    ref = store.put("big content", scope="global")
    assert ref.uri.startswith("artifact://global/")


def test_persistent_store_survives_reopen(tmp_path):
    root = tmp_path / "artifacts"
    first = ArtifactStore(task_id="t1", root=root)
    ref = first.put({"answer": 42}, summary="answer")
    first.close()

    second = ArtifactStore(task_id="t1", root=root)
    assert second.get_json(ref.uri) == {"answer": 42}
    assert second.get_ref(ref.uri) == ref
    second.close()


def test_persistent_store_detects_blob_corruption(tmp_path):
    root = tmp_path / "artifacts"
    store = ArtifactStore(task_id="t1", root=root)
    ref = store.put("critical-data", media_type="text/plain")
    blob = root / "blobs" / ref.content_hash[:2] / ref.content_hash
    blob.write_bytes(b"tampered")
    with pytest.raises(ReferenceError_, match="校验"):
        store.get_bytes(ref.uri)
    store.close()


def test_persistent_refcount_gc_across_reopen(tmp_path):
    root = tmp_path / "artifacts"
    first = ArtifactStore(task_id="t1", root=root)
    ref = first.put({"x": 1})
    first.decref(ref.uri)
    first.close()

    second = ArtifactStore(task_id="t1", root=root)
    assert second.collect() == 1
    assert not second.exists(ref.uri)
    second.close()


def test_persistent_artifact_reopens_in_fresh_python_process(tmp_path):
    import json
    import subprocess
    import sys

    root = tmp_path / "artifacts-cross-process"
    with ArtifactStore(task_id="task-x", root=root) as store:
        ref = store.put({"payload": "cross-process", "n": 7}, summary="cross")

    code = r'''
import json, sys
from agent_runtime.artifact_store.store import ArtifactStore
store = ArtifactStore(task_id="task-x", root=sys.argv[1])
try:
    ref = store.get_ref(sys.argv[2])
    print(json.dumps({"exists": store.exists(sys.argv[2]), "data": store.get_json(sys.argv[2]), "hash": ref.content_hash if ref else None}))
finally:
    store.close()
'''
    raw = subprocess.check_output([sys.executable, "-c", code, str(root), ref.uri], text=True)
    payload = json.loads(raw)
    assert payload["exists"] is True
    assert payload["data"] == {"payload": "cross-process", "n": 7}
    assert payload["hash"] == ref.content_hash


def test_persistent_corrupted_blob_is_not_reported_valid_and_can_be_repaired(tmp_path):
    store = ArtifactStore(task_id="t-repair", root=tmp_path / "artifacts", verify_reads=True)
    try:
        content = {"answer": 42, "items": [1, 2, 3]}
        ref = store.put(content)
        blob = store._blob_path(ref.content_hash)
        blob.write_bytes(b"corrupted")

        assert store.exists(ref.uri) is False
        with pytest.raises(ReferenceError_):
            store.get_json(ref.uri)

        repaired = store.put(content)
        assert repaired.uri == ref.uri
        assert store.exists(ref.uri) is True
        assert store.get_json(ref.uri) == content
    finally:
        store.close()


def test_persistent_task_scoped_artifact_is_not_readable_by_other_task(tmp_path):
    root = tmp_path / "artifacts"
    owner = ArtifactStore(task_id="task-owner", root=root)
    other = ArtifactStore(task_id="task-other", root=root)
    try:
        private_ref = owner.put({"secret": "owner-only"}, scope="task")
        global_ref = owner.put({"shared": True}, scope="global")

        assert owner.get_json(private_ref.uri) == {"secret": "owner-only"}
        assert other.get_ref(private_ref.uri) is None
        assert other.exists(private_ref.uri) is False
        with pytest.raises(ReferenceError_):
            other.get_json(private_ref.uri)

        # Global scope remains intentionally readable across task-local Store instances.
        assert other.get_json(global_ref.uri) == {"shared": True}
    finally:
        owner.close()
        other.close()


def test_persistent_same_uri_upsert_is_atomic_across_independent_stores(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3

    root = tmp_path / "artifacts"
    # Bootstrap schema before concurrent opens so the test isolates the ref UPSERT itself.
    ArtifactStore(task_id="task-race", root=root).close()

    def publish(_):
        store = ArtifactStore(task_id="task-race", root=root)
        try:
            return store.put({"same": [1, 2, 3]}, scope="task").uri
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        uris = list(pool.map(publish, range(8)))
    assert len(set(uris)) == 1

    conn = sqlite3.connect(root / "artifacts.sqlite3")
    try:
        row = conn.execute("SELECT refcount FROM refs WHERE uri=?", (uris[0],)).fetchone()
        assert row is not None
        assert row[0] == 8
    finally:
        conn.close()


def test_content_addressed_artifact_uri_uses_128_bit_hash_prefix():
    store = ArtifactStore(task_id="t-hash")
    ref = store.put({"x": 1})
    assert len(ref.uri.rsplit("/", 1)[-1]) == 32
    assert ref.content_hash.startswith(ref.uri.rsplit("/", 1)[-1])


def test_json_artifact_rejects_nonfinite_and_non_json_objects():
    store = ArtifactStore(task_id="t-json-strict")
    with pytest.raises(ValueError, match="standard JSON"):
        store.put({"value": float("nan")})
    with pytest.raises(ValueError, match="standard JSON"):
        store.put({"value": object()})


def test_json_artifact_has_canonical_content_identity():
    store = ArtifactStore(task_id="t-canonical")
    first = store.put({"b": 2, "a": [1, 2]})
    second = store.put({"a": [1, 2], "b": 2})
    assert first.uri == second.uri
    assert store.get_bytes(first.uri) == b'{"a":[1,2],"b":2}'


def test_persistent_artifact_corrupt_hash_cannot_escape_blob_root(tmp_path):
    from agent_runtime.runtime.errors import ReferenceError_

    root = tmp_path / "store"
    outside = tmp_path / "outside.txt"
    outside.write_text("DO-NOT-READ-OR-DELETE", encoding="utf-8")
    store = ArtifactStore(task_id="t", root=root)
    try:
        ref = store.put({"safe": True})
        # Simulate corrupt/tampered SQLite metadata; this value would traverse if blindly
        # joined as a filesystem path.
        bad = "../" * 20 + "outside.txt"
        store._conn.execute("UPDATE refs SET content_hash=? WHERE uri=?", (bad, ref.uri))
        store._conn.commit()
        with pytest.raises(ReferenceError_, match="元数据损坏"):
            store.get_bytes(ref.uri)
        assert store.exists(ref.uri) is False
        assert outside.read_text(encoding="utf-8") == "DO-NOT-READ-OR-DELETE"
    finally:
        store.close()


def test_fable7_physical_encoding_roundtrip_escape_and_bomb_guard(tmp_path):
    import zlib

    import pytest

    from agent_runtime.artifact_store.store import ArtifactStore, decode_physical, encode_physical

    for payload in (b"", b"small", b"ARZ1 looks like magic" * 20, b"ARR1raw", bytes(range(256)) * 4, b"a" * 5000):
        assert decode_physical(encode_physical(payload), len(payload)) == payload
    bomb = b"ARZ1" + zlib.compress(b"\0" * 1_000_000)
    with pytest.raises(ValueError):
        decode_physical(bomb, 100)
    store = ArtifactStore(task_id="t", root=tmp_path)
    ref = store.put({"log": "line\n" * 500})
    assert store.materialized_bytes < ref.size
    assert store.exists(ref.uri) and store.get_json(ref.uri) == {"log": "line\n" * 500}
    # A tampered persistent blob must be rejected, never silently accepted.
    blob = next((tmp_path / "blobs").rglob(ref.content_hash))
    blob.write_bytes(b"ARZ1" + zlib.compress(b"{}"))
    assert not store.exists(ref.uri)
