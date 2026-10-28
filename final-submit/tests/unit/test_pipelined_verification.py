"""fable7: pipelined control-plane verification with a fail-closed commit barrier."""

from __future__ import annotations

import socket

import pytest

pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")


def _cfg():
    from agent_runtime.evaluation.runner import ExperimentConfig

    return ExperimentConfig(
        "SS", "x", "structured", True, False, True, structured_wire_codec="session_msgpack",
        control_transport="uds", state_shared_memory=True, state_precision="adaptive",
    )


def test_every_pipelined_frame_is_verified_before_commit():
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.evaluation.runner import run_once

    row = run_once(_cfg(), _self_contained_tasks, 42)
    assert row["task_success"] == 1.0
    assert row["ipc_frames"] == row["message_count"] == row["ipc_verified"] == row["ipc_pipelined_frames"]
    assert row["ipc_socket_calls"] < 2 * row["ipc_frames"]  # ACKs drained in batches


def test_synchronous_mode_is_available_for_ablation(monkeypatch):
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.evaluation.runner import run_once

    monkeypatch.setenv("AGENT_PIPELINED_VERIFY", "0")
    row = run_once(_cfg(), _self_contained_tasks, 42)
    assert row["ipc_pipelined_frames"] == 0
    assert row["ipc_verified"] == row["ipc_frames"]
    assert row["ipc_socket_calls"] >= 2 * row["ipc_frames"]


def test_digest_mismatch_fails_the_task_at_the_barrier(monkeypatch):
    import asyncio

    from agent_runtime.config import load_config
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator
    from agent_runtime.scheduler import control_transport as ct

    original = ct.UDSControlTransport.send_pipelined

    def corrupt(self, payload):
        out = original(self, payload)
        if self.ack_chain:
            self._chain.update(b"tamper")  # client chain diverges from the router's
        else:
            pending = self.__dict__["_pending_acks"]
            expected, sent_at = pending[-1]
            pending[-1] = (bytes(32), sent_at)  # the router's (correct) digest will not match
        return out

    monkeypatch.setattr(ct.UDSControlTransport, "send_pipelined", corrupt)
    cfg = load_config(load_env=False, control_transport="uds", structured_wire_codec="session_msgpack")
    task = _self_contained_tasks()[0]
    ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True, use_sandbox=True)
    result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
    assert result.success is False
    assert result.error["type"] == "control_plane_verification"


@pytest.mark.parametrize("chain", ["1", "0"])
def test_mismatch_detected_in_both_ack_modes(monkeypatch, chain):
    monkeypatch.setenv("AGENT_ACK_CHAIN", chain)
    test_digest_mismatch_fails_the_task_at_the_barrier(monkeypatch)


def test_chain_ack_counts_and_router_side_failure_is_reported():
    from agent_runtime.scheduler.control_transport import UDSControlTransport

    tr = UDSControlTransport(mode="structured", codec="session_msgpack", ack_chain=True)
    try:
        info = tr.barrier()
        assert info["verified"] == 0
        # A frame the router cannot decode must surface at the barrier, not be lost.
        tr.send_pipelined(b"\x93not-a-session-frame")
        with pytest.raises(Exception, match="router rejected"):
            tr.barrier()
    finally:
        tr.close()
    tr2 = UDSControlTransport(mode="text", codec="session_msgpack", ack_chain=True)
    try:
        for i in range(5):
            tr2.send_pipelined(f"hello {i}".encode())
        info = tr2.barrier()
        assert info["verified"] == 5 and info["ack_bytes"] == 41 and info["request_bytes"] == 4
        with pytest.raises(ValueError):
            tr2.send_pipelined(b"")
    finally:
        tr2.close()


def test_chain_window_forces_intermediate_barriers(monkeypatch):
    from agent_runtime.scheduler.control_transport import UDSControlTransport

    monkeypatch.setenv("AGENT_ACK_WINDOW", "3")
    tr = UDSControlTransport(mode="text", codec="session_msgpack", ack_chain=True)
    try:
        for i in range(10):
            tr.send_pipelined(f"f{i}".encode())
        info = tr.barrier()
        assert info["verified"] == 10
        # 3 forced intermediate barriers (after 3, 6, 9 frames) + the final one: all charged.
        assert info["request_bytes"] == 4 * 4 and info["ack_bytes"] == 4 * 41
    finally:
        tr.close()


def test_ack_window_bounds_outstanding_frames(monkeypatch):
    from agent_runtime.scheduler.control_transport import UDSControlTransport

    monkeypatch.setenv("AGENT_ACK_WINDOW", "2")
    tr = UDSControlTransport(mode="text", codec="session_msgpack")
    try:
        for i in range(7):
            tr.send_pipelined(f"frame-{i}".encode())
            assert len(tr.__dict__["_pending_acks"]) <= 2
        info = tr.barrier()
        assert info["verified"] == 7
        assert not tr.__dict__["_pending_acks"]
    finally:
        tr.close()
