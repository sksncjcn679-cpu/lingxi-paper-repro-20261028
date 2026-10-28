"""Warm fork-server for CodeAct sandbox executions (v0.6.5).

Why
---
The cold sandbox path starts a fresh ``python -I -S`` launcher, applies rlimits and then
``execve``s a *second* isolated interpreter.  On the contest workload that costs ~35-40 ms per
execution, which is almost the entire per-task latency.  The fork-server pays interpreter
start-up once and then ``fork()``s a child per execution (the "zygote" pattern).

Isolation contract (unchanged versus the cold path)
---------------------------------------------------
* the server itself is ``python -I -S`` started with the *same* whitelisted environment,
  in its own session, holding no agent/runtime state and importing only stdlib modules;
* every child: ``setsid()``; private temp work directory; stdin/stdout/stderr redirected to
  files; every inherited descriptor >= 3 closed (including the server's control pipes);
  RLIMIT_CPU/AS/FSIZE/NOFILE/NPROC applied before user code runs;
* timeout kills the child's whole process group; after a normal exit the group is also
  killed so background grandchildren cannot outlive the execution;
* CPU accounting is exact: a forked child starts with zero CPU time.

Any server-side failure (spawn, protocol, crash, hang) makes the client fall back to the cold
path for that execution and retire the server; correctness never depends on the warm path.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from agent_runtime.sandbox.kernel_guard import GUARD_SOURCE as _GUARD_SOURCE

_SERVER_SOURCE = r'''
import os, sys, json, struct, resource, time, tempfile, shutil, traceback, signal, io, builtins
import json as _preloaded_json, math as _preloaded_math  # warm common stdlib for user code
IN = os.dup(0); OUT = os.dup(1)
_null = os.open(os.devnull, os.O_RDWR); os.dup2(_null, 0); os.dup2(2, 1)
MAX_OUT = int(sys.argv[1]) if len(sys.argv) > 1 else 65536
# fable12: all CodeAct workdirs live under one private per-server root so a single Landlock
# ruleset (read-only runtime trees + read-write root) is built ONCE here; each child then pays
# one landlock_restrict_self() instead of building a ruleset (~0.5 ms).
try:
    _KG["root"] = tempfile.mkdtemp(prefix="codeact_root_")
    tempfile.tempdir = _KG["root"]
    import atexit as _atexit
    _atexit.register(shutil.rmtree, _KG["root"], True)
    _RO = json.loads(os.environ.get("AGENT_SANDBOX_RO_JSON", "[]") or "[]")
    _KG["protect_pid"] = os.getpid()
    _kernel_guard_prepare(_RO)
    # fable14: confine the fork-server ITSELF once.  seccomp filters and Landlock domains are
    # inherited across fork(), so every child starts already confined and pays nothing per
    # execution (installing a seccomp filter costs ~0.3 ms, mostly BPF JIT; a Landlock domain
    # ~0.25 ms).  The server only needs what it grants its children anyway.
    _layers = tuple(x for x in os.environ.get("AGENT_SANDBOX_GUARD_LAYERS", "").split(",") if x)
    # A global signal-denial fallback on ABI <6 must apply to untrusted workers,
    # not the trusted supervisor, which needs killpg() for timeouts and cleanup.
    # Only ABI >=6 permits confining the supervisor and managing its own children.
    if _layers and _KG.get("abi", 0) >= 6:
        _KG["server_guard"] = _kernel_guard(_KG["root"], _RO, _layers)
        _KG["server_layers"] = _layers
except NameError:
    pass

def _read_exact(n):
    buf = b""
    while len(buf) < n:
        chunk = os.read(IN, n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def _send(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    os.write(OUT, struct.pack(">I", len(data)) + data)

def _cap(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read(MAX_OUT)
    except OSError:
        return "", False
    text = data.decode("utf-8", errors="ignore")
    if size > MAX_OUT:
        return text + "\n\u2026[truncated]", True
    return text, False

def _cap_fd(fd):
    size = os.fstat(fd).st_size
    data = os.pread(fd, MAX_OUT, 0)
    text = data.decode("utf-8", errors="ignore")
    if size > MAX_OUT:
        return text + "\n\u2026[truncated]", True
    return text, False

def _child(req, workdir):
    code = 1
    try:
        os.setsid()
        os.chdir(workdir)
        if req.get("_fds"):
            fi, fo, fe = req["_fds"]
        else:
            fi = os.open("stdin.txt", os.O_RDONLY)
            fo = os.open("stdout.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            fe = os.open("stderr.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.dup2(fi, 0); os.dup2(fo, 1); os.dup2(fe, 2)
        _keep = _KG.get("ruleset_fd") if "_KG" in globals() else None
        if _keep is None:
            os.closerange(3, 65536)
        else:
            os.closerange(3, _keep); os.closerange(_keep + 1, 65536)
        cpu = max(1, int(req["cpu_sec"])); mem = int(req["mem_mb"]) * 1024 * 1024
        for res, val in ((resource.RLIMIT_CPU, (cpu, cpu + 1)), (resource.RLIMIT_AS, (mem, mem)),
                         (resource.RLIMIT_FSIZE, (16 << 20, 16 << 20)), (resource.RLIMIT_NOFILE, (64, 64))):
            try:
                resource.setrlimit(res, val)
            except (ValueError, OSError):
                pass
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError, AttributeError):
            pass
        pre = req.get("_stdio")
        if pre is not None:
            # fable8: stdio wrappers prepared in the warm server before fork (never used there,
            # so their buffers are empty); they refer to fd numbers 0/1/2, which were just
            # dup2'ed to this child's own files.  Saves ~0.17 ms of wrapper setup per exec.
            sys.stdin, sys.stdout, sys.stderr = pre
        else:
            sys.stdin = io.TextIOWrapper(io.FileIO(0, "r", closefd=False), encoding="utf-8")
            sys.stdout = io.TextIOWrapper(io.FileIO(1, "w", closefd=False), encoding="utf-8", write_through=True)
            sys.stderr = io.TextIOWrapper(io.FileIO(2, "w", closefd=False), encoding="utf-8",
                                          errors="backslashreplace", write_through=True)
        guard = req.get("guard") or ()
        inherited = _KG.get("server_guard") if "_KG" in globals() else None
        if guard and inherited and set(guard) <= set(_KG.get("server_layers", ())) and (
            ("seccomp" not in guard or inherited.get("seccomp")) and ("landlock" not in guard or inherited.get("landlock_abi"))
        ):
            guard = ()  # already confined by the inherited server-level guard
        if guard:
            # fable12 kernel guard (seccomp-BPF + Landlock); report for the Runtime.
            rep = _kernel_guard(workdir, req.get("ro") or [], tuple(guard))
            ok = rep.get("no_new_privs") and (("seccomp" not in guard) or rep.get("seccomp")) and (
                ("landlock" not in guard) or rep.get("landlock_abi"))
            if not ok:
              try:
                gfd = os.open("guard.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.write(gfd, json.dumps(rep).encode())
                os.close(gfd)
              except OSError:
                pass
        sys.argv = ["main.py"]
        g = {"__name__": "__main__", "__file__": "main.py", "__builtins__": builtins}
        try:
            codeobj = req.get("_codeobj")
            if codeobj is None:
                with open("main.py", "r", encoding="utf-8") as f:
                    codeobj = compile(f.read(), "main.py", "exec")
            exec(codeobj, g)
            code = 0
        except SystemExit as exc:
            c = exc.code
            if c is None:
                code = 0
            elif isinstance(c, int):
                code = c & 0xFF
            else:
                print(c, file=sys.stderr); code = 1
        except BaseException:
            traceback.print_exc(); code = 1
        for s in (sys.stdout, sys.stderr):
            try:
                s.flush()
            except Exception:
                pass
    finally:
        os._exit(code)

def _wait(pid, timeout):
    deadline = time.monotonic() + timeout
    pidfd = None
    if hasattr(os, "pidfd_open"):
        try:
            pidfd = os.pidfd_open(pid)
        except OSError:
            pidfd = None
    try:
        delay = 0.0005
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if pidfd is not None:
                import select as _sel
                r, _, _ = _sel.select([pidfd], [], [], remaining)
                if r:
                    return True
                continue
            info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if info is not None and info.si_pid == pid:
                return True
            time.sleep(min(delay, max(0.0, remaining))); delay = min(delay * 2, 0.005)
    finally:
        if pidfd is not None:
            os.close(pidfd)

_CODE_CACHE = {}

def _precompile(src):
    # fable7: compile in the warm server BEFORE fork.  Measured on the dev host, compile() inside
    # a freshly forked child costs ~1.6 ms (first-touch of parser/compiler arenas after fork)
    # versus ~17 us in the warm server, i.e. it dominated the whole warm execution.  Compiling
    # never runs user code; a SyntaxError is left to the child so the error path is unchanged.
    key = hash(src)
    hit = _CODE_CACHE.get(key)
    if hit is not None and hit[0] == src:
        return hit[1]
    try:
        obj = compile(src, "main.py", "exec")
    except (SyntaxError, ValueError):
        return None
    if len(_CODE_CACHE) >= 128:
        _CODE_CACHE.pop(next(iter(_CODE_CACHE)))
    _CODE_CACHE[key] = (src, obj)
    return obj

_STDIO = None

def _prepared_stdio():
    global _STDIO
    if _STDIO is None:
        _STDIO = (
            io.TextIOWrapper(io.FileIO(0, "r", closefd=False), encoding="utf-8"),
            io.TextIOWrapper(io.FileIO(1, "w", closefd=False), encoding="utf-8", write_through=True),
            io.TextIOWrapper(io.FileIO(2, "w", closefd=False), encoding="utf-8",
                             errors="backslashreplace", write_through=True),
        )
    return _STDIO

def _handle(req):
    req["_codeobj"] = _precompile(req["code"]) if req.get("precompile", True) else None
    req["_stdio"] = _prepared_stdio() if req.get("precompile", True) else None
    workdir = tempfile.mkdtemp(prefix="codeact_")
    try:
        with open(os.path.join(workdir, "main.py"), "w", encoding="utf-8") as f:
            f.write(req["code"])
        # fable16: stdin/stdout/stderr live in anonymous memfds (no directory entries to create,
        # read back and unlink); the workdir keeps only main.py and whatever the program writes.
        _mfd = hasattr(os, "memfd_create")
        if _mfd:
            req["_fds"] = (os.memfd_create("stdin", os.MFD_CLOEXEC), os.memfd_create("stdout", os.MFD_CLOEXEC),
                           os.memfd_create("stderr", os.MFD_CLOEXEC))
            _sin = req.get("stdin", "").encode("utf-8")
            if _sin:
                os.write(req["_fds"][0], _sin)
                os.lseek(req["_fds"][0], 0, 0)
        else:
            with open(os.path.join(workdir, "stdin.txt"), "w", encoding="utf-8") as f:
                f.write(req.get("stdin", ""))
        start = time.perf_counter()
        pid = os.fork()
        if pid == 0:
            _child(req, workdir)
        exited = _wait(pid, float(req["timeout_sec"]))
        timed_out = not exited
        # Kill the whole group (timeout) or any background leftovers (normal exit). The
        # zombie still holds pid==pgid, so the group id cannot have been reused yet.
        for target in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
            try:
                target()
                break
            except (ProcessLookupError, PermissionError, OSError):
                continue
        _, status, ru = os.wait4(pid, 0)
        duration = time.perf_counter() - start
        exit_code = os.waitstatus_to_exitcode(status)
        if not timed_out and exit_code == -signal.SIGKILL and exited:
            # SIGKILL above hit a process that had already exited: exit status was recorded
            # before the kill, so waitstatus reflects the real code. Nothing to adjust.
            pass
        if req.get("_fds"):
            out, t1 = _cap_fd(req["_fds"][1])
            err, t2 = _cap_fd(req["_fds"][2])
        else:
            out, t1 = _cap(os.path.join(workdir, "stdout.log"))
            err, t2 = _cap(os.path.join(workdir, "stderr.log"))
        guard = req.get("guard") or ()
        guard_rep = None
        if guard:
            # The child reports only on failure; success is the prepared state it inherited.
            if _KG.get("server_guard") and set(guard) <= set(_KG.get("server_layers", ())):
                sg = _KG["server_guard"]
                guard_rep = {"seccomp": bool(sg.get("seccomp")), "landlock_abi": int(sg.get("landlock_abi", 0)),
                             "no_new_privs": bool(sg.get("no_new_privs")), "errors": list(sg.get("errors", [])),
                             "scope": "fork-server (inherited)"}
            else:
              guard_rep = {"seccomp": "seccomp" in guard and "prog" in _KG,
                         "landlock_abi": int(_KG.get("abi", 0)) if ("landlock" in guard and _KG.get("ruleset_fd") is not None) else 0,
                         "no_new_privs": True, "errors": [], "scope": "per-child"}
            try:
                with open(os.path.join(workdir, "guard.json"), "rb") as gf:
                    guard_rep = json.loads(gf.read(4096) or b"null")
            except (OSError, ValueError):
                pass
        return {"ok": True, "stdout": out, "stderr": err, "exit_code": exit_code, "guard": guard_rep,
                "timed_out": timed_out, "truncated": t1 or t2, "duration_sec": duration,
                "maxrss_kb": ru.ru_maxrss}
    finally:
        for _fd in req.pop("_fds", ()) or ():
            try:
                os.close(_fd)
            except OSError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)

while True:
    hdr = _read_exact(4)
    if hdr is None:
        break
    body = _read_exact(struct.unpack(">I", hdr)[0])
    if body is None:
        break
    try:
        req = json.loads(body.decode("utf-8"))
        if req.get("op") == "ping":
            _send({"ok": True, "pong": True, "pid": os.getpid()})
            continue
        _send(_handle(req))
    except Exception as exc:
        _send({"ok": False, "error": type(exc).__name__ + ": " + str(exc)})
'''


class ForkServerError(RuntimeError):
    """The warm path is unavailable; callers must fall back to the cold sandbox."""


def forkserver_supported() -> bool:
    return os.name == "posix" and hasattr(os, "fork") and hasattr(os, "wait4") and sys.platform.startswith("linux")


class ForkServer:
    """Client handle for one warm sandbox server process."""

    def __init__(self, env: dict[str, str], max_output_bytes: int = 64 * 1024) -> None:
        if not forkserver_supported():
            raise ForkServerError("fork-server requires Linux fork/wait4")
        self._lock = threading.Lock()
        self._env = dict(env)
        self._env["AGENT_SANDBOX_RO_JSON"] = json.dumps(_guard_ro_paths())
        self._env["AGENT_SANDBOX_GUARD_LAYERS"] = ",".join(_guard_layers())
        self._max_output = int(max_output_bytes)
        self._proc: subprocess.Popen[bytes] | None = None
        self.executions = 0
        self.restarts = 0

    # ------------------------------------------------------------------ lifecycle
    def _start(self) -> None:
        self._proc = subprocess.Popen(  # noqa: S603 - isolated interpreter, whitelisted env
            [sys.executable, "-I", "-S", "-c", _GUARD_SOURCE + _SERVER_SOURCE, str(self._max_output)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self._env,
            cwd=tempfile.gettempdir(),
            start_new_session=True,
            close_fds=True,
        )
        reply = self._roundtrip({"op": "ping"}, timeout=10.0)
        if not reply.get("pong"):
            raise ForkServerError("fork-server handshake failed")

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
        try:
            proc.wait(timeout=1.0)
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, 9)
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=1.0)
        with contextlib.suppress(Exception):
            if proc.stdout:
                proc.stdout.close()

    # ------------------------------------------------------------------ protocol
    def _roundtrip(self, req: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise ForkServerError("fork-server not running")
        data = json.dumps(req, ensure_ascii=False).encode("utf-8")
        try:
            proc.stdin.write(struct.pack(">I", len(data)) + data)
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ForkServerError(f"fork-server write failed: {exc}") from exc
        fd = proc.stdout.fileno()
        deadline = time.monotonic() + timeout

        def read_exact(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ForkServerError("fork-server response timeout")
                ready, _, _ = select.select([fd], [], [], remaining)
                if not ready:
                    continue
                chunk = os.read(fd, n - len(buf))
                if not chunk:
                    raise ForkServerError("fork-server closed the control pipe")
                buf += chunk
            return buf

        (length,) = struct.unpack(">I", read_exact(4))
        if length > 16 * 1024 * 1024:
            raise ForkServerError("fork-server frame too large")
        reply = json.loads(read_exact(length).decode("utf-8"))
        if not isinstance(reply, dict):
            raise ForkServerError("malformed fork-server reply")
        return reply

    def execute(
        self, code: str, *, stdin: str, timeout_sec: float, cpu_sec: int, mem_mb: int
    ) -> dict[str, Any]:
        with self._lock:
            try:
                if not self.alive():
                    if self._proc is not None:
                        self.restarts += 1
                        self.close()
                    self._start()
                reply = self._roundtrip(
                    {
                        "op": "exec",
                        "code": code,
                        "stdin": stdin,
                        "timeout_sec": float(timeout_sec),
                        "cpu_sec": int(cpu_sec),
                        "mem_mb": int(mem_mb),
                        "precompile": os.environ.get("AGENT_SANDBOX_PRECOMPILE", "1").strip().lower()
                        not in {"0", "false", "no", "off"},
                        "guard": _guard_layers(),
                        "ro": _guard_ro_paths(),
                    },
                    timeout=float(timeout_sec) + 5.0,
                )
            except ForkServerError:
                self.close()
                raise
            except Exception as exc:  # noqa: BLE001 - any client failure retires the server
                self.close()
                raise ForkServerError(f"{type(exc).__name__}: {exc}") from exc
            if not reply.get("ok"):
                raise ForkServerError(str(reply.get("error", "fork-server execution failed")))
            self.executions += 1
            return reply


def _guard_layers() -> list[str]:
    """fable12: kernel guard layers for CodeAct children (AGENT_SANDBOX_GUARD=0 disables)."""
    raw = os.environ.get("AGENT_SANDBOX_GUARD", "seccomp,landlock").strip().lower()
    if raw in {"0", "off", "false", "no", ""}:
        return []
    return [x for x in raw.split(",") if x in {"seccomp", "landlock"}]


_RO_CACHE: list[str] | None = None


def _guard_ro_paths() -> list[str]:
    global _RO_CACHE
    if _RO_CACHE is None:
        from agent_runtime.sandbox.kernel_guard import runtime_read_paths

        _RO_CACHE = runtime_read_paths()
    return _RO_CACHE


_POOL: dict[tuple, ForkServer] = {}
_POOL_LOCK = threading.Lock()


def get_forkserver(env: dict[str, str], max_output_bytes: int) -> ForkServer:
    """Process-wide warm server keyed by its (immutable) environment."""
    key = (tuple(sorted(env.items())), int(max_output_bytes), ",".join(_guard_layers()))
    with _POOL_LOCK:
        server = _POOL.get(key)
        if server is None:
            server = ForkServer(env, max_output_bytes)
            _POOL[key] = server
        return server


def shutdown_forkservers() -> None:
    with _POOL_LOCK:
        servers = list(_POOL.values())
        _POOL.clear()
    for server in servers:
        with contextlib.suppress(Exception):
            server.close()


atexit.register(shutdown_forkservers)

__all__ = [
    "ForkServer",
    "ForkServerError",
    "forkserver_supported",
    "get_forkserver",
    "shutdown_forkservers",
]
