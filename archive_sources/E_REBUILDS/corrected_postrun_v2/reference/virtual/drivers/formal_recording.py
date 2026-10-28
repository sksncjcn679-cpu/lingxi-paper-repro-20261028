"""Append-only recording and parent-side AF_UNIX accounting for the frozen experiment.

No RPC contents are retained: config snapshots crossing AgentHost may contain credentials.
Each socket is observed at the Runtime endpoint only, so the peer's read/write is not added.
"""
from __future__ import annotations
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import psutil


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(value).hexdigest()


class Recorder:
    def __init__(self, out, common, secret="", enabled=True):
        self.out = Path(out)
        self.common = common
        self.secret = secret
        self.enabled = enabled
        self.owner = os.getpid()
        self.records = []
        self.spans = []
        self.local = threading.local()
        self.lock = threading.RLock()
        self.next_span = 0
        self.flush_sec = 0.0
        (self.out / 'raw').mkdir(parents=True, exist_ok=True)

    def add(self, name, row, durable=False):
        if os.getpid() != self.owner or not self.enabled:
            return
        item = {**self.common, **row}
        if durable:
            self.write(name, [item], fsync=True)
        else:
            with self.lock:
                self.records.append((name, item))

    def write(self, name, items, fsync=False):
        started = time.perf_counter()
        path = self.out / "raw" / name
        with self.lock, path.open("a", encoding="utf-8") as stream:
            for row in items:
                serialized = json.dumps(row, ensure_ascii=False, default=str)
                if self.secret:
                    serialized = serialized.replace(self.secret, "[REDACTED]")
                stream.write(serialized + "\n")
            stream.flush()
            if fsync:
                os.fsync(stream.fileno())
        self.flush_sec += time.perf_counter() - started

    @contextmanager
    def span(self, category, operation, **extra):
        if os.getpid() != self.owner or not self.enabled:
            yield
            return
        stack = getattr(self.local, "stack", [])
        with self.lock:
            self.next_span += 1
            identity = self.next_span
        row = {"span_id": identity, "parent_span_id": stack[-1] if stack else None,
               "category": category, "operation": operation, "pid": os.getpid(),
               "thread": threading.get_ident(), "depth": len(stack),
               "start_ns": time.perf_counter_ns(), **extra}
        self.local.stack = stack + [identity]
        try:
            yield
        except BaseException as error:
            row["error_type"] = type(error).__name__
            raise
        finally:
            row["end_ns"] = time.perf_counter_ns()
            row["inclusive_sec"] = (row["end_ns"] - row["start_ns"]) / 1e9
            self.local.stack = stack
            with self.lock:
                self.spans.append(row)

    def partition(self, start_ns, end_ns):
        # Single Runtime execution thread. Child work is nested inside AgentHost.run and
        # never added to this partition. Resource sampler spans are deliberately excluded.
        spans = [s for s in self.spans if s["thread"] == threading.get_ident()]
        points = sorted({start_ns, end_ns, *[max(start_ns, min(end_ns, s[k]))
                         for s in spans for k in ("start_ns", "end_ns")]})
        result = Counter()
        for left, right in zip(points, points[1:]):
            covering = [s for s in spans if s["start_ns"] <= left and s["end_ns"] >= right]
            selected = max(covering, key=lambda s: (s["depth"], s["span_id"])) if covering else None
            result[selected["category"] if selected else "runtime_and_recording_other"] += (right-left)/1e9
        return dict(result)

    def flush(self):
        with self.lock:
            grouped = {}
            for name, item in self.records:
                grouped.setdefault(name, []).append(item)
            self.records.clear()
        if self.spans:
            grouped["spans.jsonl"] = [{**self.common, **s} for s in self.spans]
        for name, items in grouped.items():
            self.write(name, items)


class ResourceSampler:
    def __init__(self, recorder, role_pids=None):
        self.recorder = recorder
        self.role_pids = role_pids or (lambda: {})
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.peak_rss = 0
        self.peak_fds = 0

    def sample(self):
        root = psutil.Process()
        while not self.stop.is_set():
            by_pid = {pid: role for role, pid in self.role_pids().items()}
            values = []
            for process in [root] + root.children(recursive=True):
                try:
                    values.append({"pid": process.pid,
                        "role": "runtime" if process.pid == root.pid else by_pid.get(process.pid, "router_or_helper"),
                        "rss": process.memory_info().rss, "cpu_sec": sum(process.cpu_times()[:2]),
                        "fds": process.num_fds(), "io": read_io(process.pid)})
                except (psutil.Error, OSError):
                    continue
            rss, fds = sum(v["rss"] for v in values), sum(v["fds"] for v in values)
            self.peak_rss, self.peak_fds = max(rss, self.peak_rss), max(fds, self.peak_fds)
            self.recorder.add("resource_samples.jsonl", {"timestamp": now(), "monotonic_ns": time.perf_counter_ns(),
                "sample_interval_sec": .2, "processes": values, "loadavg": os.getloadavg(),
                "container_memory_current": read_counter("/sys/fs/cgroup/memory.current"),
                "rss_semantics": "sum includes shared pages per process; not proportional set size"})
            self.stop.wait(.2)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


def read_counter(path):
    try:
        return int(Path(path).read_text())
    except (ValueError, OSError):
        return None


def read_io(pid):
    try:
        return {key: int(value.strip()) for key, value in
                (line.split(':', 1) for line in Path(f'/proc/{pid}/io').read_text().splitlines())}
    except (ValueError, OSError):
        return None


class _ObservedSocket:
    def __init__(self, raw, observer):
        self.raw, self.observer = raw, observer

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def sendmsg(self, buffers, *args):
        size = self.raw.sendmsg(buffers, *args)
        self.observer.event(self.raw, "send", size, b"".join(bytes(v) for v in buffers)[:size])
        return size

    def sendall(self, data, *args):
        # Native main always uses sendmsg; successful fallback sendall is still exact.
        result = self.raw.sendall(data, *args)
        self.observer.event(self.raw, "send", len(data), data)
        return result

    def recv(self, size, *args):
        data = self.raw.recv(size, *args)
        self.observer.event(self.raw, "recv", len(data), data)
        return data


class SocketObserver:
    """Observe native framing helpers, including negotiation, ACKs and AgentHost RPC.

    State-FD ancillary paths are disabled in the main configuration and separately measured
    in the state microbenchmark. HTTP/provider traffic and kernel ancillary bytes are excluded.
    """
    def __init__(self, recorder):
        self.recorder = recorder
        self.owner = os.getpid()
        self.proxies = {}
        self.actual = Counter()
        self.consumed = Counter()
        self.labels = {}
        self.syscalls = 0

    def identity(self, sock):
        try:
            return f"{os.fstat(sock.fileno()).st_ino}:{sock.fileno()}"
        except OSError:
            return "closed"

    def proxy(self, sock):
        # Keep proxy identity stable so native weak-key receive buffers retain prefetched data.
        key = id(sock)
        if key not in self.proxies:
            self.proxies[key] = _ObservedSocket(sock, self)
        return self.proxies[key]

    def event(self, sock, direction, size, data):
        key = self.identity(sock)
        self.actual[(key, direction)] += size
        self.syscalls += 1
        self.recorder.add("ipc_socket_events.jsonl", {"timestamp": now(), "socket": key,
            "direction": direction, "successful_bytes": size, "bytes_sha256": sha(data),
            "boundary": "Runtime endpoint observed once", "contents": "NOT_RETAINED"})

    def register(self, sock, kind, role=None, peer_pid=None):
        if sock is not None:
            self.labels[self.identity(sock)] = {"kind": kind, "role": role, "peer_pid": peer_pid}

    def __enter__(self):
        import agent_runtime.scheduler.uds as uds
        self.uds = uds
        self.original_send, self.original_recv = uds._send_all_gather, uds._recv_exact
        def send(sock, parts):
            if os.getpid() != self.owner:
                return self.original_send(sock, parts)
            with self.recorder.span("router_ipc", "framed_send"):
                result = self.original_send(self.proxy(sock), parts)
            self.consumed[(self.identity(sock), "send")] += sum(len(p) for p in parts)
            return result
        def recv(sock, n, prefetch=True):
            if os.getpid() != self.owner:
                return self.original_recv(sock, n, prefetch)
            with self.recorder.span("router_ipc", "framed_recv"):
                result = self.original_recv(self.proxy(sock), n, prefetch)
            self.consumed[(self.identity(sock), "recv")] += len(result)
            return result
        uds._send_all_gather, uds._recv_exact = send, recv
        return self

    def totals(self):
        output = Counter()
        for (key, direction), size in self.actual.items():
            label = self.labels.get(key, {"kind": "unclassified_native_framing"})
            output[label["kind"]] += size
        return {"observed_socket_bytes": sum(self.actual.values()), "by_boundary": dict(output),
            "successful_syscalls": self.syscalls,
            "framing_consumption_conserved": dict(self.actual) == dict(self.consumed),
            "endpoints": [{"socket": key, "direction": direction, "actual_bytes": size,
                "framing_consumed_bytes": self.consumed[(key,direction)],
                **self.labels.get(key, {"kind":"unclassified_native_framing"})}
                for (key, direction), size in self.actual.items()],
            "excluded": ["HTTP/provider network", "kernel bookkeeping", "ancillary FD representation",
                         "local materialization", "logical payload", "peer endpoint duplicate accounting"]}

    def __exit__(self, *_):
        self.uds._send_all_gather, self.uds._recv_exact = self.original_send, self.original_recv
        self.recorder.add("ipc_accounting.jsonl", self.totals())
