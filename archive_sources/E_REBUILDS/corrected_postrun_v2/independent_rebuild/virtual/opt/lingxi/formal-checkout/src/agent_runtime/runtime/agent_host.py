"""fable10: one OS process per Agent ("microkernel" runtime).

Architecture
------------
The Runtime process keeps sole ownership of every shared service -- control-plane channel and
router, state exchange (SHM), versioned state ledger, artifact store, shared memory, metrics,
LLM gateway, CodeAct sandbox.  Each Agent's business logic (``BaseAgent.run`` including its
local retry loop, timeouts and circuit breaker) executes in its **own long-lived worker
process** (forked once, so it shares code pages copy-on-write).

For one stage the Orchestrator ships the inbound ``AgentMessage`` to the Agent's worker over an
``AF_UNIX`` socketpair; while the Agent runs, every access it makes to ``ctx`` is a small RPC
back to the Runtime (``getattr`` / ``setattr`` / call), answered by value for plain data and
by *reference* for live runtime objects (``ctx.blackboard``, ``ctx.metrics``,
``ctx.state_exchange`` ...), so there is exactly one copy of every piece of mutable state and
no synchronisation/merging problem.  Exceptions raised by a service are re-raised inside the
Agent, preserving its ``try/except`` semantics.

Fault isolation: if an Agent process dies (crash, ``os._exit``, OOM kill), the Runtime sees EOF,
reaps and respawns that worker and converts the stage into an explicit ``agent_process_crash``
error message, which then goes through the ordinary retry / abort path.  Other Agents and the
Runtime are unaffected.

Trust boundary: frames are pickles exchanged ONLY between the Runtime and workers it forked
itself over an unnamed socketpair (never a filesystem socket), i.e. inside one trust domain.
"""

from __future__ import annotations

import asyncio
import enum
import os
import pickle
import signal
import socket
import threading
import time
from dataclasses import is_dataclass
from typing import Any

from agent_runtime.scheduler.uds import UDSProtocolError, recv_frame, send_frame

_MAX_FRAME = 64 * 1024 * 1024


class AgentProcessCrash(RuntimeError):
    pass


# --------------------------------------------------------------------------------- value policy
_SCALARS = (type(None), bool, int, float, str, bytes, complex)


def _is_plain(value: Any, depth: int = 0) -> bool:
    """True when ``value`` may cross by value (a read-only snapshot is semantically safe)."""
    if isinstance(value, _SCALARS) or isinstance(value, enum.Enum):
        return True
    try:
        import numpy as np

        if isinstance(value, (np.ndarray, np.generic)):
            return True
    except ImportError:  # pragma: no cover
        pass
    mod = type(value).__module__ or ""
    if hasattr(value, "model_dump") and hasattr(type(value), "model_fields"):
        return True  # pydantic messages / records: value objects
    if is_dataclass(value) and not isinstance(value, type):
        params = getattr(type(value), "__dataclass_params__", None)
        return bool(params and params.frozen)
    if depth > 6:
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_is_plain(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(_is_plain(k, depth + 1) and _is_plain(v, depth + 1) for k, v in value.items())
    return mod.startswith("agent_runtime.config") and is_dataclass(value)


class _RefMarker:
    __slots__ = ("path", "callable")

    def __init__(self, path: tuple, is_callable: bool) -> None:
        self.path = path
        self.callable = is_callable


# --------------------------------------------------------------------------------- worker side
_STAGE_CACHE: dict = {}
_PENDING: list = []
_FETCHED: list = []
_MISS = object()


class RemoteObject:
    """Worker-side proxy for a live Runtime object addressed by an attribute/item path."""

    __slots__ = ("_conn", "_path", "_lock")

    def __init__(self, conn: socket.socket, path: tuple, lock: threading.Lock) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_lock", lock)

    def _rpc(self, op: str, *args: Any, deferred: bool = False) -> Any:
        item = (op, self._path, _export(args))
        if deferred:
            # Fire-and-forget effect (returns None, result unused): pipelined -- shipped with the
            # next synchronous request, applied by the Runtime strictly in program order before
            # it.  Like HTTP/2 or Redis pipelining; cuts round trips, never reorders effects.
            _PENDING.append(item)
            return None
        batch = _PENDING[:] + [item]
        _PENDING.clear()
        with self._lock:
            send_frame(self._conn, pickle.dumps(("batch", batch), protocol=5), max_frame_bytes=_MAX_FRAME)
            status, payload = pickle.loads(recv_frame(self._conn, max_frame_bytes=_MAX_FRAME))
        if status == "raise":
            raise payload
        result = _import(payload, self._conn, self._lock)
        is_get = (op == "method" and args and args[0] in ("get", "__getitem__")) or (
            op == "call" and self._path and self._path[-1][1] in ("get", "setdefault", "pop")
        )
        if is_get and isinstance(result, (dict, list)):
            # By-value snapshot of shared data: remember it so an in-place mutation (which would
            # silently diverge from the single Runtime copy) is detected at stage end.
            import copy as _copy

            _FETCHED.append((result, _copy.deepcopy(result)))
        return result

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)  # never proxy Python protocol probes (copy, pickle, ...)
        key = (self._path, name)
        hit = _STAGE_CACHE.get(key, _MISS)
        if hit is not _MISS:
            return hit
        value = self._rpc("getattr", name)
        # Per-stage cache, safe because the Runtime only changes ctx through THIS worker's RPCs
        # while the stage runs: live-object references and bound-method references are stable
        # identities; the (read-only) runtime config is a value snapshot.  A setattr on the same
        # name invalidates the entry.  Plain mutable data (blackboard values, counters) is
        # never cached.
        if isinstance(value, RemoteObject) or name == "config":
            _STAGE_CACHE[key] = value
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        _STAGE_CACHE.pop((self._path, name), None)
        self._rpc("setattr", name, value, deferred=True)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        last = self._path[-1][1] if self._path else ""
        if isinstance(last, str) and (last.startswith("record_") or last == "sample_resources"):
            return self._rpc("call", args, kwargs, deferred=True)  # metric effects return None
        return self._rpc("call", args, kwargs)

    # mapping / container protocol used by agents on ctx.blackboard and friends
    def __getitem__(self, key: Any) -> Any:
        return self._rpc("method", "__getitem__", (key,), {})

    def __setitem__(self, key: Any, value: Any) -> None:
        self._rpc("method", "__setitem__", (key, value), {}, deferred=True)

    def __delitem__(self, key: Any) -> None:
        self._rpc("method", "__delitem__", (key,), {})

    def __contains__(self, key: Any) -> bool:
        return bool(self._rpc("method", "__contains__", (key,), {}))

    def __len__(self) -> int:
        return int(self._rpc("method", "__len__", (), {}))

    def __iter__(self):
        return iter(self._rpc("method", "__iter_list__", (), {}))

    def __bool__(self) -> bool:
        return bool(self._rpc("truth"))

    def __reduce__(self):
        return (_RefMarker, (self._path, False))


def _export(value: Any) -> Any:
    """Worker -> Runtime: proxies travel as path markers."""
    if isinstance(value, RemoteObject):
        return _RefMarker(object.__getattribute__(value, "_path"), False)
    if isinstance(value, tuple):
        return tuple(_export(v) for v in value)
    if isinstance(value, list):
        return [_export(v) for v in value]
    if isinstance(value, dict):
        return {k: _export(v) for k, v in value.items()}
    return value


def _import(value: Any, conn: socket.socket, lock: threading.Lock) -> Any:
    if isinstance(value, _RefMarker):
        return RemoteObject(conn, value.path, lock)
    return value


def _worker_main(conn: socket.socket, agent: Any) -> None:  # pragma: no cover - child process
    lock = threading.Lock()
    ctx = RemoteObject(conn, (), lock)
    loop = asyncio.new_event_loop()  # one loop for the worker's lifetime (asyncio.run per stage ~0.3 ms)
    while True:
        try:
            frame = recv_frame(conn, max_frame_bytes=_MAX_FRAME)
        except Exception:
            os._exit(0)
        op, payload = pickle.loads(frame)
        if op == "stop":
            os._exit(0)
        if op == "crash_next":  # fault-injection hook used by the isolation benchmark/tests
            os.environ["AGENT_HOST_CRASH_NEXT"] = "1"
            send_frame(conn, pickle.dumps(("done", None), protocol=5))
            continue
        message = payload
        _STAGE_CACHE.clear()
        _PENDING.clear()
        _FETCHED.clear()
        try:
            if os.environ.pop("AGENT_HOST_CRASH_NEXT", None) == "1":
                os._exit(17)
            result = loop.run_until_complete(agent.run(message, ctx))
            if _PENDING:
                ctx._rpc("noop")  # flush pipelined effects before reporting completion
            for live, snapshot in _FETCHED:
                if live != snapshot:
                    raise RuntimeError(
                        "agent mutated a by-value snapshot of shared runtime state in place; "
                        "write it back through ctx (e.g. ctx.blackboard[key] = value)"
                    )
            reply = ("done", result)
        except BaseException as exc:  # noqa: BLE001 - reported to the Runtime
            reply = ("fail", exc if _picklable(exc) else RuntimeError(f"{type(exc).__name__}: {exc}"))
        with lock:
            send_frame(conn, pickle.dumps(reply, protocol=5), max_frame_bytes=_MAX_FRAME)


def _picklable(obj: Any) -> bool:
    try:
        pickle.dumps(obj)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------------- runtime side
class _Worker:
    def __init__(self, name: str, agent: Any) -> None:
        self.name = name
        self.agent = agent
        self.lock = threading.Lock()
        self.pid = 0
        self.sock: socket.socket | None = None
        self.restarts = 0
        self.start()

    def start(self) -> None:
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child
            try:
                parent.close()
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                _worker_main(child, self.agent)
            finally:
                os._exit(0)
        child.close()
        self.pid, self.sock = pid, parent

    def reap(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.waitpid(self.pid, 0)
            except OSError:
                pass
            self.pid = 0

    def stop(self) -> None:
        if self.sock is not None:
            try:
                send_frame(self.sock, pickle.dumps(("stop", None)))
            except OSError:
                pass
        self.reap()


class AgentProcessHost:
    """Runs each Agent's ``run()`` in a dedicated worker process and serves its ctx RPCs."""

    def __init__(self, agents: dict[str, Any]) -> None:
        self._objects: dict[str, Any] = {}
        self._workers = {name: _Worker(name, agent) for name, agent in agents.items()}
        self.rpc_calls = 0  # round trips
        self.rpc_ops = 0    # operations (>= round trips thanks to pipelining)
        self.crashes = 0

    def pids(self) -> dict[str, int]:
        return {name: w.pid for name, w in self._workers.items()}

    def inject_crash(self, name: str) -> None:
        w = self._workers[name]
        with w.lock:
            send_frame(w.sock, pickle.dumps(("crash_next", None)))
            recv_frame(w.sock, max_frame_bytes=_MAX_FRAME)

    def run(self, name: str, message: Any, ctx: Any) -> Any:
        w = self._workers[name]
        with w.lock:
            if w.sock is None:
                w.start()
            try:
                self._objects = {}
                send_frame(w.sock, pickle.dumps(("run", message), protocol=5), max_frame_bytes=_MAX_FRAME)
                while True:
                    msg = pickle.loads(recv_frame(w.sock, max_frame_bytes=_MAX_FRAME))
                    if msg[0] == "done":
                        return msg[1]
                    if msg[0] == "fail":
                        raise msg[1]
                    self.rpc_calls += 1
                    try:
                        result = None
                        for op, path, args in msg[1]:
                            self.rpc_ops += 1
                            result = self._serve(ctx, op, path, args)
                        reply = ("ok", result)
                    except Exception as exc:  # noqa: BLE001 - re-raised inside the Agent
                        reply = ("raise", exc if _picklable(exc) else RuntimeError(f"{type(exc).__name__}: {exc}"))
                    send_frame(w.sock, pickle.dumps(reply, protocol=5), max_frame_bytes=_MAX_FRAME)
            except (EOFError, OSError, ConnectionError, pickle.UnpicklingError, UDSProtocolError) as exc:
                self.crashes += 1
                w.restarts += 1
                w.reap()
                w.start()
                raise AgentProcessCrash(f"agent process {name} died: {type(exc).__name__}") from exc

    # ------------------------------------------------------------------ RPC service
    def _resolve(self, root: Any, path: tuple) -> Any:
        obj = root
        if path and path[0][0] == "#":
            obj = self._objects[path[0][1]]
            path = path[1:]
        for kind, key in path:
            obj = getattr(obj, key) if kind == "a" else obj[key]
        return obj

    def _wrap(self, value: Any, path: tuple) -> Any:
        if _is_plain(value):
            return value
        return _RefMarker(path, callable(value))

    def _unwrap(self, ctx: Any, value: Any) -> Any:
        if isinstance(value, _RefMarker):
            return self._resolve(ctx, value.path)
        if isinstance(value, tuple):
            return tuple(self._unwrap(ctx, v) for v in value)
        if isinstance(value, list):
            return [self._unwrap(ctx, v) for v in value]
        if isinstance(value, dict):
            return {k: self._unwrap(ctx, v) for k, v in value.items()}
        return value

    def _serve(self, ctx: Any, op: str, path: tuple, args: tuple) -> Any:
        target = self._resolve(ctx, path)
        if op == "getattr":
            (name,) = args
            value = getattr(target, name)
            if isinstance(value, (dict, list, set)):
                # Mutable container attributes of live objects (ctx.blackboard ...) are always
                # proxied: there must be exactly one copy of shared runtime state.
                return _RefMarker(path + (("a", name),), False)
            return self._wrap(value, path + (("a", name),))
        if op == "setattr":
            name, value = args
            setattr(target, name, self._unwrap(ctx, value))
            return None
        if op == "truth":
            return bool(target)
        if op == "noop":
            return None
        if op == "call":
            call_args, call_kwargs = args
            result = target(*self._unwrap(ctx, call_args), **self._unwrap(ctx, call_kwargs))
            return result if _is_plain(result) else self._stash(result)
        if op == "method":
            name, margs, mkwargs = args
            margs = self._unwrap(ctx, margs)
            if name == "__iter_list__":
                return list(target)
            result = getattr(target, name)(*margs, **self._unwrap(ctx, mkwargs))
            if name == "__getitem__" and not _is_plain(result):
                return _RefMarker(path + (("i", margs[0]),), callable(result))
            return result if _is_plain(result) else self._stash(result)
        raise ValueError(f"unknown agent-host op {op!r}")

    def _stash(self, obj: Any) -> Any:
        """Non-plain call results (live objects) become temporary references."""
        key = f"o{len(self._objects)}"
        self._objects[key] = obj
        return _RefMarker((("#", key),), callable(obj))

    def close(self) -> None:
        for w in self._workers.values():
            w.stop()


__all__ = ["AgentProcessCrash", "AgentProcessHost", "RemoteObject"]
