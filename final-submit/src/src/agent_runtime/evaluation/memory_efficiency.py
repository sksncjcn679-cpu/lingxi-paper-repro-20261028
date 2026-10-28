"""Isolated equal-success memory reuse efficiency benchmark.

Unlike the capability M0/M1 suite (where the no-memory arm intentionally fails on A2/B2),
this suite runs two identical successful tasks in both arms and toggles only shared memory.
v0.7.0 additionally exercises the exact-input Memory fast path, so the equal-success arm can
quantify avoided stages, messages, LLM calls, tokens, wire bytes and tool computation together.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import socket
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.runner import ExperimentConfig, _allocate_result_dir, _env_info, _write_checksum_manifest, run_once
from agent_runtime.scenarios.demo import make_demo_task


def _factory():
    return [make_demo_task(), make_demo_task()]


def run_memory_efficiency_benchmark(repeat: int = 10) -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    kernel_transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    common = dict(
        structured_wire_codec="session_msgpack",
        control_transport=kernel_transport,
        state_shared_memory=hasattr(socket, "AF_UNIX"),
        state_precision="adaptive",
    )
    configs = [
        ExperimentConfig("E0", "No Memory", "structured", True, False, False, **common),
        ExperimentConfig("E1", "Shared Memory", "structured", True, True, False, **common),
    ]
    raw: dict[str, list[dict[str, Any]]] = {c.id: [] for c in configs}
    for cfg in configs:
        for i in range(repeat):
            raw[cfg.id].append(run_once(cfg, _factory, 1000 + i))

    def mean(cid: str, key: str) -> float:
        return sum(float(x[key]) for x in raw[cid]) / len(raw[cid])

    base_tools = mean("E0", "tool_calls")
    mem_tools = mean("E1", "tool_calls")
    base_messages = mean("E0", "message_count")
    mem_messages = mean("E1", "message_count")
    base_tokens = mean("E0", "text_tokens")
    mem_tokens = mean("E1", "text_tokens")
    base_wire = mean("E0", "wire_bytes")
    mem_wire = mean("E1", "wire_bytes")
    base_control = mean("E0", "ipc_framed_bytes") + mean("E0", "ipc_ack_bytes") if mean("E0", "ipc_frames") else base_wire
    mem_control = mean("E1", "ipc_framed_bytes") + mean("E1", "ipc_ack_bytes") if mean("E1", "ipc_frames") else mem_wire
    base_state = mean("E0", "state_bytes")
    mem_state = mean("E1", "state_bytes")
    base_artifact = mean("E0", "artifact_bytes")
    mem_artifact = mean("E1", "artifact_bytes")
    base_transport = base_control + base_state + base_artifact
    mem_transport = mem_control + mem_state + mem_artifact
    base_llm = mean("E0", "llm_calls")
    mem_llm = mean("E1", "llm_calls")
    base_success = mean("E0", "task_success")
    mem_success = mean("E1", "task_success")
    return {
        "suite": "memory_efficiency",
        "repeat": repeat,
        "env": _env_info(),
        "methodology": {
            "task": "two identical successful demo tasks per run",
            "isolation": "same structured+state path; only shared memory toggled; sandbox disabled in both arms",
            "claim_boundary": "same exact tasks and success in both arms; message/token/wire/tool/LLM reductions are comparable; millisecond mock latency is not promoted as production-LLM latency",
        },
        "raw": raw,
        "summary": {
            "E0": {
                "success_rate": base_success,
                "tool_calls_mean": base_tools,
                "message_count_mean": base_messages,
                "llm_calls_mean": base_llm,
                "latency_sec_mean": mean("E0", "latency_sec"),
                "text_tokens_mean": base_tokens,
                "wire_bytes_mean": base_wire,
                "kernel_control_bytes_mean": base_control,
                "state_bytes_mean": base_state,
                "artifact_bytes_mean": base_artifact,
                "strict_transport_bytes_mean": base_transport,
                "ipc_frames_mean": mean("E0", "ipc_frames"),
                "memory_queries_mean": mean("E0", "memory_queries"),
                "memory_used_mean": mean("E0", "memory_used"),
            },
            "E1": {
                "success_rate": mem_success,
                "tool_calls_mean": mem_tools,
                "message_count_mean": mem_messages,
                "llm_calls_mean": mem_llm,
                "latency_sec_mean": mean("E1", "latency_sec"),
                "text_tokens_mean": mem_tokens,
                "wire_bytes_mean": mem_wire,
                "kernel_control_bytes_mean": mem_control,
                "state_bytes_mean": mem_state,
                "artifact_bytes_mean": mem_artifact,
                "strict_transport_bytes_mean": mem_transport,
                "ipc_frames_mean": mean("E1", "ipc_frames"),
                "memory_queries_mean": mean("E1", "memory_queries"),
                "memory_query_hits_mean": mean("E1", "memory_query_hits"),
                "memory_used_mean": mean("E1", "memory_used"),
                "memory_effective_mean": mean("E1", "memory_effective"),
                "fast_path_hits_mean": mean("E1", "memory_fast_path_hits"),
                "fast_path_skipped_stages_mean": mean("E1", "memory_fast_path_skipped_stages"),
            },
            "tool_call_reduction": 1 - mem_tools / base_tools if base_tools else 0.0,
            "message_reduction": 1 - mem_messages / base_messages if base_messages else 0.0,
            "token_reduction": 1 - mem_tokens / base_tokens if base_tokens else 0.0,
            "wire_reduction": 1 - mem_wire / base_wire if base_wire else 0.0,
            "kernel_control_reduction": 1 - mem_control / base_control if base_control else 0.0,
            "strict_transport_reduction": 1 - mem_transport / base_transport if base_transport else 0.0,
            "state_byte_reduction": 1 - mem_state / base_state if base_state else 0.0,
            "llm_call_reduction": 1 - mem_llm / base_llm if base_llm else 0.0,
            "success_equal": base_success == mem_success == 1.0,
        },
    }


def render_memory_efficiency_report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    return "\n".join([
        "# Equal-Success Memory Efficiency",
        "",
        "> Same task, same structured+state path, same success rate; only shared memory is toggled.",
        "",
        "| Arm | Success | Messages | Tokens | Verified UDS B | State B | Artifact B | Full transport B | LLM calls | Tool calls | Fast hits/skipped |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| E0 No Memory | {s['E0']['success_rate']:.0%} | {s['E0']['message_count_mean']:.1f} | {s['E0']['text_tokens_mean']:.1f} | {s['E0']['kernel_control_bytes_mean']:.1f} | {s['E0']['state_bytes_mean']:.1f} | {s['E0']['artifact_bytes_mean']:.1f} | {s['E0']['strict_transport_bytes_mean']:.1f} | {s['E0']['llm_calls_mean']:.1f} | {s['E0']['tool_calls_mean']:.1f} | 0/0 |",
        f"| E1 Shared Memory | {s['E1']['success_rate']:.0%} | {s['E1']['message_count_mean']:.1f} | {s['E1']['text_tokens_mean']:.1f} | {s['E1']['kernel_control_bytes_mean']:.1f} | {s['E1']['state_bytes_mean']:.1f} | {s['E1']['artifact_bytes_mean']:.1f} | {s['E1']['strict_transport_bytes_mean']:.1f} | {s['E1']['llm_calls_mean']:.1f} | {s['E1']['tool_calls_mean']:.1f} | {s['E1']['fast_path_hits_mean']:.1f}/{s['E1']['fast_path_skipped_stages_mean']:.1f} |",
        "",
        f"- Messages: **-{s['message_reduction']:.1%}**; communication tokens: **-{s['token_reduction']:.1%}**; codec payload: **-{s['wire_reduction']:.1%}**.",
        f"- Kernel-framed control: **-{s['kernel_control_reduction']:.1%}**; state bytes: **-{s['state_byte_reduction']:.1%}**; full control+state+artifact transport: **-{s['strict_transport_reduction']:.1%}**.",
        f"- LLM calls: **-{s['llm_call_reduction']:.1%}**; tool calls: **-{s['tool_call_reduction']:.1%}**.",
        f"- Both arms successful: **{s['success_equal']}**",
        "- Latency is intentionally not promoted as a production-LLM claim because the mock task completes in milliseconds.",
        "",
        f"Source SHA-256: `{payload['env']['source_tree_sha256']}`",
        "",
    ])


def write_memory_efficiency_results(payload: dict[str, Any], root: str = "results") -> Path:
    out = _allocate_result_dir(root, "memory_efficiency")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_memory_efficiency_report(payload), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({"generated_at_utc": datetime.now(timezone.utc).isoformat(), "suite": payload["suite"], "repeat": payload["repeat"], "environment": payload["env"], "methodology": payload["methodology"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_checksum_manifest(out)
    return out


def run_memory_efficiency_cli(args) -> int:
    payload = run_memory_efficiency_benchmark(repeat=args.repeat)
    out = write_memory_efficiency_results(payload)
    print(render_memory_efficiency_report(payload))
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["run_memory_efficiency_benchmark", "write_memory_efficiency_results", "run_memory_efficiency_cli"]
