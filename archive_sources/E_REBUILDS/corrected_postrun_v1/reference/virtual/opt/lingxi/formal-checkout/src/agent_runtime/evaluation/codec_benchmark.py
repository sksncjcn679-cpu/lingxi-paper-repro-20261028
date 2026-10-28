"""Structured codec benchmark: stateless compact vs Protocol-1.3 session codecs.

This suite intentionally does *not* report token savings for binary frames.  It measures
machine-to-machine wire bytes and local encode/decode latency on semantically identical
``AgentMessage`` objects.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest

from agent_runtime.protocol.message import ActionType, AgentMessage, MessageStatus
from agent_runtime.protocol.serialization import (
    decode_structured,
    decode_structured_msgpack,
    encode_structured,
    encode_structured_msgpack,
)
from agent_runtime.protocol.session_codec import SessionWireCodec


def representative_messages() -> list[AgentMessage]:
    base = dict(
        trace_id="trace_codec",
        task_id="task_codec",
        context_id="ctx_codec",
        source_agent="planner",
        target_agent="executor",
        action_type=ActionType.EXECUTE,
        capability="execute",
        status=MessageStatus.OK,
    )
    return [
        AgentMessage(step_id="s0", input_parameters={"op": "sum", "n": 3}, **base),
        AgentMessage(
            step_id="s1",
            parent_message_id="msg_parent_123456",
            causation_id="msg_parent_123456",
            sequence_no=7,
            artifact_references=[
                "artifact://task/task_codec/0123456789abcdef",
                "artifact://global/fedcba9876543210",
            ],
            state_reference="state://task_codec/0123456789abcdef",
            dependencies=["s0", "s1", "s2"],
            confidence=0.937,
            input_parameters={
                "description": "x" * 256,
                "operands": list(range(32)),
            },
            **base,
        ),
        AgentMessage(
            step_id="s2",
            sequence_no=9,
            result={
                "summary": "reviewed result with structured metadata",
                "labels": ["correct", "verified", "reusable"],
                "metrics": {"quality": 1.0, "latency_ms": 12.3},
            },
            evidence_references=[f"artifact://global/{i:016x}" for i in range(4)],
            **base,
        ),
    ]


def _median_us(fn, repeat: int) -> float:
    samples: list[float] = []
    for _ in range(repeat):
        start = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - start) / 1000.0)
    return float(statistics.median(samples))


def run_codec_benchmark(*, repeat: int = 2000) -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    messages = representative_messages()
    session_encoder = SessionWireCodec()
    session_decoder = SessionWireCodec()
    session_frames: list[tuple[str, bytes]] = []
    for message in messages:
        text, binary = session_encoder.encode_dual(message)
        session_frames.append((text, binary))
        back = session_decoder.decode_msgpack(binary)
        # Session codec is layered over the same compact semantic payload as the stateless
        # codec. Oversized inline parameters may therefore already be reference-first/summary
        # compacted before session compression. Compare against that canonical compact form,
        # not the pre-compaction application object.
        canonical = decode_structured(encode_structured(message))
        assert back.model_dump(exclude={"timestamp"}) == canonical.model_dump(exclude={"timestamp"})

    cases: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        json_wire = encode_structured(message)
        msgpack_wire = encode_structured_msgpack(message)
        session_json_wire, session_msgpack_wire = session_frames[index]
        # Both codecs must decode to the exact same compact semantic payload.
        # Compact structured mode intentionally summarizes oversized inline parameters,
        # so equality to the original pre-compaction object is not expected.
        json_back = decode_structured(json_wire).model_dump(exclude={"timestamp"})
        msgpack_back = decode_structured_msgpack(msgpack_wire).model_dump(exclude={"timestamp"})
        assert json_back == msgpack_back
        json_bytes = len(json_wire.encode("utf-8"))
        msgpack_bytes = len(msgpack_wire)
        cases.append(
            {
                "case": index,
                "json_bytes": json_bytes,
                "msgpack_bytes": msgpack_bytes,
                "byte_reduction": 1 - msgpack_bytes / json_bytes,
                "session_json_bytes": len(session_json_wire.encode("utf-8")),
                "session_msgpack_bytes": len(session_msgpack_wire),
                "session_msgpack_reduction_vs_json": 1 - len(session_msgpack_wire) / json_bytes,
                "json_encode_median_us": _median_us(lambda: encode_structured(message), repeat),
                "msgpack_encode_median_us": _median_us(
                    lambda: encode_structured_msgpack(message), repeat
                ),
                "json_decode_median_us": _median_us(lambda: decode_structured(json_wire), repeat),
                "msgpack_decode_median_us": _median_us(
                    lambda: decode_structured_msgpack(msgpack_wire), repeat
                ),
            }
        )
    total_json = sum(case["json_bytes"] for case in cases)
    total_msgpack = sum(case["msgpack_bytes"] for case in cases)
    total_session_json = sum(case["session_json_bytes"] for case in cases)
    total_session_msgpack = sum(case["session_msgpack_bytes"] for case in cases)

    def _encode_session_sequence() -> None:
        codec = SessionWireCodec()
        for msg in messages:
            codec.encode_dual(msg)

    def _decode_session_sequence() -> None:
        codec = SessionWireCodec()
        for _text, binary in session_frames:
            codec.decode_msgpack(binary)

    return {
        "suite": "codec",
        "repeat": repeat,
        "env": _env_info(),
        "cases": cases,
        "aggregate": {
            "json_bytes": total_json,
            "msgpack_bytes": total_msgpack,
            "byte_reduction": 1 - total_msgpack / total_json,
            "session_json_bytes": total_session_json,
            "session_msgpack_bytes": total_session_msgpack,
            "session_json_reduction_vs_json": 1 - total_session_json / total_json,
            "session_msgpack_reduction_vs_json": 1 - total_session_msgpack / total_json,
            "session_encode_sequence_median_us": _median_us(_encode_session_sequence, repeat),
            "session_decode_sequence_median_us": _median_us(_decode_session_sequence, repeat),
        },
    }


def write_codec_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "codec")
    (out / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    agg = payload["aggregate"]
    lines = [
        "# Structured codec benchmark",
        "",
        "> Binary MessagePack is compared only on machine wire bytes and codec latency; token metrics are not applicable.",
        "",
        f"- Source SHA-256: {payload['env']['source_tree_sha256']}",
        f"- Aggregate JSON bytes: {agg['json_bytes']}",
        f"- Aggregate MessagePack bytes: {agg['msgpack_bytes']}",
        f"- Aggregate byte reduction: {agg['byte_reduction']:.1%}",
        f"- Protocol-1.3 session JSON bytes: {agg['session_json_bytes']} ({agg['session_json_reduction_vs_json']:.1%} vs compact JSON)",
        f"- Protocol-1.3 session MessagePack bytes: {agg['session_msgpack_bytes']} ({agg['session_msgpack_reduction_vs_json']:.1%} vs compact JSON)",
        f"- Session sequence encode/decode median: {agg['session_encode_sequence_median_us']:.2f}/{agg['session_decode_sequence_median_us']:.2f} µs",
        "",
        "| case | Compact JSON B | Stateless MsgPack B | Session JSON B | Session MsgPack B | Session reduction | JSON enc µs | MsgPack enc µs |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in payload["cases"]:
        lines.append(
            f"| {case['case']} | {case['json_bytes']} | {case['msgpack_bytes']} | "
            f"{case['session_json_bytes']} | {case['session_msgpack_bytes']} | "
            f"{case['session_msgpack_reduction_vs_json']:.1%} | {case['json_encode_median_us']:.2f} | "
            f"{case['msgpack_encode_median_us']:.2f} |"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "suite": payload["suite"],
                "repeat": payload["repeat"],
                "environment": payload["env"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    return out

def run_codec_benchmark_cli(args) -> int:
    payload = run_codec_benchmark(repeat=args.repeat)
    out = write_codec_results(payload)
    print(f"Codec benchmark written to {out}")
    print(f"MessagePack byte reduction: {payload['aggregate']['byte_reduction']:.1%}")
    return 0


__all__ = ["run_codec_benchmark", "write_codec_results", "representative_messages"]
