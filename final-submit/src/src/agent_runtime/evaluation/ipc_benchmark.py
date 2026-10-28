"""跨进程状态传递 microbenchmark。

目标不是证明共享内存在所有 payload 上都更快，而是把两种本机进程间传递路径拆开量化：

* ``copy``：NumPy payload 通过 pickle 序列化后完整写入同一条 ``Pipe``；
* ``shm``：payload 已由 owner 物化在共享内存中，Pipe 只发送 shape/dtype/name 描述符，
  consumer attach 后以只读视图计算校验和。

报告同时记录一次性共享内存物化成本与 transfer-only round-trip，避免把初始化成本藏掉。
小 payload 上共享内存可能没有时延优势；此结果应如实保留。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import multiprocessing as mp
import pickle
from pathlib import Path
import statistics
import struct
import time
from typing import Any

import numpy as np

from agent_runtime.evaluation.runner import (
    _allocate_result_dir,
    _env_info,
    _write_checksum_manifest,
)
from agent_runtime.state_exchange.shared_buffer import SharedArrayDescriptor, SharedVectorBuffer


_STOP = b"Q"
_COPY = b"P"
_SHM = b"S"


def _consumer(conn: Any) -> None:
    """Persistent worker used by the benchmark; top-level for spawn portability."""
    try:
        while True:
            blob = conn.recv_bytes()
            if blob == _STOP:
                return
            mode, body = blob[:1], blob[1:]
            if mode == _COPY:
                arr = pickle.loads(body)  # noqa: S301 - trusted benchmark payload
                value = float(np.asarray(arr).sum(dtype=np.float64))
            elif mode == _SHM:
                raw = json.loads(body.decode("utf-8"))
                desc = SharedArrayDescriptor(
                    name=str(raw["name"]),
                    shape=tuple(int(x) for x in raw["shape"]),
                    dtype=str(raw["dtype"]),
                    nbytes=int(raw["nbytes"]),
                )
                with SharedVectorBuffer.attach_readonly(desc) as view:
                    value = float(view.sum(dtype=np.float64))
            else:
                raise ValueError(f"unknown IPC benchmark mode: {mode!r}")
            conn.send_bytes(struct.pack("!d", value))
    finally:
        conn.close()


def _roundtrip(conn: Any, blob: bytes) -> tuple[float, float]:
    start = time.perf_counter_ns()
    conn.send_bytes(blob)
    reply = conn.recv_bytes()
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000.0
    (value,) = struct.unpack("!d", reply)
    return elapsed_ms, float(value)


def _percentile(samples: list[float], q: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return float(ordered[idx])


def _latency_stats(samples: list[float]) -> dict[str, float]:
    return {
        "mean_ms": float(statistics.fmean(samples)) if samples else 0.0,
        "median_ms": float(statistics.median(samples)) if samples else 0.0,
        "p95_ms": _percentile(samples, 0.95),
        "min_ms": min(samples) if samples else 0.0,
        "max_ms": max(samples) if samples else 0.0,
    }


def _descriptor_blob(desc: SharedArrayDescriptor) -> bytes:
    body = json.dumps(asdict(desc), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return _SHM + body


def run_ipc_benchmark(
    *,
    sizes: tuple[int, ...] = (1024, 64 * 1024, 1024 * 1024),
    repeat: int = 30,
    warmup: int = 5,
) -> dict[str, Any]:
    """Run copy-vs-shared-memory round-trip measurements in a spawned worker.

    ``sizes`` are logical payload bytes and must be positive multiples of float32 itemsize.
    The same persistent consumer handles both paths, so process startup is excluded from both.
    """
    if repeat <= 0 or warmup < 0:
        raise ValueError("repeat must be > 0 and warmup must be >= 0")
    for size in sizes:
        if size <= 0 or size % np.dtype(np.float32).itemsize:
            raise ValueError("IPC payload sizes must be positive multiples of 4 bytes")

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=True)
    worker = ctx.Process(target=_consumer, args=(child,), daemon=True)
    worker.start()
    child.close()
    cases: list[dict[str, Any]] = []
    try:
        for payload_bytes in sizes:
            count = payload_bytes // np.dtype(np.float32).itemsize
            arr = np.arange(count, dtype=np.float32)
            expected_sum = float(arr.sum(dtype=np.float64))
            copy_blob = _COPY + pickle.dumps(arr, protocol=5)

            buf = SharedVectorBuffer(use_shared_memory=True)
            materialize_start = time.perf_counter_ns()
            materialized_bytes = buf.put("payload", arr)
            materialize_ms = (time.perf_counter_ns() - materialize_start) / 1_000_000.0
            if buf.degraded:
                buf.close()
                raise RuntimeError("shared_memory backend unavailable; IPC benchmark cannot run")
            desc = buf.descriptor("payload")
            assert desc is not None
            shm_blob = _descriptor_blob(desc)

            # Alternate modes during warmup to avoid giving only one path the cold-start penalty.
            for i in range(warmup):
                for blob in ((copy_blob, shm_blob) if i % 2 == 0 else (shm_blob, copy_blob)):
                    _, value = _roundtrip(parent, blob)
                    if value != expected_sum:
                        raise AssertionError("IPC warmup checksum mismatch")

            copy_samples: list[float] = []
            shm_samples: list[float] = []
            for i in range(repeat):
                order = (("copy", copy_blob), ("shm", shm_blob))
                if i % 2:
                    order = tuple(reversed(order))
                for name, blob in order:
                    elapsed, value = _roundtrip(parent, blob)
                    if value != expected_sum:
                        raise AssertionError(f"IPC {name} checksum mismatch")
                    (copy_samples if name == "copy" else shm_samples).append(elapsed)

            copy_stats = _latency_stats(copy_samples)
            shm_stats = _latency_stats(shm_samples)
            delta_ms = copy_stats["median_ms"] - shm_stats["median_ms"]
            break_even = (
                int(math.ceil(materialize_ms / delta_ms))
                if delta_ms > 0
                else None
            )
            amortized: dict[str, dict[str, float | None]] = {}
            for transfers in (1, 4, 16):
                copy_total = copy_stats["median_ms"] * transfers
                shm_total = materialize_ms + shm_stats["median_ms"] * transfers
                amortized[str(transfers)] = {
                    "copy_ms": copy_total,
                    "shm_ms": shm_total,
                    "shm_improvement": 1.0 - shm_total / copy_total if copy_total else None,
                }

            cases.append(
                {
                    "payload_bytes": payload_bytes,
                    "elements": count,
                    "dtype": "float32",
                    "checksum": expected_sum,
                    "copy": {
                        "wire_bytes": len(copy_blob),
                        "latency": copy_stats,
                    },
                    "shm": {
                        "control_wire_bytes": len(shm_blob),
                        "materialized_bytes": materialized_bytes,
                        "materialize_ms": materialize_ms,
                        "latency": shm_stats,
                    },
                    "derived": {
                        "transfer_wire_reduction": 1.0 - len(shm_blob) / len(copy_blob),
                        "transfer_median_latency_improvement": (
                            1.0 - shm_stats["median_ms"] / copy_stats["median_ms"]
                            if copy_stats["median_ms"]
                            else None
                        ),
                        "break_even_transfers": break_even,
                        "amortized": amortized,
                    },
                }
            )
            buf.close()
    finally:
        try:
            parent.send_bytes(_STOP)
        except (BrokenPipeError, EOFError, OSError):
            pass
        parent.close()
        worker.join(timeout=5)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=2)
        if worker.exitcode not in (0, None):
            raise RuntimeError(f"IPC benchmark worker exited with code {worker.exitcode}")

    return {
        "suite": "ipc",
        "repeat": repeat,
        "warmup": warmup,
        "env": _env_info(),
        "methodology": {
            "copy": "pickle protocol 5 payload sent via multiprocessing Pipe",
            "shm": "owner materializes once; only SharedArrayDescriptor is sent via same Pipe; consumer attaches read-only",
            "latency_scope": "persistent-worker request/ack round-trip; process startup excluded",
            "materialization_scope": "reported separately and included in amortized totals",
            "caution": "microbenchmark; latency depends on OS, Python build, CPU load, and payload size",
        },
        "cases": cases,
    }


def render_ipc_report(payload: dict[str, Any]) -> str:
    lines = [
        "# IPC 状态传递专项实验",
        "",
        f"- repeat: {payload['repeat']} / warmup: {payload['warmup']}",
        f"- Python: {payload['env']['python']} / {payload['env']['platform']}",
        f"- source SHA-256: {payload['env']['source_tree_sha256']}",
        "- 说明：共享内存初次物化成本单列；transfer latency 不含进程启动。",
        "",
        "| Payload | Copy wire B | SHM control B | Wire降低 | Copy median ms | SHM median ms | Transfer latency改善 | SHM materialize ms | Break-even transfers |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for case in payload["cases"]:
        derived = case["derived"]
        lat = derived["transfer_median_latency_improvement"]
        lat_text = f"{lat:.1%}" if lat is not None else "N/A"
        be = derived["break_even_transfers"]
        lines.append(
            f"| {case['payload_bytes']} | {case['copy']['wire_bytes']} | "
            f"{case['shm']['control_wire_bytes']} | {derived['transfer_wire_reduction']:.1%} | "
            f"{case['copy']['latency']['median_ms']:.4f} | "
            f"{case['shm']['latency']['median_ms']:.4f} | {lat_text} | "
            f"{case['shm']['materialize_ms']:.4f} | {be if be is not None else 'N/A'} |"
        )
    lines += [
        "",
        "> 注意：若小 payload 的共享内存时延更差，应保留该结果；该机制的首要收益是减少重复 payload 拷贝/序列化，时延收益存在尺寸与复用次数拐点。",
        "",
    ]
    return "\n".join(lines)


def write_ipc_results(payload: dict[str, Any], root: str = "results") -> Path:
    out_dir = _allocate_result_dir(root, "ipc")
    (out_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_ipc_report(payload), encoding="utf-8")
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "suite": "ipc",
        "repeat": payload["repeat"],
        "warmup": payload["warmup"],
        "environment": payload["env"],
        "methodology": payload["methodology"],
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_checksum_manifest(out_dir)
    return out_dir


def run_ipc_benchmark_cli(args: Any) -> int:
    sizes = tuple(int(part.strip()) for part in str(args.sizes).split(",") if part.strip())
    payload = run_ipc_benchmark(sizes=sizes, repeat=args.repeat, warmup=args.warmup)
    out_dir = write_ipc_results(payload)
    print(render_ipc_report(payload))
    print(f"\n结果已保存到: {out_dir}")
    return 0


__all__ = [
    "run_ipc_benchmark",
    "write_ipc_results",
    "render_ipc_report",
    "run_ipc_benchmark_cli",
]
