"""Large-state Runtime-path benchmark for copy, sealed-fd, and in-process handoff."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from agent_runtime.evaluation.runner import (
    _allocate_result_dir,
    _env_info,
    _write_checksum_manifest,
)
from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.scheduler.control_transport import UDSStateAttachTransport
from agent_runtime.state_exchange.exchange import StateExchange
from agent_runtime.state_exchange.fd_passing import VerifierProcess, fd_passing_supported


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def _run_variant(
    label: str,
    repeat: int,
    attach_factory: Callable[[], tuple[Any, ...] | None],
    *,
    dim: int,
    threshold: int,
) -> dict[str, Any]:
    latencies: list[float] = []
    handoff_latencies: list[float] = []
    socket_bytes = 0
    payload_bytes = 0
    rows: list[dict[str, Any]] = []
    # Fairness: every variant creates its channel/process ONCE (setup timed separately) and is
    # warmed up with one same-size handoff before measurement.  Earlier releases re-created the
    # copy verifier process per repeat, so process start-up leaked into the copy baseline.
    setup_start = time.perf_counter()
    attachment = attach_factory()
    setup_ms = (time.perf_counter() - setup_start) * 1000
    attach = attachment[0] if attachment is not None else None
    cleanup = attachment[1] if attachment is not None else lambda: None
    timing_source = attachment[2] if attachment is not None and len(attachment) > 2 else None
    if attach is not None:
        warm = bytes(dim * 4)
        attach(warm)
    for index in range(repeat):
        handoff_elapsed = 0.0
        handoff_socket_bytes = 0

        def timed_attach(
            payload: bytes, *, attach_fn: Callable[[bytes], tuple[bool, int]] | None = attach
        ) -> tuple[bool, int]:
            nonlocal handoff_elapsed, handoff_socket_bytes
            start = time.perf_counter()
            result = attach_fn(payload) if attach_fn is not None else (True, 0)
            handoff_elapsed += time.perf_counter() - start
            handoff_socket_bytes += int(result[1])
            return result

        exchange = StateExchange(
            MockEmbedding(dim=dim, seed=42),
            task_id=f"large-state-{label}-{index}",
            precision="float32",
            deduplicate=False,
            on_large_state=timed_attach if attach is not None else None,
            fd_handoff_min_bytes=threshold,
        )
        try:
            start = time.perf_counter()
            ref = exchange.embed_text("large state runtime workload")
            elapsed = time.perf_counter() - start
            latencies.append(elapsed)
            handoff_latencies.append(handoff_elapsed)
            payload_bytes = ref.shape[0] * 4
            socket_bytes += 0
            rows.append(
                {
                    "success": True,
                    "state_nbytes": ref.nbytes,
                    "payload_bytes": payload_bytes,
                    "handoff_socket_bytes": handoff_socket_bytes,
                    "handoff_ms": handoff_elapsed * 1000,
                    "total_ms": elapsed * 1000,
                }
            )
            if timing_source is not None:
                extra = timing_source()
                rows[-1].update(extra)
            socket_bytes += handoff_socket_bytes
        except Exception as exc:  # noqa: BLE001 - negative evidence is part of the report
            rows.append({"success": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            exchange.close()
    cleanup()
    successful = [row for row in rows if row.get("success")]
    serialize_samples = [float(row.get("serialize_ms", 0.0)) for row in successful]
    socket_samples = [float(row.get("socket_ms", row.get("handoff_ms", 0.0))) for row in successful]
    return {
        "label": label,
        "repeat": repeat,
        "channel_setup_ms": setup_ms,
        "success_rate": len(successful) / max(1, repeat),
        "state_payload_bytes": payload_bytes,
        "socket_bytes_total": socket_bytes,
        "handoff_p50_ms": _percentile(handoff_latencies, 0.5) * 1000,
        "handoff_p95_ms": _percentile(handoff_latencies, 0.95) * 1000,
        "total_p50_ms": _percentile(latencies, 0.5) * 1000,
        "total_p95_ms": _percentile(latencies, 0.95) * 1000,
        "serialize_p50_ms": _percentile(serialize_samples, 0.5) if serialize_samples else 0.0,
        "socket_p50_ms": _percentile(socket_samples, 0.5) if socket_samples else 0.0,
        "rows": rows,
    }


def run_state_fd_handoff_benchmark(*, repeat: int = 5, dim: int = 300_000) -> dict[str, Any]:
    """Run the same large state through all three Runtime attachment policies."""
    threshold = 1 << 20

    def no_attachment():
        return None

    def copy_attachment():
        verifier = VerifierProcess()
        latest: dict[str, float] = {"serialize_ms": 0.0, "socket_ms": 0.0}

        def attach(payload: bytes) -> tuple[bool, int]:
            ok, sent, serialize_ms, socket_ms = verifier.handoff_copy_timed(payload, raw=False, verify=True)
            latest.update(serialize_ms=serialize_ms, socket_ms=socket_ms)
            return ok, sent

        return attach, verifier.close, lambda: dict(latest)

    def raw_copy_attachment():
        verifier = VerifierProcess()
        latest: dict[str, float] = {"serialize_ms": 0.0, "socket_ms": 0.0}

        def attach(payload: bytes) -> tuple[bool, int]:
            ok, sent, serialize_ms, socket_ms = verifier.handoff_copy_timed(payload, raw=True, verify=False)
            latest.update(serialize_ms=serialize_ms, socket_ms=socket_ms)
            return ok, sent

        return attach, verifier.close, lambda: dict(latest)

    def fd_attachment():
        transport = UDSStateAttachTransport()

        def attach(payload: bytes) -> tuple[bool, int]:
            return transport.attach_bytes(payload)

        return attach, transport.close

    variants: dict[str, Any] = {
        "inproc": _run_variant("inproc", repeat, no_attachment, dim=dim, threshold=threshold),
        "copy_socket": _run_variant("copy_socket", repeat, copy_attachment, dim=dim, threshold=threshold),
        "raw_copy_socket": _run_variant("raw_copy_socket", repeat, raw_copy_attachment, dim=dim, threshold=threshold),
    }
    if fd_passing_supported():
        variants["sealed_fd_uds"] = _run_variant(
            "sealed_fd_uds", repeat, fd_attachment, dim=dim, threshold=threshold
        )
    else:
        variants["sealed_fd_uds"] = {"label": "sealed_fd_uds", "supported": False}
    return {
        "suite": "large_state_workload",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "StateExchange.embed_text with identical float32 payload; copy_socket includes JSON/hash metadata, raw_copy_socket sends the same raw bytes with a 4-byte frame length, sealed_fd_uds uses UDS + sealed memfd + SCM_RIGHTS, inproc has no external attachment",
        "repeat": repeat,
        "embedding_dim": dim,
        "fd_handoff_min_bytes": threshold,
        "env": _env_info(),
        "variants": variants,
    }


def render_state_fd_handoff(p: dict[str, Any]) -> str:
    lines = [
        "# Large-state Runtime workload",
        "",
        "| Variant | Success | Payload B | Socket B | Serialize p50 ms | Socket p50 ms | Handoff p50 ms | Total p50 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, row in p["variants"].items():
        if row.get("supported") is False:
            lines.append(f"| {label} | unavailable | - | - | - | - |")
            continue
        lines.append(
            f"| {label} | {row['success_rate']:.0%} | {row['state_payload_bytes']:,} | "
            f"{row['socket_bytes_total']:,} | {row.get('serialize_p50_ms', 0.0):.3f} | {row.get('socket_p50_ms', 0.0):.3f} | "
            f"{row['handoff_p50_ms']:.3f} | {row['total_p50_ms']:.3f} |"
        )
    lines += ["", f"Source SHA-256: `{p['env']['source_tree_sha256']}`", ""]
    return "\n".join(lines)


def run_state_fd_handoff_cli(args) -> int:
    p = run_state_fd_handoff_benchmark(
        repeat=getattr(args, "repeat", 5), dim=getattr(args, "dim", 300_000)
    )
    out = _allocate_result_dir("results", "large_state_workload")
    (out / "summary.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_state_fd_handoff(p), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps({"suite": p["suite"], "environment": p["env"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    print(render_state_fd_handoff(p))
    print(f"\n结果已保存到: {out}")
    return 0 if p["variants"].get("sealed_fd_uds", {}).get("success_rate", 0) == 1 else 2


__all__ = ["run_state_fd_handoff_benchmark", "render_state_fd_handoff", "run_state_fd_handoff_cli"]
