"""Request/reply versus forward message-topology ablation."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import (
    ExperimentConfig,
    _allocate_result_dir,
    _env_info,
    _write_checksum_manifest,
    run_once,
)
from agent_runtime.evaluation.stats import paired_analysis


def run_topology_benchmark(*, repeat: int = 1) -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    control_transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    configs = {
        topology: ExperimentConfig(
            topology,
            f"Session+State {topology}",
            "structured",
            True,
            False,
            True,
            structured_wire_codec="session_msgpack",
            pipeline_topology=topology,
            control_transport=control_transport,
            state_shared_memory=False,
        )
        for topology in ("request_reply", "forward")
    }
    raw = {topology: [] for topology in configs}
    pair_orders = []
    for i in range(repeat):
        order = list(configs)
        if i % 2:
            order.reverse()
        pair_orders.append(order)
        for topology in order:
            raw[topology].append(run_once(configs[topology], _self_contained_tasks, 42 + i))
    summary = {}
    for topology, rows in raw.items():
        keys = ("message_count", "text_tokens", "wire_bytes", "state_bytes", "artifact_bytes", "latency_sec")
        summary[topology] = {
            "success_rate": sum(int(row["task_success"]) for row in rows) / len(rows),
            **{f"{key}_mean": sum(float(row[key]) for row in rows) / len(rows) for key in keys},
        }
    paired = {
        key: paired_analysis(
            [float(row[key]) for row in raw["request_reply"]],
            [float(row[key]) for row in raw["forward"]],
        )
        for key in ("message_count", "text_tokens", "wire_bytes", "latency_sec")
    }
    return {
        "suite": "topology",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repeat": repeat,
        "env": _env_info(),
        "methodology": {
            "tasks": 4,
            "variants": {
                k: {"pipeline_topology": k, "control_transport": control_transport}
                for k in configs
            },
            "state_shared_memory": False,
            "pair_order": pair_orders,
            "pairing": "same task factory and seed per pair; arm order alternates to reduce time drift",
            "forward_message_definition": "four stage requests plus the reviewer result delivered to orchestrator",
        },
        "summary": summary,
        "paired_forward_minus_request_reply": paired,
        "raw": raw,
    }


def render_topology_report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    return "\n".join(
        [
            "# Message Topology Benchmark",
            "",
            "| Topology | Success | Messages | Tokens | Control wire B | State B | Artifact B | Latency s |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            *[
                f"| {key} | {s[key]['success_rate']:.0%} | {s[key]['message_count_mean']:.1f} | {s[key]['text_tokens_mean']:.1f} | {s[key]['wire_bytes_mean']:.1f} | {s[key]['state_bytes_mean']:.1f} | {s[key]['artifact_bytes_mean']:.1f} | {s[key]['latency_sec_mean']:.4f} |"
                for key in ("request_reply", "forward")
            ],
            "",
            "## Paired effect (forward - request_reply)",
            "",
            "| Metric | Mean difference | 95% CI | n |",
            "| --- | ---: | ---: | ---: |",
            *[
                f"| {key} | {value['mean_difference']:.4f} | [{value['ci95']['low']:.4f}, {value['ci95']['high']:.4f}] | {value['n']} |"
                for key, value in payload["paired_forward_minus_request_reply"].items()
            ],
            "",
            f"Control transport: `{payload['methodology']['variants']['forward']['control_transport']}`; state shared memory disabled to isolate message topology.",
            "",
            f"Source SHA-256: `{payload['env']['source_tree_sha256']}`",
            "",
        ]
    )


def write_topology_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "topology")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_topology_report(payload), encoding="utf-8")
    _write_checksum_manifest(out)
    return out


__all__ = ["run_topology_benchmark", "render_topology_report", "write_topology_results"]
