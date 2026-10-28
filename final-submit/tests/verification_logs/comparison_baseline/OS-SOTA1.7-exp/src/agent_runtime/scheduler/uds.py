"""Unix Domain Socket framed transport.

这是系统层的可选 transport primitive，不改变默认 Orchestrator 的进程内执行模型。
消息采用 4-byte big-endian length prefix + payload，避免 ``SOCK_STREAM`` 的半包/粘包
问题。UDS socket 文件默认 0600，并限制单帧大小，适合作为同机 Agent 进程间控制面。
大数值 payload 应继续通过 ``state://`` + shared memory 引用传递，而不是内联进 UDS。
"""

from __future__ import annotations

import os
import math
from pathlib import Path
import socket
import stat
import struct
import weakref
from typing import Callable

DEFAULT_MAX_FRAME_BYTES = 4 * 1024 * 1024
_HEADER = struct.Struct("!I")


class UDSProtocolError(RuntimeError):
    """UDS framing / transport protocol error."""


# fable7: single-syscall framing.
#
# The historical framing issued two ``sendall`` calls (4-byte header, then payload) and two
# ``recv`` calls (header, then payload) per frame.  For the ~100-300 B control frames of the
# structured protocol this doubled the syscall count and, worse, woke the peer on the header
# alone so it immediately blocked again waiting for the payload: an extra context-switch pair on
# every hop.  Now the sender gathers header+payload in ONE ``sendmsg`` (scatter/gather iovec,
# no concatenation copy for large payloads) and the receiver drains up to 64 KiB per ``recv``
# into a per-socket reassembly buffer, so a complete small frame costs one syscall per side.
_RECV_CHUNK = 64 * 1024
_pending: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _buffer_for(sock: object) -> bytearray:
    try:
        buf = _pending.get(sock)
        if buf is None:
            buf = bytearray()
            _pending[sock] = buf
        return buf
    except TypeError:  # pragma: no cover - non-weakrefable socket-like object
        return bytearray()


def _recv_exact(sock: socket.socket, n: int, prefetch: bool = True) -> bytes:
    buf = _buffer_for(sock)
    while len(buf) < n:
        # prefetch=False never reads past the frame: required before a socket switches to
        # ancillary-data reads (SCM_RIGHTS), where an over-read would strip passed fds.
        chunk = sock.recv(max(_RECV_CHUNK, n - len(buf)) if prefetch else n - len(buf))
        if not chunk:
            raise UDSProtocolError("peer closed connection before frame completed")
        buf += chunk
    out = bytes(buf[:n])
    del buf[:n]
    return out


def _send_all_gather(sock: socket.socket, parts: list[bytes]) -> None:
    sendmsg = getattr(sock, "sendmsg", None)
    if sendmsg is None:
        sock.sendall(b"".join(parts))
        return
    views = [memoryview(p) for p in parts if len(p)]
    while views:
        sent = sendmsg(views)
        while views and sent >= len(views[0]):
            sent -= len(views[0])
            views.pop(0)
        if views and sent:
            views[0] = views[0][sent:]


def send_frame(sock: socket.socket, payload: bytes, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> None:
    if max_frame_bytes <= 0:
        raise ValueError("max_frame_bytes must be positive")
    if len(payload) > max_frame_bytes:
        raise UDSProtocolError(
            f"frame too large: {len(payload)} > max_frame_bytes={max_frame_bytes}"
        )
    _send_all_gather(sock, [_HEADER.pack(len(payload)), payload])


def recv_frame(
    sock: socket.socket, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES, prefetch: bool = True
) -> bytes:
    if max_frame_bytes <= 0:
        raise ValueError("max_frame_bytes must be positive")
    header = _recv_exact(sock, _HEADER.size, prefetch)
    (size,) = _HEADER.unpack(header)
    if size > max_frame_bytes:
        raise UDSProtocolError(f"declared frame too large: {size} > {max_frame_bytes}")
    return _recv_exact(sock, size, prefetch)


def buffered_bytes(sock: object) -> int:
    """Bytes already read from ``sock`` but not yet consumed as a frame."""
    try:
        buf = _pending.get(sock)
    except TypeError:  # pragma: no cover
        return 0
    return len(buf) if buf is not None else 0


class UDSFrameServer:
    """Single-threaded framed UDS server suitable for local control-plane IPC."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        timeout_sec: float = 5.0,
        replace_existing_socket: bool = False,
    ) -> None:
        if max_frame_bytes <= 0:
            raise ValueError("max_frame_bytes must be positive")
        if not math.isfinite(timeout_sec) or timeout_sec <= 0:
            raise ValueError("timeout_sec must be a finite positive number")
        if not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("AF_UNIX is not available on this platform")
        self.path = Path(path)
        self.max_frame_bytes = int(max_frame_bytes)
        self.timeout_sec = float(timeout_sec)
        self.replace_existing_socket = bool(replace_existing_socket)
        self._sock: socket.socket | None = None
        self._bound_identity: tuple[int, int] | None = None

    def start(self) -> None:
        if self._sock is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() or self.path.is_symlink():
            mode = self.path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise FileExistsError(f"refusing to replace non-socket path: {self.path}")
            if not self.replace_existing_socket:
                raise FileExistsError(
                    f"refusing to replace existing socket path: {self.path}; "
                    "remove a verified stale socket explicitly or opt in"
                )
            self.path.unlink()

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bound = False
        try:
            sock.settimeout(self.timeout_sec)
            sock.bind(str(self.path))
            bound = True
            st = self.path.lstat()
            self._bound_identity = (st.st_dev, st.st_ino)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            sock.listen(16)
        except Exception:
            sock.close()
            # 只有本实例已成功 bind 的 pathname 才允许清理；避免竞态下
            # bind 失败后误删另一进程刚创建的 socket。
            if bound:
                self._unlink_owned_path()
            raise
        self._sock = sock

    def serve_once(self, handler: Callable[[bytes], bytes]) -> None:
        if self._sock is None:
            raise RuntimeError("UDS server is not started")
        conn, _ = self._sock.accept()
        with conn:
            conn.settimeout(self.timeout_sec)
            request = recv_frame(conn, max_frame_bytes=self.max_frame_bytes)
            response = handler(request)
            if not isinstance(response, bytes):
                raise TypeError("UDS handler must return bytes")
            send_frame(conn, response, max_frame_bytes=self.max_frame_bytes)

    def _unlink_owned_path(self) -> None:
        """仅删除本 server bind 的同一 socket inode，避免路径替换竞态。"""
        identity = self._bound_identity
        if identity is None:
            return
        try:
            st = self.path.lstat()
        except FileNotFoundError:
            self._bound_identity = None
            return
        if stat.S_ISSOCK(st.st_mode) and (st.st_dev, st.st_ino) == identity:
            self.path.unlink()
        self._bound_identity = None

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._unlink_owned_path()

    def __enter__(self) -> "UDSFrameServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def uds_request(
    path: str | os.PathLike[str],
    payload: bytes,
    *,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    timeout_sec: float = 5.0,
) -> bytes:
    """Send one framed request and wait for one framed response."""
    if max_frame_bytes <= 0:
        raise ValueError("max_frame_bytes must be positive")
    if not math.isfinite(timeout_sec) or timeout_sec <= 0:
        raise ValueError("timeout_sec must be a finite positive number")
    if not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("AF_UNIX is not available on this platform")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_sec)
        sock.connect(str(path))
        send_frame(sock, payload, max_frame_bytes=max_frame_bytes)
        return recv_frame(sock, max_frame_bytes=max_frame_bytes)


__all__ = [
    "UDSFrameServer",
    "UDSProtocolError",
    "uds_request",
    "send_frame",
    "recv_frame",
    "DEFAULT_MAX_FRAME_BYTES",
]
