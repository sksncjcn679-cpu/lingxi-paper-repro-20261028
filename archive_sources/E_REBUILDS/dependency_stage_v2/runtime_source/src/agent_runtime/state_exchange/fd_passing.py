"""Sealed zero-copy state handoff: ``memfd_create`` + ``F_ADD_SEALS`` + ``SCM_RIGHTS`` (opus2).

Why this exists
---------------
Named POSIX shared memory (``/dev/shm/psm_*``) is reachable by *name*: any process that learns
the name can open it, the name must be unlinked explicitly (resource-tracker leaks), and the
producer can still mutate the pages after the consumer has validated them (TOCTOU).

Linux offers a capability-style alternative:

1. ``memfd_create`` - an anonymous memory file with no filesystem name;
2. ``F_ADD_SEALS(SHRINK|GROW|WRITE|SEAL)`` - after sealing, *nobody* (including the producer)
   can modify, resize or unseal it, so a validated object stays valid;
3. ``sendmsg(SCM_RIGHTS)`` over ``AF_UNIX`` - hands the *file descriptor* to exactly one peer;
   the payload never crosses the socket, the peer ``mmap``s the same pages read-only.

The kernel frees the object when the last descriptor closes, so there is nothing to unlink.

This module provides the primitive, a verifier process and a benchmark comparing it with
copying the same bytes through the socket.  Availability is probed at runtime; non-Linux hosts
report ``supported=False`` instead of pretending.
"""

from __future__ import annotations

import contextlib
import hashlib
import statistics
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import mmap
import os
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

_HDR = struct.Struct(">I")
_SEALS_REQUIRED = ("F_SEAL_SHRINK", "F_SEAL_GROW", "F_SEAL_WRITE", "F_SEAL_SEAL")



def _uname():
    """Portable ``os.uname`` (absent on Windows; fable16 crashed there in ``os-probe``)."""
    import platform

    return platform.uname()


def _api_present() -> bool:
    try:
        import fcntl

        return (
            sys.platform.startswith("linux")
            and hasattr(os, "memfd_create")
            and hasattr(socket, "send_fds")
            and all(hasattr(fcntl, name) for name in (*_SEALS_REQUIRED, "F_ADD_SEALS", "F_GET_SEALS"))
        )
    except ImportError:
        return False


_PROBE: dict[str, Any] | None = None


def probe_fd_capabilities(*, refresh: bool = False) -> dict[str, Any]:
    """Runtime probe of what *this kernel* allows on a fully sealed memfd.

    Kernel semantics differ: before Linux 6.7, ``mmap(MAP_SHARED, PROT_READ)`` of an
    ``F_SEAL_WRITE`` memfd fails with EPERM (the shared mapping could later be mprotect()ed
    writable); newer kernels allow it.  ``MAP_PRIVATE`` read-only mappings are accepted on both,
    and ``pread`` always works.  The consumer uses the best available mode.
    """
    global _PROBE
    if _PROBE is not None and not refresh:
        return _PROBE
    result: dict[str, Any] = {"api_present": _api_present(), "kernel": _uname().release,
                              "seal": False, "mmap_shared_ro": False, "mmap_private_ro": False, "pread": False,
                              "scm_rights": False, "errors": {}}
    if result["api_present"]:
        seg = None
        try:
            seg = SealedSegment.create(b"probe" * 64)
            result["seal"] = True
            for key, flags in (("mmap_shared_ro", mmap.MAP_SHARED), ("mmap_private_ro", mmap.MAP_PRIVATE)):
                try:
                    with mmap.mmap(seg.fd, seg.size, flags=flags, prot=mmap.PROT_READ) as mm:
                        result[key] = mm[:5] == b"probe"
                except OSError as exc:
                    result["errors"][key] = f"{type(exc).__name__}: {exc}"
            try:
                result["pread"] = os.pread(seg.fd, 5, 0) == b"probe"
            except OSError as exc:
                result["errors"]["pread"] = str(exc)
            a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                socket.send_fds(a, [b"x"], [seg.fd])
                _msg, fds, _f, _addr = socket.recv_fds(b, 16, 1)
                result["scm_rights"] = len(fds) == 1
                for fd in fds:
                    os.close(fd)
            finally:
                a.close()
                b.close()
        except OSError as exc:
            result["errors"]["setup"] = f"{type(exc).__name__}: {exc}"
        finally:
            if seg is not None:
                seg.close()
    # Same preference order as verify_sealed_fd(): MAP_PRIVATE (portable) → MAP_SHARED → pread.
    if result["mmap_private_ro"]:
        result["consumer_read_mode"] = "mmap_private_ro"
    elif result["mmap_shared_ro"]:
        result["consumer_read_mode"] = "mmap_shared_ro"
    elif result["pread"]:
        result["consumer_read_mode"] = "pread"
    else:
        result["consumer_read_mode"] = None
    result["supported"] = bool(result["seal"] and result["scm_rights"] and result["consumer_read_mode"])
    _PROBE = result
    return result


def fd_passing_supported() -> bool:
    return _api_present() and bool(probe_fd_capabilities().get("supported"))


def _all_seals() -> int:
    import fcntl

    return fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SEAL


@dataclass
class SealedSegment:
    """An immutable anonymous memory object owned through a file descriptor."""

    fd: int
    size: int
    sha256: str

    @classmethod
    def create(cls, payload: bytes, name: str = "agent-state") -> SealedSegment:
        import fcntl

        fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            view = memoryview(payload)
            written = 0
            while written < len(view):
                written += os.write(fd, view[written:])
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, _all_seals())
        except BaseException:
            os.close(fd)
            raise
        return cls(fd=fd, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())

    @classmethod
    def build(cls, nbytes: int, writer, *, name: str = "agent-state", compute_sha: bool = True) -> SealedSegment:
        """In-place production: the producer writes straight into the memfd pages, then seals.

        Avoids the extra user-space copy of ``create(payload)``.  The writable shared mapping is
        unmapped before sealing (the kernel refuses F_SEAL_WRITE while one exists).
        """
        import fcntl

        fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.ftruncate(fd, nbytes)
            digest = ""
            if nbytes:
                with mmap.mmap(fd, nbytes, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ | mmap.PROT_WRITE) as mm:
                    view = memoryview(mm)
                    try:
                        writer(view)
                        if compute_sha:
                            digest = hashlib.sha256(view).hexdigest()
                    finally:
                        view.release()
            elif compute_sha:
                digest = hashlib.sha256(b"").hexdigest()
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, _all_seals())
        except BaseException:
            os.close(fd)
            raise
        return cls(fd=fd, size=nbytes, sha256=digest)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self.fd)


def verify_sealed_fd(fd: int, size: int, expected_sha256: str | None = None) -> dict[str, Any]:
    """Consumer side: require full seals, map read-only, optionally verify content hash."""
    import fcntl

    seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
    sealed = (seals & _all_seals()) == _all_seals()
    if not sealed:
        return {"ok": False, "reason": "segment not fully sealed"}
    if os.fstat(fd).st_size != size:
        return {"ok": False, "reason": "size mismatch"}
    if size == 0:
        return {"ok": expected_sha256 in (None, hashlib.sha256(b"").hexdigest()), "sealed": True, "read_mode": "none"}
    # Read-only MAP_PRIVATE is accepted on sealed memfds by old and new kernels (MAP_SHARED is
    # rejected with EPERM before Linux 6.7).  Pages stay shared (no COW without writes), so the
    # handoff remains zero-copy.  pread() is the last-resort fallback.
    digest = None
    read_mode = None
    for flags, mode in ((mmap.MAP_PRIVATE, "mmap_private_ro"), (mmap.MAP_SHARED, "mmap_shared_ro")):
        try:
            with mmap.mmap(fd, size, flags=flags, prot=mmap.PROT_READ) as mm:
                digest = hashlib.sha256(mm).hexdigest() if expected_sha256 is not None else None
                if expected_sha256 is None:
                    _ = mm[0]  # touch: prove the mapping is readable
            read_mode = mode
            break
        except OSError:
            continue
    if read_mode is None:
        h = hashlib.sha256()
        offset = 0
        while offset < size:
            chunk = os.pread(fd, min(1 << 20, size - offset), offset)
            if not chunk:
                return {"ok": False, "reason": "short read"}
            h.update(chunk)
            offset += len(chunk)
        digest = h.hexdigest() if expected_sha256 is not None else None
        read_mode = "pread"
    return {"ok": expected_sha256 is None or digest == expected_sha256, "sealed": True, "read_mode": read_mode}


# ---------------------------------------------------------------------------- verifier process
def _send(sock: socket.socket, data: bytes, fds: list[int] | None = None) -> int:
    header = _HDR.pack(len(data))
    if fds:
        return socket.send_fds(sock, [header + data], fds)
    # Two sends instead of concatenating: avoids copying a large payload in user space.
    sock.sendall(header)
    sock.sendall(data)
    return len(header) + len(data)


def _recv_exact(sock: socket.socket, n: int, prefix: bytes = b"") -> bytearray:
    """Receive exactly ``n`` bytes into one preallocated buffer (no quadratic re-copying)."""
    buf = bytearray(n)
    got = min(len(prefix), n)
    buf[:got] = prefix[:got]
    view = memoryview(buf)
    while got < n:
        k = sock.recv_into(view[got:], n - got)
        if k == 0:
            raise ConnectionError("peer closed")
        got += k
    return buf


def _verifier_main(sock_fd: int) -> int:  # pragma: no cover - runs in the verifier process
    sock = socket.socket(fileno=sock_fd)
    while True:
        try:
            first, fds, _flags, _addr = socket.recv_fds(sock, 65536, 4)
        except OSError:
            return 0
        if not first:
            return 0
        (hlen,) = _HDR.unpack(first[:4])
        head = first[4:]
        while len(head) < hlen:
            head += _recv_exact(sock, hlen - len(head))
        header, rest = json.loads(head[:hlen]), head[hlen:]
        verify = bool(header.get("verify"))
        try:
            if header["mode"] == "fd":
                result = verify_sealed_fd(fds[0], header["size"], header["sha256"] if verify else None)
            elif header["mode"] in {"copy", "raw_copy"}:  # payload follows as one framed message
                if len(rest) < 4:
                    rest = bytes(_recv_exact(sock, 4, rest))
                (plen,) = _HDR.unpack(rest[:4])
                body = _recv_exact(sock, plen, rest[4:])
                ok = (
                    hashlib.sha256(body).hexdigest() == header.get("sha256")
                    if verify else len(body) == plen
                )
                result = {"ok": ok, "sealed": False}
        finally:
            for fd in fds:
                with contextlib.suppress(OSError):
                    os.close(fd)
        sock.sendall(b"\x01" if result.get("ok") else b"\x00")


class VerifierProcess:
    """A separate consumer process connected by an AF_UNIX socketpair."""

    def __init__(self) -> None:
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock = parent
        src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env = dict(os.environ)
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        self._proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c",
             "import sys; from agent_runtime.state_exchange.fd_passing import _verifier_main; sys.exit(_verifier_main(int(sys.argv[1])))",
             str(child.fileno())],
            pass_fds=[child.fileno()], env=env, start_new_session=True,
        )
        child.close()
        self.pid = self._proc.pid

    def handoff_fd(self, seg: SealedSegment, *, verify: bool) -> tuple[bool, int]:
        header = json.dumps({"mode": "fd", "size": seg.size, "sha256": seg.sha256, "verify": verify}).encode()
        sent = _send(self._sock, header, [seg.fd])
        return _recv_exact(self._sock, 1) == b"\x01", sent

    def handoff_copy(self, payload: bytes, sha256: str, *, verify: bool) -> tuple[bool, int]:
        header = json.dumps({"mode": "copy", "size": len(payload), "sha256": sha256, "verify": verify}).encode()
        sent = _send(self._sock, header)
        sent += _send(self._sock, payload)
        return _recv_exact(self._sock, 1) == b"\x01", sent

    def handoff_copy_timed(
        self, payload: bytes, *, raw: bool, verify: bool = False
    ) -> tuple[bool, int, float, float]:
        """Send bytes while separating metadata/encoding work from socket work."""
        serialize_start = time.perf_counter()
        header: dict[str, Any] = {"mode": "raw_copy" if raw else "copy", "size": len(payload), "verify": verify}
        if verify:
            header["sha256"] = hashlib.sha256(payload).hexdigest()
        encoded_header = json.dumps(header, separators=(",", ":")).encode()
        serialize_ms = (time.perf_counter() - serialize_start) * 1000
        socket_start = time.perf_counter()
        sent = _send(self._sock, encoded_header)
        sent += _send(self._sock, payload)
        ok = _recv_exact(self._sock, 1) == b"\x01"
        socket_ms = (time.perf_counter() - socket_start) * 1000
        return ok, sent, serialize_ms, socket_ms

    def handoff_raw_copy(self, payload: bytes, *, verify: bool = False) -> tuple[bool, int]:
        ok, sent, _serialize_ms, _socket_ms = self.handoff_copy_timed(payload, raw=True, verify=verify)
        return ok, sent

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._sock.close()
        with contextlib.suppress(Exception):
            self._proc.wait(timeout=2)
        with contextlib.suppress(Exception):
            self._proc.kill()


# ---------------------------------------------------------------------------- policy
# Default = measured crossover of ``fd-passing-benchmark`` on the dev host (64 KiB: copy wins; 1 MiB: fd wins) (fixed sendmsg/mmap/seal
# overhead dominates small payloads).  Override with AGENT_FD_HANDOFF_MIN_BYTES after measuring
# the target host.
DEFAULT_FD_HANDOFF_MIN_BYTES = 1024 * 1024
# Conservative fallbacks used only when the host has not been calibrated yet.
DEFAULT_MIN_FANOUT_COPY_IN = 8
DEFAULT_MIN_FANOUT_IN_PLACE = 4


# ---------------------------------------------------------------------------- calibration
CALIBRATION_VERSION = 2  # fable7: interleaved randomized arms, MAD stability, guarded thresholds


def calibration_path() -> Path:
    override = os.environ.get("AGENT_FD_CALIBRATION")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "agent-runtime" / "fd_policy.json"


CALIBRATION_SIZES = (1 << 20, 4 << 20, 16 << 20)


def calibrate_fd_policy(*, sizes: tuple[int, ...] = CALIBRATION_SIZES, reps: int = 5, save: bool = True,
                        size: int | None = None) -> dict[str, Any]:
    """Measure this host's copy/materialise/handoff costs and derive break-even fan-outs.

    Hard-coded thresholds do not transfer between hosts: on the dev host copying 16 MiB cost
    ~5 ms while materialising a memfd cost ~16 ms (break-even k=8), but on the openEuler target
    copying the same object cost ~17 ms and materialising ~15 ms (break-even k=1).  So the
    runtime measures the host once and caches the result; ``choose_handoff`` reads it.
    """
    if not fd_passing_supported():
        return {"supported": False, "reason": "memfd/SCM_RIGHTS unavailable"}
    if size is not None:
        sizes = (size,)
    buckets: dict[str, Any] = {}
    verifier_outer = None
    for one in sorted(sizes):
        buckets[str(one)] = _calibrate_one(one, reps)
    result = {
        "supported": True, "version": CALIBRATION_VERSION, "reps": max(3, reps),
        "kernel": _uname().release, "machine": _uname().machine,
        "buckets": buckets,
        "consistency": calibration_consistency(buckets),
        # Backwards-compatible top-level view (largest calibrated bucket).
        **{k: buckets[str(max(sizes))][k] for k in ("copy_ms", "fd_handoff_ms", "materialise_copy_in_ms",
                                                    "materialise_in_place_ms", "k_copy_in", "k_in_place")},
        "size": max(sizes),
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }
    if save:
        try:
            path = calibration_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result["path"] = str(path)
        except OSError as exc:  # pragma: no cover - read-only cache dir
            result["save_error"] = str(exc)
    global _CALIBRATION
    _CALIBRATION = result
    return result


def _mad_ratio(values: list[float]) -> float:
    med = statistics.median(values)
    if med <= 0:
        return 0.0
    return statistics.median(abs(v - med) for v in values) / med


def _calibrate_one(size: int, reps: int) -> dict[str, Any]:
    """Robust per-size cost calibration (fable7).

    fable5 measured the four arms back-to-back in a fixed order with 5 repetitions.  On the
    openEuler VM that produced 97.3 ms for a 4 MiB socket copy while the fan-out study on the
    same VM measured 17.3 ms for 16 MiB -- a 20x per-byte disagreement that a threshold must not
    be derived from.  Now each arm is warmed separately, the arm order is shuffled every
    repetition (so page-cache / scheduler drift is spread over all arms), at least 9 samples are
    taken, the median is used and the median-absolute-deviation ratio is recorded.  A bucket whose
    copy or handoff dispersion exceeds 50% of its median is flagged ``stable=False`` and does not
    override the conservative defaults (see :func:`policy_thresholds`).
    """
    import random

    payload = os.urandom(size)
    sha = hashlib.sha256(payload).hexdigest()
    fill = lambda view: view.__setitem__(slice(None), payload)  # noqa: E731
    samples: dict[str, list[float]] = {"copy": [], "handoff": [], "create": [], "inplace": []}
    rng = random.Random(size)
    verifier = VerifierProcess()

    def arm_copy() -> None:
        t0 = time.perf_counter()
        verifier.handoff_copy(payload, sha, verify=False)
        samples["copy"].append(time.perf_counter() - t0)

    def arm_fd() -> None:
        t0 = time.perf_counter()
        seg = SealedSegment.create(payload)
        samples["create"].append(time.perf_counter() - t0)
        try:
            t0 = time.perf_counter()
            verifier.handoff_fd(seg, verify=False)
            samples["handoff"].append(time.perf_counter() - t0)
        finally:
            seg.close()

    def arm_inplace() -> None:
        t0 = time.perf_counter()
        seg = SealedSegment.build(size, fill, compute_sha=False)
        samples["inplace"].append(time.perf_counter() - t0)
        seg.close()

    arms = [arm_copy, arm_fd, arm_inplace]
    try:
        for arm in arms:  # per-arm warm-up, discarded
            arm()
        for values in samples.values():
            values.clear()
        for _ in range(max(9, reps)):
            order = arms[:]
            rng.shuffle(order)
            for arm in order:
                arm()
    finally:
        verifier.close()
    c = statistics.median(samples["copy"]) * 1000
    h = statistics.median(samples["handoff"]) * 1000
    m_copy = statistics.median(samples["create"]) * 1000
    m_place = statistics.median(samples["inplace"]) * 1000
    dispersion = {k: round(_mad_ratio(v), 4) for k, v in samples.items()}
    stable = max(dispersion["copy"], dispersion["handoff"]) <= 0.5
    gain = c - h  # saved per additional consumer by handing an fd instead of copying

    def break_even(materialise: float) -> int:
        if gain <= 0:
            return 1 << 30  # copying is cheaper per consumer: never pay for a memfd
        return max(1, math.ceil(materialise / gain))

    return {
        "size": size, "copy_ms": c, "fd_handoff_ms": h,
        "materialise_copy_in_ms": m_copy, "materialise_in_place_ms": m_place,
        "k_copy_in": break_even(m_copy), "k_in_place": break_even(m_place),
        "samples": len(samples["copy"]), "mad_ratio": dispersion, "stable": stable,
        "copy_mb_per_s": (size / (1 << 20)) / (c / 1000) if c > 0 else None,
    }


def calibration_consistency(buckets: dict[str, Any]) -> dict[str, Any]:
    """Cross-bucket sanity: per-byte copy cost should not vary by more than 8x across sizes."""
    rates = [b["copy_ms"] / (int(k) / (1 << 20)) for k, b in buckets.items() if b.get("copy_ms")]
    if len(rates) < 2:
        return {"consistent": True, "per_mib_copy_ms_spread": 1.0}
    spread = max(rates) / max(1e-9, min(rates))
    return {"consistent": spread <= 8.0, "per_mib_copy_ms_spread": round(spread, 2)}


_CALIBRATION: dict[str, Any] | None = None


def load_calibration(*, refresh: bool = False) -> dict[str, Any] | None:
    """Cached host calibration; ignored when it was produced on a different kernel/arch."""
    global _CALIBRATION
    if _CALIBRATION is not None and not refresh:
        return _CALIBRATION
    try:
        data = json.loads(calibration_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (data.get("version") != CALIBRATION_VERSION or data.get("kernel") != _uname().release
            or data.get("machine") != _uname().machine):
        return None
    _CALIBRATION = data
    return data


def policy_thresholds(size: int | None = None) -> dict[str, Any]:
    """Effective (k_copy_in, k_in_place) with provenance: env override > calibration > default.

    Costs are not linear in payload size, so the calibration stores per-size buckets and the
    threshold for a request is taken from the nearest bucket >= size (largest bucket otherwise).
    """
    cal = load_calibration()
    src = "calibration" if cal else "default"
    bucket = None
    if cal and cal.get("buckets"):
        keys = sorted(int(k) for k in cal["buckets"])
        pick = next((k for k in keys if size is not None and k >= size), keys[-1])
        bucket = cal["buckets"][str(pick)]
        if bucket.get("stable") is False or not cal.get("consistency", {}).get("consistent", True):
            # Noisy or self-contradictory host measurement: keep conservative defaults.
            src = "default (calibration rejected: unstable or inconsistent)"
            cal = None
            bucket = None
    k_copy = int((bucket or cal or {}).get("k_copy_in", DEFAULT_MIN_FANOUT_COPY_IN)) if cal else DEFAULT_MIN_FANOUT_COPY_IN
    k_place = int((bucket or cal or {}).get("k_in_place", DEFAULT_MIN_FANOUT_IN_PLACE)) if cal else DEFAULT_MIN_FANOUT_IN_PLACE
    for name, key in (("AGENT_FD_MIN_FANOUT_COPYIN", "copy"), ("AGENT_FD_MIN_FANOUT_INPLACE", "place")):
        raw = os.environ.get(name)
        if raw:
            try:
                value = max(1, int(raw))
            except ValueError:
                continue
            src = "env"
            if key == "copy":
                k_copy = value
            else:
                k_place = value
    return {"k_copy_in": k_copy, "k_in_place": k_place, "source": src,
            "bucket_bytes": (bucket or {}).get("size"),
            "kernel": (cal or {}).get("kernel", _uname().release)}


def choose_handoff(size: int, *, fanout: int = 1, in_place: bool = False, min_fd_bytes: int | None = None) -> str:
    """Cost-aware transport selection (opus5 cost model).

    Measured with an efficient copy baseline: handing an *existing* sealed segment is much cheaper
    than copying, but materialising bytes into a memfd costs about one copy.  So fd only wins when
    the object is large AND (it is consumed by >= 2 receivers, or the producer writes it in place).
    """
    if not fd_passing_supported():
        return "copy"
    # Break-even fan-outs measured by fd-passing-benchmark (fan-out study) on the dev host with an
    # efficient copy baseline: copy-in memfd pays off from k=8 consumers, in-place production from
    # k=4.  Re-measure on the target host and override via the environment.
    th = policy_thresholds(size)
    need = th["k_in_place"] if in_place else th["k_copy_in"]
    if fanout < need:
        return "copy"
    threshold = min_fd_bytes
    if threshold is None:
        try:
            threshold = int(os.environ.get("AGENT_FD_HANDOFF_MIN_BYTES", DEFAULT_FD_HANDOFF_MIN_BYTES))
        except ValueError:
            threshold = DEFAULT_FD_HANDOFF_MIN_BYTES
    return "fd" if size >= threshold else "copy"


# ---------------------------------------------------------------------------- benchmark
def _pct(v: list[float], q: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def run_fd_passing_benchmark(sizes: tuple[int, ...] = (512, 4096, 65536, 1 << 20, 16 << 20), repeat: int = 30) -> dict[str, Any]:
    from datetime import datetime, timezone

    from agent_runtime.evaluation.runner import _env_info

    if not fd_passing_supported():
        return {"suite": "fd_passing", "supported": False, "reason": "requires Linux memfd_create + F_ADD_SEALS + send_fds"}
    verifier = VerifierProcess()
    rows: dict[str, Any] = {}
    tamper_rejected = False
    try:
        for size in sizes:
            payload = os.urandom(size)
            sha = hashlib.sha256(payload).hexdigest()
            reps = repeat if size <= (1 << 20) else max(5, repeat // 5)
            res: dict[str, Any] = {}
            for mode in ("copy", "fd"):
                for verify in (False, True):
                    lat, lat_total, ok_all, sent_bytes = [], [], True, 0
                    for _ in range(reps):
                        t0 = time.perf_counter()
                        if mode == "fd":
                            seg = SealedSegment.create(payload)  # producer materializes once
                            t1 = time.perf_counter()
                            ok, sent = verifier.handoff_fd(seg, verify=verify)
                            seg.close()
                        else:
                            t1 = t0
                            ok, sent = verifier.handoff_copy(payload, sha, verify=verify)
                        now = time.perf_counter()
                        lat.append(now - t1)
                        lat_total.append(now - t0)
                        ok_all &= ok
                        sent_bytes = sent
                    res[f"{mode}{'_verify' if verify else ''}"] = {
                        "p50_ms": _pct(lat, 0.5) * 1000, "p95_ms": _pct(lat, 0.95) * 1000,
                        "p50_incl_create_ms": _pct(lat_total, 0.5) * 1000,
                        "socket_bytes": sent_bytes, "all_ok": ok_all, "repeat": reps,
                    }
            res["socket_byte_reduction"] = 1 - res["fd"]["socket_bytes"] / res["copy"]["socket_bytes"]
            res["handoff_speedup_p50"] = res["copy"]["p50_ms"] / max(1e-9, res["fd"]["p50_ms"])
            rows[str(size)] = res
        crossover = next((int(sz) for sz, r in rows.items() if r["fd"]["p50_incl_create_ms"] < r["copy"]["p50_ms"]), None)
        fan = _fanout_study(verifier, min(max(sizes), 16 << 20), reps=max(5, repeat // 3))
        # The fan-out study measures exactly the decision the policy has to make, so its observed
        # break-evens supersede the quick os-probe estimate for that size bucket.
        _store_observed_break_even(fan)
        # A producer cannot mutate a sealed segment after handoff (TOCTOU protection).
        seg = SealedSegment.create(b"immutable-state")
        try:
            os.pwrite(seg.fd, b"X", 0)
        except PermissionError:
            tamper_rejected = True
        finally:
            seg.close()
    finally:
        verifier.close()
    return {"suite": "fd_passing", "supported": True, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "env": _env_info(), "results": rows, "sealed_write_rejected": tamper_rejected,
            "measured_crossover_bytes": crossover,
            "kernel_probe": probe_fd_capabilities(),
            "calibration": load_calibration() or {},
            "policy": policy_thresholds(fan["size"] if fan else None),
            "policy_mix": _policy_mix(rows, fan),
            "fanout_study": fan,
            "method": "separate consumer process over AF_UNIX socketpair; copy = framed bytes through socket; fd = memfd sealed SHRINK|GROW|WRITE|SEAL + SCM_RIGHTS + read-only mmap; handoff latency excludes producer materialization (both paths already hold the bytes)"}


def _fanout_study(verifier: "VerifierProcess", size: int, reps: int) -> dict[str, Any]:
    """Total cost of delivering one object to k consumers: copy k times vs materialise once + k fd handoffs."""
    payload = os.urandom(size)
    sha = hashlib.sha256(payload).hexdigest()
    out: dict[str, Any] = {"size": size, "rows": {}}
    fill = lambda view: view.__setitem__(slice(None), payload)  # noqa: E731 - in-place writer
    for k in (1, 2, 4, 8):
        copy_t, fd_t, inplace_t = [], [], []
        for _ in range(reps):
            t0 = time.perf_counter()
            for _ in range(k):
                verifier.handoff_copy(payload, sha, verify=False)
            copy_t.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            seg = SealedSegment.create(payload)
            for _ in range(k):
                verifier.handoff_fd(seg, verify=False)
            seg.close()
            fd_t.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            seg = SealedSegment.build(size, fill, compute_sha=False)
            for _ in range(k):
                verifier.handoff_fd(seg, verify=False)
            seg.close()
            inplace_t.append(time.perf_counter() - t0)
        out["rows"][str(k)] = {"copy_ms": _pct(copy_t, 0.5) * 1000, "fd_create_ms": _pct(fd_t, 0.5) * 1000,
                               "fd_inplace_ms": _pct(inplace_t, 0.5) * 1000}
    out["break_even_fanout"] = next((int(k) for k, r in out["rows"].items() if r["fd_create_ms"] < r["copy_ms"]), None)
    out["break_even_fanout_inplace"] = next((int(k) for k, r in out["rows"].items() if r["fd_inplace_ms"] < r["copy_ms"]), None)
    return out


def _store_observed_break_even(fan: dict[str, Any]) -> None:
    cal = load_calibration(refresh=True) or {"supported": True, "version": CALIBRATION_VERSION,
                                             "kernel": _uname().release, "machine": _uname().machine,
                                             "buckets": {}, "reps": 0}
    k_copy = fan.get("break_even_fanout") or DEFAULT_MIN_FANOUT_COPY_IN
    k_place = fan.get("break_even_fanout_inplace") or DEFAULT_MIN_FANOUT_IN_PLACE
    bucket = {"size": fan["size"], "k_copy_in": int(k_copy), "k_in_place": int(k_place),
              "source": "fanout_study", "copy_ms": fan["rows"]["1"]["copy_ms"],
              "fd_handoff_ms": None, "materialise_copy_in_ms": None, "materialise_in_place_ms": None}
    cal.setdefault("buckets", {})[str(fan["size"])] = bucket
    cal["measured_at"] = datetime.now(timezone.utc).isoformat()
    for key in ("k_copy_in", "k_in_place"):
        cal[key] = bucket[key]
    cal["size"] = fan["size"]
    global _CALIBRATION
    _CALIBRATION = cal
    try:
        path = calibration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cal, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:  # pragma: no cover
        pass


def _policy_mix(rows: dict[str, Any], fan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pipeline mix: 90 small single-consumer states + 10 large tensors read by 4 consumers.

    Small-state costs come from the size table; large-tensor costs come from the measured
    fan-out study (k=4): copy 4x, or materialise once (copy-in or in-place) + 4 fd handoffs.
    """
    sizes = sorted(int(z) for z in rows)
    if not sizes or not fan or "4" not in fan.get("rows", {}):
        return {}
    small, k = sizes[0], 4
    f = fan["rows"][str(k)]
    large = int(fan["size"])
    small_copy = rows[str(small)]["copy"]["p50_ms"]
    small_fd = rows[str(small)]["fd"]["p50_incl_create_ms"]
    hdr = rows[str(small)]["fd"]["socket_bytes"]
    copy_large = (f["copy_ms"], k * (large + 136))
    small_part = (90 * small_copy, 90 * (small + 132))

    def policy(in_place: bool):
        # Same decision function the runtime uses; large tensors have fan-out k.
        big = choose_handoff(large, fanout=k, in_place=in_place)
        big_cost = (f["fd_inplace_ms"] if in_place else f["fd_create_ms"], k * hdr) if big == "fd" else copy_large
        return (small_part[0] + 10 * big_cost[0], small_part[1] + 10 * big_cost[1]), big

    (a_ms, a_b), a_dec = policy(False)
    (i_ms, i_b), i_dec = policy(True)
    plan = {
        "always_copy": (90 * small_copy + 10 * f["copy_ms"], small_part[1] + 10 * copy_large[1]),
        "always_fd": (90 * small_fd + 10 * f["fd_create_ms"], 100 * hdr + 10 * (k - 1) * hdr),
        "adaptive": (a_ms, a_b),
        "adaptive_inplace": (i_ms, i_b),
    }
    out: dict[str, Any] = {"mix": {f"{small}B x1 consumer": 90, f"{large}B x{k} consumers": 10}}
    for name, (ms, sb) in plan.items():
        out[name] = {"total_ms": ms, "socket_bytes": sb}
    out["adaptive"]["large_decision"] = a_dec
    out["adaptive_inplace"]["large_decision"] = i_dec
    return out


def render_fd_passing(p: dict[str, Any]) -> str:
    if not p.get("supported"):
        return f"# Sealed fd handoff\n\nNot supported on this host: {p.get('reason')}\n"
    lines = ["# Sealed zero-copy state handoff: memfd + SCM_RIGHTS vs socket copy", "",
             "| Payload | Socket bytes copy → fd | Handoff p50 copy / fd ms | fd incl. memfd create ms | + SHA-256 verify p50 copy / fd ms | Speed-up (no verify) | All verified |",
             "| ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for size, r in p["results"].items():
        lines.append(f"| {int(size):,} B | {r['copy']['socket_bytes']:,} → {r['fd']['socket_bytes']:,} (-{r['socket_byte_reduction']:.1%}) | "
                     f"{r['copy']['p50_ms']:.3f} / {r['fd']['p50_ms']:.3f} | {r['fd']['p50_incl_create_ms']:.3f} | {r['copy_verify']['p50_ms']:.3f} / {r['fd_verify']['p50_ms']:.3f} | "
                     f"{r['handoff_speedup_p50']:.1f}x | {all(r[k]['all_ok'] for k in ('copy', 'fd', 'copy_verify', 'fd_verify'))} |")
    cx = p.get("measured_crossover_bytes")
    configured = DEFAULT_FD_HANDOFF_MIN_BYTES
    lines += ["", f"- Measured crossover in this run (fd incl. create beats copy): **{cx:,} B**; the configured policy threshold is **{configured:,} B** and can be overridden with `AGENT_FD_HANDOFF_MIN_BYTES`." if cx else f"- No crossover in the measured range: the configured policy threshold remains **{configured:,} B** and can be overridden with `AGENT_FD_HANDOFF_MIN_BYTES`."]
    kp = p.get("kernel_probe") or {}
    lines += [f"- Kernel probe ({kp.get('kernel')}): MAP_SHARED ro={kp.get('mmap_shared_ro')}, MAP_PRIVATE ro={kp.get('mmap_private_ro')}, pread={kp.get('pread')}, SCM_RIGHTS={kp.get('scm_rights')} → consumer read mode **{kp.get('consumer_read_mode')}**."]
    fs = p.get("fanout_study") or {}
    if fs.get("rows"):
        lines += ["", f"## Fan-out study ({fs['size']:,} B delivered to k consumers)", "",
                  "| k | copy k times ms | copy-in memfd + k fd ms | in-place memfd + k fd ms |", "| ---: | ---: | ---: | ---: |"]
        for k, r in fs["rows"].items():
            lines.append(f"| {k} | {r['copy_ms']:.2f} | {r['fd_create_ms']:.2f} | {r['fd_inplace_ms']:.2f} |")
        pol = p.get("policy") or {}
        lines += ["", f"- Break-even fan-out observed here: copy-in memfd **k={fs.get('break_even_fanout')}**, in-place production **k={fs.get('break_even_fanout_inplace')}**.",
                  f"- Effective `choose_handoff` thresholds: copy-in k={pol.get('k_copy_in')}, in-place k={pol.get('k_in_place')} "
                  f"(source: {pol.get('source')}; run `agent-runtime os-probe` to (re)calibrate this host, or override with "
                  "AGENT_FD_MIN_FANOUT_COPYIN / AGENT_FD_MIN_FANOUT_INPLACE)."]
    pm = p.get("policy_mix") or {}
    if pm.get("adaptive"):
        lines += [f"- Pipeline mix {pm['mix']}: always-copy {pm['always_copy']['total_ms']:.1f} ms / {pm['always_copy']['socket_bytes']:,.0f} B; "
                  f"always-fd {pm['always_fd']['total_ms']:.1f} ms; adaptive {pm['adaptive']['total_ms']:.1f} ms / {pm['adaptive']['socket_bytes']:,.0f} B; "
                  f"**adaptive + in-place {pm['adaptive_inplace']['total_ms']:.1f} ms / {pm['adaptive_inplace']['socket_bytes']:,.0f} B**."]
    lines += [f"- Producer write after sealing rejected by the kernel (TOCTOU protection): **{p['sealed_write_rejected']}**.",
              f"- Method: {p['method']}.", "", f"Source SHA-256: `{p['env']['source_tree_sha256']}`", ""]
    return "\n".join(lines)


def run_fd_passing_cli(args) -> int:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _write_checksum_manifest

    p = run_fd_passing_benchmark(repeat=getattr(args, "repeat", 30))
    out = _allocate_result_dir("results", "fd_passing")
    (out / "summary.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_fd_passing(p), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({"suite": "fd_passing", "environment": p.get("env")}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_checksum_manifest(out)
    print(render_fd_passing(p))
    print(f"\n结果已保存到: {out}")
    if p.get("supported"):
        return 0
    import sys as _sys

    # Unsupported on a non-Linux host is an environment fact (recorded in summary.json), not a
    # failure; on Linux it means the kernel lacks memfd sealing/SCM_RIGHTS and stays an error.
    return 2 if _sys.platform.startswith("linux") else 0


__all__ = ["calibrate_fd_policy", "load_calibration", "policy_thresholds", "calibration_path",
           "probe_fd_capabilities", "choose_handoff", "DEFAULT_FD_HANDOFF_MIN_BYTES", "SealedSegment", "VerifierProcess", "fd_passing_supported", "verify_sealed_fd",
           "run_fd_passing_benchmark", "render_fd_passing", "run_fd_passing_cli"]
