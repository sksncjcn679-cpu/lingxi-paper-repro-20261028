"""fable8 reliability tests: verified-resolution cache, decoded-blob cache, routine doc cache."""

from __future__ import annotations

import pytest


def test_verified_resolution_cache_returns_private_copies_and_invalidates_on_replace():
    from agent_runtime.state_exchange.delta import StateIntegrityError, VersionedStateLedger, _Entry

    led = VersionedStateLedger(namespace="t")
    base = led.put_snapshot({"stage": "plan", "items": [1, 2]})
    cur = base
    for i in range(4):
        cur = led.update(cur.uri, {"stage": f"s{i}", "items": [1, 2, i]})
    first = led.resolve(cur.uri)
    first["items"].append("poison")  # caller mutation must not poison the cache
    second = led.resolve(cur.uri)
    assert second == {"stage": "s3", "items": [1, 2, 3]}
    assert led.resolve_cache_hits >= 1
    # Replacing an entry object (e.g. corruption / reload) invalidates its cached value.
    entry = led._entries[cur.uri]
    led._entries[cur.uri] = _Entry(ref=entry.ref, ops=())
    with pytest.raises(StateIntegrityError):
        led.resolve(cur.uri)


def test_decoded_blob_cache_is_correct_and_bounded():
    from agent_runtime.artifact_store.store import ArtifactStore

    store = ArtifactStore(task_id="t")
    refs = [store.put({"i": i, "log": "line\n" * 300}) for i in range(80)]
    for r in refs:
        assert store.get_json(r.uri)["i"] == refs.index(r)
    assert len(store.__dict__["_decoded"]) <= 64
    again = store.get_bytes(refs[-1].uri)
    assert again is store.get_bytes(refs[-1].uri)  # immutable bytes served from cache


def test_routine_document_is_computed_once_and_stable():
    from agent_runtime.protocol.routine import Routine

    r = Routine.learn([{"st": 0, "s": 0, "t": 1, "a": 3, "c": 0}, {"st": 0, "s": 1, "t": 0, "a": 3, "c": 0, "sta": 1}])
    assert r.to_json() is r.to_json()
    assert r.digest == Routine.from_json(r.to_json()).digest


@pytest.mark.parametrize("prep", ["1", "0"])
def test_prefork_stdio_preparation_preserves_io_semantics(monkeypatch, prep):
    from agent_runtime.config import SandboxConfig
    from agent_runtime.sandbox.executor import CodeActExecutor

    monkeypatch.setenv("AGENT_SANDBOX_PRECOMPILE", prep)
    ex = CodeActExecutor(SandboxConfig(warm_start=True, timeout_sec=5))
    for i in range(3):  # repeated forks reuse the prepared wrappers
        r = ex.run("import sys\nline = sys.stdin.readline().strip()\nprint('读到', line)\n"
                   "print('err\\udcff', file=sys.stderr)\n", stdin=f"输入-{i}\n")
        assert r.exit_code == 0
        assert r.stdout.strip() == f"读到 输入-{i}"
        assert "err" in r.stderr and "\\udcff" in r.stderr


def test_fast_resource_sampler_matches_psutil_rss():
    psutil = pytest.importorskip("psutil")
    from agent_runtime.observability.metrics import _fast_sample

    first = _fast_sample()
    if first is None:
        pytest.skip("/proc unavailable")
    rss, pct = _fast_sample()
    ref = psutil.Process().memory_info().rss
    assert abs(rss - ref) <= 256 * 1024  # same statm field; allow for allocation between calls
    assert pct >= 0.0


def _mem():
    from agent_runtime.config import load_config
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.providers.factory import build_embedding

    return MemoryStore(embedding=build_embedding(load_config(load_env=False)))


def test_plan_template_requires_verified_same_signature_same_topic():
    from agent_runtime.memory.models import MemoryRecord

    store = _mem()
    fields = set(MemoryRecord.model_fields)

    def add(topic, op, operands, success):
        kw = dict(task_topic=topic, summary=f"{op} plan {topic} {operands} {success}", source_agent="reviewer",
                  payload={"operation": op, "operands": operands, "answer": "x"}, success_feedback=success)
        rec, _ = store.write(MemoryRecord(**{k: v for k, v in kw.items() if k in fields}))
        return rec.memory_id

    ok = add("svc", "diagnose_service", ["a", "b"], True)
    add("svc", "diagnose_service", ["a", "b", "c"], True)
    add("other", "diagnose_service", ["a", "b"], True)
    add("svc", "assess_migration", ["a", "b"], True)
    add("svc", "diagnose_service", ["b", "a"], False)
    hit = store.find_plan_template(operation="diagnose_service", operands=["b", "a"], topic="svc")
    assert hit is not None and hit.memory_id == ok
    assert store.find_plan_template(operation="diagnose_service", operands=["a"], topic="svc") is None
    assert store.find_plan_template(operation="diagnose_service", operands=["a", "b"], topic="nope") is None


def test_plan_cache_changes_cost_not_outcome(monkeypatch):
    from agent_runtime.evaluation import contest_workload as cw

    on = cw._memory_phase(True)
    monkeypatch.setenv("AGENT_PLAN_CACHE", "0")
    off = cw._memory_phase(True)
    assert on["success_count"] == off["success_count"] == on["task_count"]
    assert on["aggregate"]["plan_cache_hits"] >= 1 and off["aggregate"]["plan_cache_hits"] == 0
    assert on["aggregate"]["llm_calls"] < off["aggregate"]["llm_calls"]
    assert on["aggregate"]["memory_harmful"] == off["aggregate"]["memory_harmful"] == 0
    # identical answers task by task
    ans_on = [r.get("answer") for r in on.get("tasks", on.get("rows", []))]
    ans_off = [r.get("answer") for r in off.get("tasks", off.get("rows", []))]
    assert ans_on == ans_off
