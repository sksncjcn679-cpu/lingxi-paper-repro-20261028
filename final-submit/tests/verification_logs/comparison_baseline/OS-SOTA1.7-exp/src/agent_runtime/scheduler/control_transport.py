"""Verified persistent AF_UNIX control-plane service (Protocol 1.3 / v0.8.0).

A single dedicated Python router subprocess is kept warm for the Runtime process. Each Channel
opens a fresh UDS connection and performs a tiny handshake declaring text/codec/reference mode;
therefore Protocol-1.3 session dictionaries remain isolated per task even though router process
startup is amortized. Each control frame is decoded and independently re-encoded in the router,
which returns ``OK + SHA-256(reencoded_bytes)``. The client verifies that digest against the
transmitted payload.

This combines v0.7's stateful Session-MessagePack path with v0.6.6's subprocess lifecycle,
lossless digest acknowledgement and socket-call instrumentation, while avoiding fork() from a
multi-threaded Runtime and avoiding a hidden per-task router cold start.
"""
from __future__ import annotations

from typing import Any

import atexit
import contextlib
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

from agent_runtime.scheduler.uds import UDSProtocolError, recv_frame, send_frame

_ACK_OK = 0x01
_ACK_ERR = 0x00
_HELLO_OK = b"HELLO"
_NEED_ROUTINE = b"NEEDROUTINE"
# Router-side cache of negotiated CPHC routines (digest -> Routine), shared by connections.
_ROUTER_ROUTINES: dict[str, Any] = {}
_ROUTER_ROUTINES_LOCK = threading.Lock()


class _CountingSocket:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self.calls = 0

    def sendall(self, data: bytes) -> None:
        self.calls += 1
        self._sock.sendall(data)

    def recv(self, n: int) -> bytes:
        self.calls += 1
        return self._sock.recv(n)

    def sendmsg(self, buffers) -> int:
        self.calls += 1
        return self._sock.sendmsg(buffers)

    def __getattr__(self, name):
        return getattr(self._sock, name)


def _serve_state_connection(conn: socket.socket) -> None:  # pragma: no cover - router subprocess
    from agent_runtime.state_exchange.fd_passing import verify_sealed_fd

    while True:
        try:
            data, fds, _flags, _addr = socket.recv_fds(conn, 512, 4)
        except OSError:
            return
        if not data:
            return
        ok = False
        try:
            if len(data) > 512 or len(fds) != 1:
                raise ValueError("invalid state attachment header")
            header = json.loads(data.decode("utf-8"))
            result = verify_sealed_fd(
                fds[0], int(header["size"]), str(header["sha256"])
            )
            ok = bool(result.get("ok"))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            ok = False
        finally:
            for fd in fds:
                with contextlib.suppress(OSError):
                    os.close(fd)
        with contextlib.suppress(OSError):
            conn.sendall(b"\x01" if ok else b"\x00")


def _serve_connection(conn: socket.socket) -> None:  # pragma: no cover - router subprocess
    from agent_runtime.protocol.serialization import (
        decode_structured,
        decode_structured_msgpack,
        encode_structured,
        encode_structured_msgpack,
    )
    from agent_runtime.protocol.session_codec import SessionWireCodec

    try:
        hello_bytes = recv_frame(conn, prefetch=False)
        if hello_bytes == b"state\x00v1":
            send_frame(conn, _HELLO_OK)
            _serve_state_connection(conn)
            return
        hello = hello_bytes.decode("utf-8")
        fields = hello.split("\0")
        if len(fields) < 3:
            raise ValueError("malformed router hello")
        mode, codec, ref_text = fields[0], fields[1], fields[2]
        reference_first = ref_text == "1"
        # fable8: optional cumulative hash-chain acknowledgements (negotiated, backwards compatible).
        ack_chain = len(fields) >= 4 and fields[3] == "ackchain"
        if ack_chain:
            fields = fields[:3] + fields[4:]
        value_dictionary = codec == "session_msgpack_dict"
        predictive = False
        routine = None
        schema_atoms = len(fields) >= 5 and fields[3] == "cphc2"
        if len(fields) >= 5 and fields[3] in ("cphc", "cphc2"):
            from agent_runtime.protocol.routine import Routine

            predictive = True
            digest = fields[4]
            if len(fields) >= 6:
                routine = Routine.from_json(fields[5])
                if routine.digest != digest:
                    raise ValueError("routine digest mismatch")
                with _ROUTER_ROUTINES_LOCK:
                    _ROUTER_ROUTINES[digest] = routine
                    while len(_ROUTER_ROUTINES) > 64:
                        _ROUTER_ROUTINES.pop(next(iter(_ROUTER_ROUTINES)))
            elif digest != "-":
                with _ROUTER_ROUTINES_LOCK:
                    routine = _ROUTER_ROUTINES.get(digest)
                if routine is None:
                    send_frame(conn, _NEED_ROUTINE)
                    return
        decode_session = SessionWireCodec(value_dictionary=value_dictionary, predictive=predictive, routine=routine, schema_atoms=schema_atoms)
        encode_session = SessionWireCodec(value_dictionary=value_dictionary, predictive=predictive, routine=routine, schema_atoms=schema_atoms)
        send_frame(conn, _HELLO_OK)
        chain = hashlib.sha256()
        chain_count = 0
        chain_error: str | None = None
        while True:
            try:
                payload = recv_frame(conn)
            except Exception:
                break
            if ack_chain and not payload:
                # Barrier request (a codec payload is never empty): one cumulative ACK for every
                # frame since the previous barrier = SHA-256 over the per-frame re-encode digests.
                if chain_error is None:
                    send_frame(conn, bytes([_ACK_OK]) + chain.digest() + chain_count.to_bytes(4, "big"))
                else:
                    send_frame(conn, bytes([_ACK_ERR]) + chain_error.encode("utf-8", "replace")[:512])
                chain, chain_count, chain_error = hashlib.sha256(), 0, None
                continue
            try:
                if mode == "text":
                    again = payload
                elif codec == "json":
                    msg = decode_structured(payload.decode("utf-8"))
                    again = encode_structured(msg, reference_first=reference_first).encode("utf-8")
                elif codec == "msgpack":
                    msg = decode_structured_msgpack(payload)
                    again = encode_structured_msgpack(msg, reference_first=reference_first)
                elif codec == "session_json":
                    msg = decode_session.decode_json(payload.decode("utf-8"))
                    again = encode_session.encode_json(msg).encode("utf-8")
                elif codec in {"session_msgpack", "session_msgpack_dict"}:
                    msg = decode_session.decode_msgpack(payload)
                    again = encode_session.encode_msgpack(msg)
                else:
                    raise ValueError(f"unknown structured codec: {codec}")
                if ack_chain:
                    chain.update(hashlib.sha256(again).digest())
                    chain_count += 1
                else:
                    send_frame(conn, bytes([_ACK_OK]) + hashlib.sha256(again).digest())
            except Exception as exc:  # noqa: BLE001
                if ack_chain:
                    chain_count += 1
                    if chain_error is None:
                        chain_error = f"frame {chain_count}: {exc}"[:512]
                else:
                    send_frame(conn, bytes([_ACK_ERR]) + str(exc).encode("utf-8", "replace")[:512])
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(Exception):
            send_frame(conn, bytes([_ACK_ERR]) + str(exc).encode("utf-8", "replace")[:512])
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _router_service_main(path: str) -> int:  # pragma: no cover - router subprocess
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        srv.bind(path)
    finally:
        os.umask(old)
    srv.listen(32)
    print("READY", flush=True)
    threads: list[threading.Thread] = []
    try:
        while True:
            conn, _ = srv.accept()
            t = threading.Thread(target=_serve_connection, args=(conn,), daemon=True)
            t.start()
            threads.append(t)
            threads = [x for x in threads if x.is_alive()]
    except Exception:
        return 0
    finally:
        with contextlib.suppress(Exception):
            srv.close()
        with contextlib.suppress(FileNotFoundError):
            Path(path).unlink()


class _RouterService:
    def __init__(self) -> None:
        self._dir = tempfile.mkdtemp(prefix="agentrt-v080-router-")
        os.chmod(self._dir, 0o700)
        self.path = str(Path(self._dir) / "control.sock")
        env = dict(os.environ)
        src = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        code = (
            "import sys; from agent_runtime.scheduler.control_transport import _router_service_main; "
            "raise SystemExit(_router_service_main(sys.argv[1]))"
        )
        self._proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", code, self.path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        line = self._proc.stdout.readline() if self._proc.stdout else b""
        if line.strip() != b"READY":
            detail = b""
            if self._proc.stderr:
                with contextlib.suppress(Exception):
                    detail = self._proc.stderr.read(1024)
            self.close()
            raise RuntimeError(f"UDS router service failed to start: {detail.decode('utf-8','replace')}")

    @property
    def pid(self) -> int:
        return self._proc.pid

    def alive(self) -> bool:
        return self._proc.poll() is None

    def close(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.terminate()
                proc.wait(timeout=2)
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.kill(); proc.wait(timeout=1)
            for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
                if stream is not None:
                    with contextlib.suppress(Exception): stream.close()
        with contextlib.suppress(Exception):
            if os.path.exists(self.path): os.unlink(self.path)
        with contextlib.suppress(Exception): os.rmdir(self._dir)


_SERVICE: _RouterService | None = None
_SERVICE_LOCK = threading.Lock()


def _get_service() -> _RouterService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None or not _SERVICE.alive():
            if _SERVICE is not None:
                _SERVICE.close()
            _SERVICE = _RouterService()
        return _SERVICE


def shutdown_router_service() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        service, _SERVICE = _SERVICE, None
    if service is not None:
        service.close()


atexit.register(shutdown_router_service)


class UDSControlTransport:
    """One fresh Protocol session over the process-wide verified router subprocess."""

    def __init__(
        self,
        *,
        mode: str,
        codec: str,
        reference_first: bool = True,
        timeout_sec: float = 5.0,
        cphc: bool = False,
        routine: Any = None,
        ack_chain: bool = False,
        schema_atoms: bool = False,
    ) -> None:
        from agent_runtime.protocol.routine import ROUTINES

        self.cphc = bool(cphc)
        self.routine = routine
        self.routine_uploaded = False
        self.router_id: Any = None
        # fable8: cumulative hash-chain ACK (only meaningful with pipelined sends).
        self.ack_chain = bool(ack_chain)
        self.schema_atoms = bool(schema_atoms)
        self._chain = hashlib.sha256()
        self._chain_count = 0
        self.ack_window = min(1024, max(1, int(os.environ.get("AGENT_ACK_WINDOW", "64") or 64)))  # bounded: ACKs never fill the router send buffer
        self.barrier_error: str | None = None
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("AF_UNIX is not available on this platform")
        self.mode = mode; self.codec = codec; self.reference_first = bool(reference_first)
        self.timeout_sec = float(timeout_sec); self._lock = threading.Lock(); self.closed = False
        external_socket = os.environ.get("AGENT_ROUTER_SOCKET")
        service = None
        raw = None
        last_error: Exception | None = None
        for attempt in range(2):
            service = None if external_socket else _get_service()
            raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            raw.settimeout(self.timeout_sec)
            try:
                raw.connect(external_socket or service.path)
                self._sock = _CountingSocket(raw)
                # Fresh connection = fresh server-side SessionWireCodec state.
                router_id = external_socket or (service.pid if service is not None else None)
                parts = [mode, codec, "1" if reference_first else "0"]
                if self.ack_chain:
                    parts.append("ackchain")
                upload = False
                if self.cphc:
                    parts += ["cphc2" if self.schema_atoms else "cphc", routine.digest if routine is not None else "-"]
                    if routine is not None and ROUTINES.needs_upload(("uds", router_id), routine.digest):
                        parts.append(routine.to_json())
                        upload = True
                send_frame(self._sock, "\0".join(parts).encode("utf-8"))
                hello = recv_frame(self._sock)
                if hello == _NEED_ROUTINE and routine is not None and not upload:
                    # Router restarted or evicted the routine: upload it on a fresh connection.
                    ROUTINES.forget_router(("uds", router_id))
                    raise RuntimeError("router requested routine upload")
                if hello != _HELLO_OK:
                    raise RuntimeError(f"router session handshake failed: {hello!r}")
                self.routine_uploaded = upload
                self.router_id = router_id
                break
            except (OSError, RuntimeError) as exc:
                last_error = exc
                with contextlib.suppress(Exception):
                    raw.close()
                if "requested routine upload" in str(exc) and not attempt:
                    continue
                if external_socket or attempt:
                    raise RuntimeError(f"router session connection failed: {exc}") from exc
                # Force the process-wide service selector to discard a process that died
                # between poll() and connect(), then retry with a fresh router.
                global _SERVICE
                with _SERVICE_LOCK:
                    if _SERVICE is service:
                        _SERVICE = None
        else:
            raise RuntimeError(f"router session connection failed: {last_error}")
        self.router_pid = service.pid if service is not None else None
        # Message metrics intentionally start after this tiny connection handshake.
        self._sock.calls = 0

    def transmit(self, payload: bytes) -> tuple[float, int, int, int, bool]:
        if self.closed:
            raise RuntimeError("UDS control transport is closed")
        expected = hashlib.sha256(payload).digest()
        with self._lock:
            calls_before = self._sock.calls
            start = time.perf_counter()
            send_frame(self._sock, payload)
            reply = recv_frame(self._sock)
            elapsed = time.perf_counter() - start
            calls = self._sock.calls - calls_before
        if not reply or reply[0] != _ACK_OK:
            detail = reply[1:].decode("utf-8", errors="replace") if reply else "empty NACK"
            raise UDSProtocolError(f"router rejected control frame: {detail}")
        verified = reply[1:] == expected
        if not verified:
            raise UDSProtocolError("router lossless re-encode digest mismatch")
        return elapsed, len(payload)+4, len(reply)+4, calls, verified

    # ------------------------------------------------------------------ pipelined verification
    # fable7: verified pipelining with a bounded ACK window and a commit barrier.
    #
    # The synchronous path makes every Agent hop wait for the router to decode, re-encode and
    # hash the frame (one blocking round trip, two context switches).  The router's check is an
    # integrity *attestation* of the wire -- Agents consume the in-process message, not the
    # router's copy -- so it can be pipelined exactly like TCP's sliding window / a database
    # group commit: frames are sent immediately, ACK digests are matched in FIFO order whenever
    # the window fills or at the task commit barrier, and ANY missing or mismatching ACK fails
    # the task before its result is committed.  Every frame is still verified; only *when* the
    # client learns the verdict moves.  On multi-core hosts the router's work overlaps with Agent
    # work; on every host the ACKs are drained with far fewer wake-ups.
    def send_pipelined(self, payload: bytes) -> tuple[float, int, int]:
        if self.closed:
            raise RuntimeError("UDS control transport is closed")
        if not payload:
            raise ValueError("empty control frames are reserved for barrier requests")
        expected = hashlib.sha256(payload).digest()
        with self._lock:
            pending = self.__dict__.setdefault("_pending_acks", [])
            if self.ack_chain:
                if self._chain_count >= self.ack_window:
                    self._chain_barrier_locked()  # bounded detection latency
                calls_before = self._sock.calls
                start = time.perf_counter()
                send_frame(self._sock, payload)
                elapsed = time.perf_counter() - start
                self._chain.update(expected)
                self._chain_count += 1
                return elapsed, len(payload) + 4, self._sock.calls - calls_before
            if len(pending) >= self.ack_window:
                self._drain_locked(len(pending) - self.ack_window + 1)
            calls_before = self._sock.calls
            start = time.perf_counter()
            send_frame(self._sock, payload)
            elapsed = time.perf_counter() - start
            pending.append((expected, time.perf_counter()))
            return elapsed, len(payload) + 4, self._sock.calls - calls_before

    def _drain_locked(self, count: int) -> None:
        pending = self.__dict__.setdefault("_pending_acks", [])
        verified = self.__dict__.setdefault("_verified_acks", [])
        for _ in range(count):
            expected, _sent_at = pending.pop(0)
            calls_before = self._sock.calls
            reply = recv_frame(self._sock)
            self._ack_calls = getattr(self, "_ack_calls", 0) + (self._sock.calls - calls_before)
            if not reply or reply[0] != _ACK_OK:
                detail = reply[1:].decode("utf-8", errors="replace") if reply else "empty NACK"
                self.barrier_error = f"router rejected control frame: {detail}"
                raise UDSProtocolError(self.barrier_error)
            if reply[1:] != expected:
                self.barrier_error = "router lossless re-encode digest mismatch"
                raise UDSProtocolError(self.barrier_error)
            verified.append(len(reply) + 4)

    def _chain_barrier_locked(self) -> None:
        """Send a barrier request and verify the router's cumulative hash-chain ACK."""
        if self._chain_count == 0:
            return
        calls_before = self._sock.calls
        send_frame(self._sock, b"")
        reply = recv_frame(self._sock)
        self._ack_calls = getattr(self, "_ack_calls", 0) + (self._sock.calls - calls_before)
        self._barrier_request_bytes = getattr(self, "_barrier_request_bytes", 0) + 4
        verified = self.__dict__.setdefault("_verified_acks", [])
        count = self._chain_count
        expected = self._chain.digest()
        self._chain, self._chain_count = hashlib.sha256(), 0
        if not reply or reply[0] != _ACK_OK:
            detail = reply[1:].decode("utf-8", errors="replace") if reply else "empty NACK"
            self.barrier_error = f"router rejected control frame: {detail}"
            raise UDSProtocolError(self.barrier_error)
        if len(reply) != 37 or reply[1:33] != expected or int.from_bytes(reply[33:37], "big") != count:
            self.barrier_error = "router cumulative digest/count mismatch"
            raise UDSProtocolError(self.barrier_error)
        # One ACK frame covers `count` frames; charge its bytes once, spread as a single entry.
        verified.extend([0] * (count - 1) + [len(reply) + 4])

    def barrier(self) -> dict[str, Any]:
        """Collect and verify every outstanding ACK (the task commit barrier)."""
        with self._lock:
            if self.ack_chain:
                start = time.perf_counter()
                self._chain_barrier_locked()
                verified = self.__dict__.setdefault("_verified_acks", [])
                # Includes intermediate barriers forced by the window since the last call.
                out = {
                    "verified": len(verified),
                    "ack_bytes": sum(verified),
                    "request_bytes": getattr(self, "_barrier_request_bytes", 0),
                    "wait_sec": time.perf_counter() - start,
                    "socket_calls": getattr(self, "_ack_calls", 0),
                }
                verified.clear()
                self._barrier_request_bytes = 0
                self._ack_calls = 0
                return out
            pending = self.__dict__.setdefault("_pending_acks", [])
            start = time.perf_counter()
            if pending:
                self._drain_locked(len(pending))
            verified = self.__dict__.setdefault("_verified_acks", [])
            out = {
                "verified": len(verified),
                "ack_bytes": sum(verified),
                "wait_sec": time.perf_counter() - start,
                "socket_calls": getattr(self, "_ack_calls", 0),
            }
            verified.clear()
            self._ack_calls = 0
            return out

    def close(self) -> None:
        if self.closed: return
        self.closed = True
        with contextlib.suppress(Exception): self._sock.close()

    def __enter__(self) -> "UDSControlTransport": return self
    def __exit__(self, exc_type, exc, tb) -> None: self.close()


class UDSStateAttachTransport:
    """Dedicated SCM_RIGHTS channel for sealed state attachments."""

    def __init__(self, *, timeout_sec: float = 5.0) -> None:
        from agent_runtime.state_exchange.fd_passing import SealedSegment

        if not hasattr(socket, "AF_UNIX") or not hasattr(socket, "send_fds"):
            raise RuntimeError("SCM_RIGHTS is not available on this platform")
        self._segment_type = SealedSegment
        self.timeout_sec = float(timeout_sec)
        self.closed = False
        external_socket = os.environ.get("AGENT_ROUTER_SOCKET")
        service = None if external_socket else _get_service()
        self.router_pid = service.pid if service is not None else None
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(self.timeout_sec)
        raw.connect(external_socket or service.path)
        send_frame(raw, b"state\x00v1")
        if recv_frame(raw, prefetch=False) != _HELLO_OK:
            raw.close()
            raise RuntimeError("router state attachment handshake failed")
        self._sock = raw

    def attach_bytes(self, payload: bytes) -> tuple[bool, int]:
        if self.closed:
            raise RuntimeError("state attachment transport is closed")
        segment = self._segment_type.create(payload)
        try:
            import json

            header = json.dumps(
                {"size": segment.size, "sha256": segment.sha256},
                separators=(",", ":"),
            ).encode("utf-8")
            if len(header) > 512:
                raise ValueError("state attachment header exceeds 512 bytes")
            socket.send_fds(self._sock, [header], [segment.fd])
            reply = self._sock.recv(1)
            if not reply:
                raise ConnectionError("router closed state attachment channel")
            return reply == b"\x01", len(header) + 1
        finally:
            segment.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(OSError):
            self._sock.close()


__all__ = ["UDSControlTransport", "UDSStateAttachTransport", "shutdown_router_service"]
