"""v0.8.0 Fusion Edition regression tests."""
from __future__ import annotations

import socket

from agent_runtime.config import SandboxConfig, load_config
from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import ExperimentConfig, run_once


def _ss(**kwargs):
    base = dict(
        structured_wire_codec="session_msgpack",
        control_transport="uds" if hasattr(socket, "AF_UNIX") else "inproc",
        state_shared_memory=hasattr(socket, "AF_UNIX"),
        state_precision="adaptive",
    )
    base.update(kwargs)
    return ExperimentConfig("SS", "Fusion SS", "structured", True, False, True, **base)


def test_warm_start_default_and_env(monkeypatch):
    assert SandboxConfig().warm_start is True
    monkeypatch.setenv("AGENT_SANDBOX_WARM", "false")
    assert load_config().sandbox.warm_start is False


def test_projection_artifact_resolves_exact_subset_and_counts_physical_bytes():
    from agent_runtime.artifact_store.store import ArtifactStore
    store = ArtifactStore(task_id="t")
    base = store.put({"a":"x"*500,"b":"y"*500,"c":1})
    before = store.materialized_bytes
    view = store.put_projection(base.uri,["a","c"])
    assert store.get_json(view.uri) == {"a":"x"*500,"c":1}
    assert store.materialized_bytes > before
    assert view.size < 200


def test_protocol13_verified_uds_and_full_plane_artifact_accounting():
    row = run_once(_ss(), _self_contained_tasks, 42)
    assert row["task_success"] == 1.0
    if hasattr(socket, "AF_UNIX"):
        assert row["ipc_frames"] == row["message_count"] == row["ipc_verified"]
        assert row["ipc_socket_calls"] >= row["ipc_frames"] + 1
        assert row["state_shm_transfers"] > 0
    assert row["artifact_bytes"] > 0
    assert row["state_int8_transfers"] + row.get("state_int4_transfers", 0) > 0  # fable9: certified int4


def test_warm_e2e_has_identical_wire_and_success():
    cold = run_once(_ss(sandbox_warm_start=False), _self_contained_tasks, 42)
    warm = run_once(_ss(sandbox_warm_start=True), _self_contained_tasks, 42)
    assert cold["task_success"] == warm["task_success"] == 1.0
    assert cold["wire_bytes"] == warm["wire_bytes"]
    assert cold["artifact_bytes"] == warm["artifact_bytes"]


def test_scale_benchmark_logic_with_stub(monkeypatch):
    import agent_runtime.evaluation.scale_benchmark as mod
    def fake(cfg,_factory,seed):
        base={"task_success":1.0,"latency_sec":0.04,"wire_bytes":4000.0,"text_tokens":1000.0,
              "ipc_frames":36.0,"ipc_framed_bytes":4144.0,"ipc_ack_bytes":1332.0,"ipc_roundtrip_sec":0.01,
              "state_bytes":0.0,"artifact_bytes":100.0,"message_count":36.0,"tool_calls":8.0,"ipc_verified":36.0,"ipc_socket_calls":144.0}
        if cfg.id=="S": base.update(wire_bytes=2800.0,text_tokens=850.0,ipc_framed_bytes=2944.0)
        if cfg.id=="SS": base.update(wire_bytes=500.0,text_tokens=200.0,ipc_framed_bytes=644.0,state_bytes=800.0,artifact_bytes=500.0)
        return base
    monkeypatch.setattr(mod,"run_once",fake)
    p=mod.run_scale_benchmark(repeat=2)
    assert set(p["cells"]) == {"small","medium","large"}
    assert p["cells"]["large"]["SS"]["saving_vs_text"]["full_physical"] > 0


def test_scale_helpers_render_writer_and_validation(tmp_path):
    import pytest
    from agent_runtime.evaluation import scale_benchmark as mod
    with pytest.raises(ValueError):
        mod.run_scale_benchmark(repeat=0)
    made = mod._factory(2)()
    assert len(made) == 4
    # Build a compact synthetic payload to cover renderer/writer without live task runs.
    stat = {"mean": 1.0, "std": 0.0}
    lat = {"n":2,"mean":0.01,"median":0.01,"p95":0.011,"std":0.001,"ci95_low":0.009,"ci95_high":0.011}
    cells={}
    for scale in mod.SCALES:
        cells[scale]={}
        for cid in ("T","S","SS"):
            det={k:dict(stat) for k in ("wire_bytes","text_tokens","control_physical_bytes","state_bytes","artifact_bytes","full_transport_bytes","message_count","tool_calls","ipc_verified","ipc_socket_calls")}
            cells[scale][cid]={"success_rate":1.0,"deterministic":det,"mean_task_latency_sec":lat,"ipc_rtt_sec":lat}
            if cid != "T": cells[scale][cid]["saving_vs_text"]={"tokens":0.1,"codec_payload":0.2,"verified_control":0.2,"full_physical":0.3}
    payload={"suite":"scale_benchmark","generated_at_utc":"x","env":{"source_tree_sha256":"abc"},"repeat":2,"scales":mod.SCALES,"cells":cells,"methodology":{}}
    assert "Small / Medium / Large" in mod.render_scale_benchmark(payload)
    out=mod.write_scale_benchmark(payload,tmp_path)
    assert (out/"summary.json").exists() and (out/"MANIFEST.sha256").exists()


def test_verified_router_service_lifecycle_and_closed_guard():
    import socket as _socket
    import pytest
    if not hasattr(_socket,"AF_UNIX"):
        pytest.skip("AF_UNIX unavailable")
    from agent_runtime.scheduler.control_transport import UDSControlTransport, shutdown_router_service
    tr=UDSControlTransport(mode="text",codec="session_msgpack")
    with tr:
        elapsed,outb,ackb,calls,verified=tr.transmit(b"hello")
        assert elapsed >= 0 and outb == 9 and ackb == 37 and calls >= 2 and verified
    tr.close()
    with pytest.raises(RuntimeError): tr.transmit(b"again")
    shutdown_router_service()
    # Service must recreate cleanly after explicit shutdown.
    tr2=UDSControlTransport(mode="text",codec="session_msgpack")
    assert tr2.transmit(b"recreated")[-1] is True
    tr2.close(); shutdown_router_service()


def test_router_warmup_is_background_and_deduplicated(monkeypatch):
    import threading
    import agent_runtime.scheduler.control_transport as ct

    started = threading.Event()
    release = threading.Event()
    calls = []

    def wait_for_release():
        calls.append(1)
        started.set()
        release.wait(1)

    monkeypatch.setattr(ct, "_SERVICE", None)
    monkeypatch.setattr(ct, "_SERVICE_WARMER", None)
    monkeypatch.setattr(ct, "_get_service", wait_for_release)
    monkeypatch.setattr(ct.socket, "AF_UNIX", object(), raising=False)
    monkeypatch.delenv("AGENT_ROUTER_SOCKET", raising=False)
    ct.warm_router_service()
    assert started.wait(1)
    ct.warm_router_service()
    assert len(calls) == 1
    release.set()
    ct._SERVICE_WARMER.join(1)


def test_router_warmup_skips_external_socket(monkeypatch):
    import agent_runtime.scheduler.control_transport as ct

    monkeypatch.setenv("AGENT_ROUTER_SOCKET", "/tmp/external-router.sock")
    monkeypatch.setattr(ct, "_SERVICE_WARMER", None)
    ct.warm_router_service()
    assert ct._SERVICE_WARMER is None
