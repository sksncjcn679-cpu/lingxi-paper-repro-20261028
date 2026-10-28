"""opus2: sealed memfd + SCM_RIGHTS handoff."""

from __future__ import annotations

import hashlib
import os

import pytest

from agent_runtime.state_exchange.fd_passing import (
    SealedSegment,
    VerifierProcess,
    choose_handoff,
    fd_passing_supported,
    run_fd_passing_benchmark,
    verify_sealed_fd,
)

pytestmark = pytest.mark.skipif(not fd_passing_supported(), reason="Linux memfd/SCM_RIGHTS required")


def test_sealed_segment_is_immutable_and_verifiable():
    seg = SealedSegment.create(b"state-bytes" * 100)
    try:
        with pytest.raises(PermissionError):
            os.pwrite(seg.fd, b"X", 0)
        with pytest.raises(OSError):
            os.ftruncate(seg.fd, 1)
        assert verify_sealed_fd(seg.fd, seg.size, seg.sha256)["ok"]
        assert not verify_sealed_fd(seg.fd, seg.size, "0" * 64)["ok"]
    finally:
        seg.close()


def test_unsealed_memfd_is_rejected():
    fd = os.memfd_create("unsealed", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.write(fd, b"mutable")
        assert verify_sealed_fd(fd, 7)["ok"] is False
    finally:
        os.close(fd)


def test_cross_process_fd_and_copy_handoffs_verify():
    v = VerifierProcess()
    try:
        payload = os.urandom(200_000)
        seg = SealedSegment.create(payload)
        ok_fd, sent_fd = v.handoff_fd(seg, verify=True)
        seg.close()
        ok_cp, sent_cp = v.handoff_copy(payload, hashlib.sha256(payload).hexdigest(), verify=True)
        assert ok_fd and ok_cp
        assert sent_fd < 256 < len(payload) < sent_cp
        bad = SealedSegment.create(b"x" * 10)
        bad.sha256 = "0" * 64
        ok_bad, _ = v.handoff_fd(bad, verify=True)
        bad.close()
        assert ok_bad is False
    finally:
        v.close()


def test_state_exchange_main_path_uses_uds_fd_handoff():
    """A large state created by StateExchange must exercise the Runtime callback."""
    from agent_runtime.providers.mock import MockEmbedding
    from agent_runtime.scheduler.control_transport import UDSStateAttachTransport
    from agent_runtime.state_exchange.exchange import StateExchange

    transport = UDSStateAttachTransport()
    calls: list[tuple[bool, int, int]] = []

    def attach(payload: bytes):
        ok, socket_bytes = transport.attach_bytes(payload)
        calls.append((ok, len(payload), socket_bytes))
        return ok, socket_bytes

    exchange = StateExchange(
        MockEmbedding(dim=300_000),
        task_id="fd-main-path",
        precision="float32",
        on_large_state=attach,
        fd_handoff_min_bytes=1 << 20,
    )
    try:
        ref = exchange.embed_text("large runtime state")
        assert ref.nbytes >= 1 << 20
        assert calls and calls[0][0] is True
        assert calls[0][1] == ref.shape[0] * 4
        assert calls[0][2] < 512
    finally:
        exchange.close()
        transport.close()


def test_state_exchange_fd_callback_failure_is_propagated():
    from agent_runtime.providers.mock import MockEmbedding
    from agent_runtime.runtime.errors import ReferenceError_
    from agent_runtime.state_exchange.exchange import StateExchange

    def reject(_payload: bytes):
        return False, 0

    exchange = StateExchange(
        MockEmbedding(dim=300_000),
        task_id="fd-rejected",
        precision="float32",
        on_large_state=reject,
        fd_handoff_min_bytes=1 << 20,
    )
    try:
        with pytest.raises(ReferenceError_, match="sealed state attachment rejected"):
            exchange.embed_text("large runtime state")
    finally:
        exchange.close()


def test_no_fd_leak_after_handoffs():
    before = len(os.listdir("/proc/self/fd"))
    v = VerifierProcess()
    for _ in range(20):
        seg = SealedSegment.create(b"y" * 1000)
        v.handoff_fd(seg, verify=False)
        seg.close()
    v.close()
    assert len(os.listdir("/proc/self/fd")) <= before + 1


def test_cost_aware_choice(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_FD_HANDOFF_MIN_BYTES", raising=False)
    assert choose_handoff(460) == "copy"
    # opus5 cost model: a single consumer never pays for materialising a memfd copy.
    monkeypatch.delenv("AGENT_FD_MIN_FANOUT_COPYIN", raising=False)
    monkeypatch.delenv("AGENT_FD_MIN_FANOUT_INPLACE", raising=False)
    # Isolate from any cached host calibration so this test pins the *defaults*.
    from agent_runtime.state_exchange import fd_passing as fp

    monkeypatch.setenv("AGENT_FD_CALIBRATION", str(tmp_path / "absent.json"))
    monkeypatch.setattr(fp, "_CALIBRATION", None)
    assert fp.policy_thresholds()["source"] == "default"
    assert choose_handoff(4 << 20) == "copy"
    assert choose_handoff(4 << 20, fanout=4) == "copy"          # copy-in needs k >= 8
    assert choose_handoff(4 << 20, fanout=8) == "fd"
    assert choose_handoff(4 << 20, in_place=True) == "copy"     # in-place needs k >= 4
    assert choose_handoff(4 << 20, fanout=4, in_place=True) == "fd"
    assert choose_handoff(460, fanout=64) == "copy"             # below the size threshold
    monkeypatch.setenv("AGENT_FD_HANDOFF_MIN_BYTES", "100")
    monkeypatch.setenv("AGENT_FD_MIN_FANOUT_COPYIN", "2")
    monkeypatch.setattr(fp, "_CALIBRATION", None)
    assert choose_handoff(460, fanout=2) == "fd"


def test_benchmark_reports_byte_reduction_and_crossover():
    r = run_fd_passing_benchmark(sizes=(4096, 2 << 20), repeat=5)
    big = r["results"][str(2 << 20)]
    assert r["sealed_write_rejected"] is True
    assert big["socket_byte_reduction"] > 0.99
    assert all(big[k]["all_ok"] for k in ("copy", "fd", "copy_verify", "fd_verify"))


def _patch_mmap(monkeypatch, *, deny_shared=True, deny_private=False):
    import mmap as _mmap

    import agent_runtime.state_exchange.fd_passing as fp

    real = _mmap.mmap

    def fake(fd, length, *args, flags=_mmap.MAP_SHARED, **kw):
        if (flags == _mmap.MAP_SHARED and deny_shared) or (flags == _mmap.MAP_PRIVATE and deny_private):
            raise PermissionError(1, "Operation not permitted")
        return real(fd, length, *args, flags=flags, **kw)

    monkeypatch.setattr(fp.mmap, "mmap", fake)


def test_openeuler_66_semantics_shared_mapping_denied(monkeypatch):
    """Linux < 6.7: MAP_SHARED of an F_SEAL_WRITE memfd -> EPERM. MAP_PRIVATE must be used."""
    _patch_mmap(monkeypatch, deny_shared=True)
    seg = SealedSegment.create(b"z" * 5000)
    try:
        r = verify_sealed_fd(seg.fd, seg.size, seg.sha256)
        assert r["ok"] and r["read_mode"] == "mmap_private_ro"
    finally:
        seg.close()


def test_all_mappings_denied_falls_back_to_pread(monkeypatch):
    _patch_mmap(monkeypatch, deny_shared=True, deny_private=True)
    seg = SealedSegment.create(os.urandom(3 << 20))
    try:
        r = verify_sealed_fd(seg.fd, seg.size, seg.sha256)
        assert r["ok"] and r["read_mode"] == "pread"
        assert not verify_sealed_fd(seg.fd, seg.size, "0" * 64)["ok"]
    finally:
        seg.close()


def test_probe_reports_kernel_capabilities_and_policy_mix():
    from agent_runtime.state_exchange.fd_passing import probe_fd_capabilities

    p = probe_fd_capabilities(refresh=True)
    assert p["supported"] and p["consumer_read_mode"] in {"mmap_shared_ro", "mmap_private_ro", "pread"}
    r = run_fd_passing_benchmark(sizes=(512, 2 << 20), repeat=3)
    pm = r["policy_mix"]
    assert {"always_copy", "always_fd", "adaptive", "adaptive_inplace"} <= set(pm)
    from agent_runtime.state_exchange.fd_passing import choose_handoff

    big = r["fanout_study"]["size"]
    assert pm["adaptive"]["large_decision"] == choose_handoff(big, fanout=4)
    assert pm["adaptive_inplace"]["large_decision"] == choose_handoff(big, fanout=4, in_place=True)
    assert pm["adaptive_inplace"]["socket_bytes"] < pm["always_copy"]["socket_bytes"]
    assert set(r["fanout_study"]["rows"]) == {"1", "2", "4", "8"}


def test_in_place_build_is_sealed_and_matches_content():
    data = os.urandom(300_000)
    seg = SealedSegment.build(len(data), lambda view: view.__setitem__(slice(None), data))
    try:
        assert seg.sha256 == hashlib.sha256(data).hexdigest()
        assert os.pread(seg.fd, len(data), 0) == data
        with pytest.raises(PermissionError):
            os.pwrite(seg.fd, b"X", 0)
        assert verify_sealed_fd(seg.fd, seg.size, seg.sha256)["ok"]
    finally:
        seg.close()


def test_in_place_build_failure_closes_fd():
    before = len(os.listdir("/proc/self/fd"))

    def boom(view):
        raise RuntimeError("producer failed")

    with pytest.raises(RuntimeError):
        SealedSegment.build(4096, boom)
    assert len(os.listdir("/proc/self/fd")) <= before


def test_host_calibration_drives_policy(monkeypatch, tmp_path):
    from agent_runtime.state_exchange import fd_passing as fp

    monkeypatch.setenv("AGENT_FD_CALIBRATION", str(tmp_path / "cal.json"))
    monkeypatch.delenv("AGENT_FD_MIN_FANOUT_COPYIN", raising=False)
    monkeypatch.delenv("AGENT_FD_MIN_FANOUT_INPLACE", raising=False)
    monkeypatch.setattr(fp, "_CALIBRATION", None)
    cal = fp.calibrate_fd_policy(size=1 << 20, reps=3)
    assert cal["supported"] and cal["k_copy_in"] >= 1 and cal["k_in_place"] >= 1
    assert (tmp_path / "cal.json").exists()
    monkeypatch.setattr(fp, "_CALIBRATION", None)
    th = fp.policy_thresholds()
    assert th["source"] == "calibration" and th["k_copy_in"] == cal["k_copy_in"]
    # An explicit environment override always wins over the cached calibration.
    monkeypatch.setenv("AGENT_FD_MIN_FANOUT_COPYIN", "99")
    monkeypatch.setattr(fp, "_CALIBRATION", None)
    assert fp.policy_thresholds()["source"] == "env"
    assert fp.choose_handoff(4 << 20, fanout=50) == "copy"


def test_calibration_from_another_kernel_is_ignored(monkeypatch, tmp_path):
    import json as _json

    from agent_runtime.state_exchange import fd_passing as fp

    path = tmp_path / "cal.json"
    path.write_text(_json.dumps({"version": fp.CALIBRATION_VERSION, "kernel": "0.0.0-not-this-host",
                                 "machine": "x86_64", "k_copy_in": 1, "k_in_place": 1}))
    monkeypatch.setenv("AGENT_FD_CALIBRATION", str(path))
    monkeypatch.setattr(fp, "_CALIBRATION", None)
    assert fp.load_calibration(refresh=True) is None
    monkeypatch.delenv("AGENT_FD_MIN_FANOUT_COPYIN", raising=False)
    monkeypatch.delenv("AGENT_FD_MIN_FANOUT_INPLACE", raising=False)
    assert fp.policy_thresholds()["source"] == "default"
