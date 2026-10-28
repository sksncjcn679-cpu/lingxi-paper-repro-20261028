"""Cross-process Unix Domain Socket transport regression."""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
import math
import socket
import struct

import pytest

from agent_runtime.protocol.mapper import from_wire, semantically_equivalent, to_wire
from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.scheduler.uds import (
    UDSFrameServer,
    UDSProtocolError,
    recv_frame,
    uds_request,
)


def _serve_echo(path: str, ready) -> None:
    with UDSFrameServer(path, timeout_sec=5.0) as server:
        ready.set()
        server.serve_once(lambda payload: payload)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")
def test_structured_agent_message_roundtrips_over_uds_in_fresh_process(tmp_path: Path):
    path = tmp_path / "agent.sock"
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    proc = ctx.Process(target=_serve_echo, args=(str(path), ready))
    proc.start()
    assert ready.wait(timeout=5), "UDS server failed to become ready"

    message = AgentMessage(
        trace_id="trace_uds",
        task_id="task_uds",
        step_id="s1",
        source_agent="planner",
        target_agent="retriever",
        action_type=ActionType.RETRIEVE,
        capability="retrieval",
        input_parameters={"plan_ref": "state://task/abc"},
        state_reference="state://task/abc",
        status=MessageStatus.PENDING,
    )
    wire = to_wire(message, "structured")
    echoed = uds_request(path, wire.encode("utf-8"), timeout_sec=5.0).decode("utf-8")
    decoded = from_wire(echoed, "structured")
    assert semantically_equivalent(message, decoded)
    assert decoded.state_reference == message.state_reference

    proc.join(timeout=5)
    assert proc.exitcode == 0


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")
def test_uds_server_refuses_to_replace_regular_file(tmp_path: Path):
    path = tmp_path / "agent.sock"
    path.write_text("do-not-delete", encoding="utf-8")
    server = UDSFrameServer(path)
    with pytest.raises(FileExistsError, match="non-socket"):
        server.start()
    assert path.read_text(encoding="utf-8") == "do-not-delete"


def test_uds_frame_limit_is_enforced_locally(tmp_path: Path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX unavailable")
    path = tmp_path / "agent.sock"
    with UDSFrameServer(path, max_frame_bytes=8) as server:
        # The client rejects oversize before touching the socket payload.
        with pytest.raises(UDSProtocolError, match="frame too large"):
            uds_request(path, b"x" * 9, max_frame_bytes=8, timeout_sec=0.1)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")
def test_uds_server_does_not_hijack_existing_socket_path(tmp_path: Path):
    path = tmp_path / "agent.sock"
    first = UDSFrameServer(path)
    first.start()
    try:
        second = UDSFrameServer(path)
        with pytest.raises(FileExistsError, match="existing socket"):
            second.start()
        # 第二个 server 的失败不能删除第一个 server 的 pathname。
        assert path.exists()
        assert first._sock is not None
    finally:
        first.close()
    assert not path.exists()


def test_recv_frame_rejects_truncated_peer():
    left, right = socket.socketpair()
    try:
        right.sendall(b"\x00\x00")
        right.close()
        with pytest.raises(UDSProtocolError, match="peer closed"):
            recv_frame(left)
    finally:
        left.close()


def test_recv_frame_rejects_oversized_declared_length():
    left, right = socket.socketpair()
    try:
        right.sendall(struct.pack("!I", 9))
        with pytest.raises(UDSProtocolError, match="declared frame too large"):
            recv_frame(left, max_frame_bytes=8)
    finally:
        left.close()
        right.close()


def test_uds_constructor_validates_frame_limit(tmp_path: Path):
    with pytest.raises(ValueError, match="positive"):
        UDSFrameServer(tmp_path / "bad.sock", max_frame_bytes=0)


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_uds_rejects_non_positive_or_non_finite_timeout(tmp_path: Path, timeout: float):
    with pytest.raises(ValueError, match="finite positive"):
        UDSFrameServer(tmp_path / "bad-timeout.sock", timeout_sec=timeout)
    with pytest.raises(ValueError, match="finite positive"):
        uds_request(tmp_path / "missing.sock", b"x", timeout_sec=timeout)


def test_frame_helpers_reject_invalid_frame_limits():
    left, right = socket.socketpair()
    try:
        with pytest.raises(ValueError, match="positive"):
            recv_frame(left, max_frame_bytes=0)
    finally:
        left.close()
        right.close()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")
def test_uds_start_is_idempotent_and_close_tolerates_missing_path(tmp_path: Path):
    path = tmp_path / "agent.sock"
    server = UDSFrameServer(path)
    server.start()
    server.start()
    path.unlink()
    server.close()
    server.close()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")
def test_explicit_replace_existing_socket_is_opt_in(tmp_path: Path):
    path = tmp_path / "stale.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()  # pathname remains, but there is no listener

    server = UDSFrameServer(path, replace_existing_socket=True)
    try:
        server.start()
        assert path.exists()
    finally:
        server.close()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")
def test_close_never_deletes_replaced_non_socket_path(tmp_path: Path):
    path = tmp_path / "agent.sock"
    server = UDSFrameServer(path)
    server.start()
    path.unlink()
    path.write_text("replacement", encoding="utf-8")
    server.close()
    assert path.read_text(encoding="utf-8") == "replacement"


def test_uds_requires_af_unix(monkeypatch, tmp_path: Path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("already unavailable")
    monkeypatch.delattr(socket, "AF_UNIX")
    with pytest.raises(RuntimeError, match="AF_UNIX"):
        UDSFrameServer(tmp_path / "x.sock")
    with pytest.raises(RuntimeError, match="AF_UNIX"):
        uds_request(tmp_path / "x.sock", b"x")
