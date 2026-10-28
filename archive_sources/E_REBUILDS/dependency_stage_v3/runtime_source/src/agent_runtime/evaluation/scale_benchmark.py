"""Protocol-1.3 workload-size sweep with 95% confidence intervals (v0.8.0).

Same four Linux/openEuler tasks are run at journal-evidence x1/x4/x16. Every mode uses the same
verified AF_UNIX path on Linux; Structured+State additionally uses SHM + adaptive state. Strict
transport is control framing + SHA-256 ACK + state materialization + artifact materialization.
"""
from __future__ import annotations

import copy
import json
import math
import socket
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.runner import ExperimentConfig, _allocate_result_dir, _env_info, _write_checksum_manifest, run_once
from agent_runtime.evaluation.stats import holm_bonferroni, paired_analysis
from agent_runtime.scenarios.linux_service import make_group as make_service_group
from agent_runtime.scenarios.openeuler_migration import make_group as make_migration_group

SCALES = {"small": 1, "medium": 4, "large": 16}


def _configs() -> list[ExperimentConfig]:
    transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    return [
        ExperimentConfig("T", "Lean Text", "text", False, False, True, text_style="lean", control_transport=transport),
        ExperimentConfig("S", "Session Structured", "structured", False, False, True, structured_wire_codec="session_msgpack", control_transport=transport),
        ExperimentConfig("SS", "Session Structured + State", "structured", True, False, True, structured_wire_codec="session_msgpack", control_transport=transport, state_shared_memory=hasattr(socket, "AF_UNIX"), state_precision="adaptive"),
    ]


def _factory(multiplier: int):
    def make():
        tasks = [*make_service_group(self_contained=True), *make_migration_group(self_contained=True)]
        out = []
        for task in tasks:
            t = copy.deepcopy(task)
            facts = t.payload.get("facts", {})
            for key, value in list(facts.items()):
                if isinstance(value, str) and "\n" in value:
                    facts[key] = "\n".join([value] * multiplier)
            out.append(t)
        return out
    return make


def _stats(values: list[float]) -> dict[str, float]:
    n = len(values)
    mean = statistics.fmean(values) if values else 0.0
    std = statistics.stdev(values) if n > 1 else 0.0
    ordered = sorted(values)
    p95 = ordered[min(n - 1, int(math.ceil(0.95 * n)) - 1)] if n else 0.0
    half = 1.96 * std / math.sqrt(n) if n > 1 else 0.0
    return {"n": n, "mean": mean, "median": statistics.median(values) if values else 0.0, "p95": p95, "std": std, "ci95_low": mean-half, "ci95_high": mean+half}


def _physical(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["control_physical_bytes"] = (out["ipc_framed_bytes"] + out["ipc_ack_bytes"]) if out["ipc_frames"] else out["wire_bytes"]
    out["control_state_bytes"] = out["control_physical_bytes"] + out["state_bytes"]
    out["full_transport_bytes"] = out["control_state_bytes"] + out.get("artifact_bytes", 0.0)
    return out


def run_scale_benchmark(repeat: int = 10) -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    cells: dict[str, dict[str, Any]] = {}
    for scale, mult in SCALES.items():
        make = _factory(mult)
        cells[scale] = {}
        raw_by_mode: dict[str, list[dict[str, Any]]] = {}
        for cfg in _configs():
            run_once(cfg, make, 42)  # warm imports, router and fork-server
            rows = [_physical(run_once(cfg, make, 42+i)) for i in range(repeat)]
            raw_by_mode[cfg.id] = rows
            keys = ("wire_bytes","text_tokens","control_physical_bytes","state_bytes","artifact_bytes","full_transport_bytes","message_count","tool_calls","ipc_verified","ipc_socket_calls")
            det = {k: [float(r.get(k,0.0)) for r in rows] for k in keys}
            cells[scale][cfg.id] = {
                "success_rate": statistics.fmean(float(r["task_success"]) for r in rows),
                "deterministic": {k:{"mean":statistics.fmean(v),"std":statistics.pstdev(v)} for k,v in det.items()},
                "mean_task_latency_sec": _stats([float(r["latency_sec"])/4.0 for r in rows]),
                "ipc_rtt_sec": _stats([float(r["ipc_roundtrip_sec"]) for r in rows]),
            }
        base = cells[scale]["T"]["deterministic"]
        for cid in ("S","SS"):
            d = cells[scale][cid]["deterministic"]
            cells[scale][cid]["saving_vs_text"] = {
                "tokens": 1-d["text_tokens"]["mean"]/base["text_tokens"]["mean"],
                "codec_payload": 1-d["wire_bytes"]["mean"]/base["wire_bytes"]["mean"],
                "verified_control": 1-d["control_physical_bytes"]["mean"]/base["control_physical_bytes"]["mean"],
                "full_physical": 1-d["full_transport_bytes"]["mean"]/base["full_transport_bytes"]["mean"],
            }
            # Every mode uses the same seed sequence at a given scale, so these are
            # paired observations rather than independent samples. Keep the raw rows
            # local and publish only the reproducible statistical summary.
            metric_getters = {
                "text_tokens": lambda row: float(row["text_tokens"]),
                "wire_bytes": lambda row: float(row["wire_bytes"]),
                "control_physical_bytes": lambda row: float(row["control_physical_bytes"]),
                "state_bytes": lambda row: float(row["state_bytes"]),
                "artifact_bytes": lambda row: float(row.get("artifact_bytes", 0.0)),
                "full_transport_bytes": lambda row: float(row["full_transport_bytes"]),
                "task_latency_sec": lambda row: float(row["latency_sec"]) / 4.0,
                "ipc_roundtrip_sec": lambda row: float(row["ipc_roundtrip_sec"]),
            }
            paired: dict[str, Any] = {}
            p_values: dict[str, float] = {}
            for metric, getter in metric_getters.items():
                analysis = paired_analysis(
                    [getter(row) for row in raw_by_mode["T"]],
                    [getter(row) for row in raw_by_mode[cid]],
                )
                paired[metric] = analysis
                p_values[metric] = analysis["p_value_normal_approx"]
            for metric, value in holm_bonferroni(p_values).items():
                paired[metric]["holm_p_value_normal_approx"] = value
            cells[scale][cid]["paired_vs_text"] = paired
    return {"suite":"scale_benchmark","generated_at_utc":datetime.now(timezone.utc).isoformat(),"env":_env_info(),"repeat":repeat,"scales":SCALES,"cells":cells,
            "methodology":{"tasks_per_cell":4,"protocol":"1.3 session_msgpack","physical_accounting":"verified UDS framing+SHA256 ACK+state+artifact","latency_statistics":"mean, median, P95, std, normal-approximation 95% CI"}}


def render_scale_benchmark(p: dict[str, Any]) -> str:
    lines=["# v0.8 Scale Sweep — Small / Medium / Large","",f"> repeat={p['repeat']} per cell; all four OS tasks must succeed. Full physical = verified UDS control + state + artifact.","",
           "| Scale | Mode | Success | Tokens | Verified control B | State+Artifact B | Full physical B | Full saving | Task latency ms mean [95% CI] | P95 ms |",
           "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for scale,cells in p["cells"].items():
        for cid in ("T","S","SS"):
            c=cells[cid]; d=c["deterministic"]; lat=c["mean_task_latency_sec"]; sv=c.get("saving_vs_text")
            svtxt=f"{sv['full_physical']:.1%}" if sv else "baseline"
            lines.append(f"| {scale} | {cid} | {c['success_rate']:.0%} | {d['text_tokens']['mean']:,.0f} | {d['control_physical_bytes']['mean']:,.0f} | {d['state_bytes']['mean']+d['artifact_bytes']['mean']:,.0f} | {d['full_transport_bytes']['mean']:,.0f} | {svtxt} | {lat['mean']*1000:.2f} [{lat['ci95_low']*1000:.2f}, {lat['ci95_high']*1000:.2f}] | {lat['p95']*1000:.2f} |")
    for scale, cells in p["cells"].items():
        for cid in ("S", "SS"):
            paired = cells[cid].get("paired_vs_text")
            if not paired:
                continue
            effect = paired["full_transport_bytes"]
            lines += [
                "",
                f"### {scale} / {cid} paired inference",
                "",
                f"- Full physical bytes: Cohen's d_z={effect['cohens_dz']:.3f}; "
                f"95% CI=[{effect['ci95']['low']:.2f}, {effect['ci95']['high']:.2f}] bytes; "
                f"Holm p (normal approx)={effect['holm_p_value_normal_approx']:.4g}.",
                "- CI uses Student-t critical values; p-value and power are normal approximations.",
            ]
    lines += ["",f"Source SHA-256: `{p['env']['source_tree_sha256']}`",""]
    return "\n".join(lines)


def write_scale_benchmark(p: dict[str,Any], root: str|Path="results") -> Path:
    out=_allocate_result_dir(str(root),"scale_benchmark")
    (out/"summary.json").write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"report.md").write_text(render_scale_benchmark(p),encoding="utf-8")
    (out/"metadata.json").write_text(json.dumps({"suite":p["suite"],"generated_at_utc":p["generated_at_utc"],"environment":p["env"],"methodology":p["methodology"]},ensure_ascii=False,indent=2),encoding="utf-8")
    _write_checksum_manifest(out); return out


def run_scale_benchmark_cli(args) -> int:
    p=run_scale_benchmark(repeat=args.repeat); out=write_scale_benchmark(p)
    print(render_scale_benchmark(p)); print(f"\n结果已保存到: {out}"); return 0

__all__=["run_scale_benchmark","write_scale_benchmark","render_scale_benchmark","run_scale_benchmark_cli"]
