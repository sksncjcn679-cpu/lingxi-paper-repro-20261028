"""fable7 mechanism ablation: every new mechanism switched off one at a time, same host, interleaved.

The previous releases compared headline latencies taken at different times on different VM
states, which cannot attribute a 2-3 ms difference to anything.  Here all arms run in the same
process, in a rotated order every repeat, on the same four contest tasks, and every arm reports
tokens, verified control bytes, strict full-plane bytes, per-task latency and success.

Arms (Structured+State, UDS, warm sandbox):

* ``all-off``     — the four switchable fable7 mechanisms off (Protocol 1.3 frames, synchronous
                    verify, raw artifacts, child-side compile).  NOTE: single-syscall framing and
                    resource-sample rate limiting are not switchable, so this arm is *not* the
                    fable5 release; use ``scripts/ab_compare.py`` for a true release-to-release A/B;
* ``fable7``      — everything on (the default);
* ``-cphc``       — fable7 without context-predictive headers/routines/reference table;
* ``-pipeline``   — fable7 with a blocking verified round trip per frame;
* ``-artifactz``  — fable7 with raw (uncompressed) artifact materialisation;
* ``-precompile`` — fable7 with compile() inside the forked sandbox child.

Negative results are reported as they come; nothing is filtered.
"""

from __future__ import annotations

import json
import os
import socket
import statistics
from contextlib import contextmanager
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

_ALL_OFF = {
    "AGENT_DECISION_CERTIFIED": "0",
    "AGENT_SHM_ARENA": "0",
    "AGENT_SCHEMA_ATOMS": "0",
    "AGENT_ACK_CHAIN": "0",
    "AGENT_STATE_RESOLVE_CACHE": "0",
    "AGENT_HEADER_PREDICTION": "0",
    "AGENT_PIPELINED_VERIFY": "0",
    "AGENT_ARTIFACT_COMPRESSION": "0",
    "AGENT_SANDBOX_PRECOMPILE": "0",
}
ARMS: dict[str, dict[str, str]] = {
    "all-off": dict(_ALL_OFF),
    "fable9": {},
    "-certified": {"AGENT_DECISION_CERTIFIED": "0"},
    "-arena": {"AGENT_SHM_ARENA": "0"},
    "-schemaatoms": {"AGENT_SCHEMA_ATOMS": "0"},
    "-ackchain": {"AGENT_ACK_CHAIN": "0"},
    "-resolvecache": {"AGENT_STATE_RESOLVE_CACHE": "0"},
    "-cphc": {"AGENT_HEADER_PREDICTION": "0"},
    "-pipeline": {"AGENT_PIPELINED_VERIFY": "0"},
    "-artifactz": {"AGENT_ARTIFACT_COMPRESSION": "0"},
    "-precompile": {"AGENT_SANDBOX_PRECOMPILE": "0"},
}


@contextmanager
def _env(overrides: dict[str, str]):
    keys = set(_ALL_OFF)
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(overrides)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _cfg() -> ExperimentConfig:
    uds = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    return ExperimentConfig(
        "SS", "Structured+State", "structured", True, False, True,
        structured_wire_codec="session_msgpack", control_transport=uds,
        state_shared_memory=hasattr(socket, "AF_UNIX"), state_precision="adaptive",
    )


def _one(arm: str) -> dict[str, Any]:
    with _env(ARMS[arm]):
        row = run_once(_cfg(), _self_contained_tasks, 42)
    control = (row["ipc_framed_bytes"] + row["ipc_ack_bytes"]) if row["ipc_frames"] else row["wire_bytes"]
    return {
        "success": row["task_success"] == 1.0,
        "tokens": row["text_tokens"],
        "control_bytes": control,
        "full_bytes": control + row["state_bytes"] + row["artifact_bytes"],
        "artifact_bytes": row["artifact_bytes"],
        "latency_ms": row["latency_sec"] / 4.0 * 1000.0,
        "socket_calls": row["ipc_socket_calls"],
        "verified": row["ipc_verified"],
        "frames": row["ipc_frames"],
    }


def run_fable7_ablation(repeat: int = 8) -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    arms = list(ARMS)
    for arm in arms:  # warm router / fork-server once per arm, discarded
        _one(arm)
    raw: dict[str, list[dict[str, Any]]] = {a: [] for a in arms}
    for i in range(repeat):
        shift = i % len(arms)
        for arm in arms[shift:] + arms[:shift]:
            raw[arm].append(_one(arm))
    summary: dict[str, Any] = {}
    for arm, rows in raw.items():
        summary[arm] = {
            "success_rate": sum(r["success"] for r in rows) / len(rows),
            "all_frames_verified": all(r["verified"] == r["frames"] for r in rows),
            **{
                f"{k}_median": statistics.median(r[k] for r in rows)
                for k in ("tokens", "control_bytes", "full_bytes", "artifact_bytes", "latency_ms", "socket_calls")
            },
        }
    base = raw["all-off"]
    for arm in arms:
        if arm == "all-off":
            continue
        summary[arm]["latency_paired_vs_all_off"] = paired_analysis(
            [r["latency_ms"] for r in base], [r["latency_ms"] for r in raw[arm]]
        )
    return {
        "suite": "fable7_ablation",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repeat": repeat,
        "arms": ARMS,
        "cpu_count": os.cpu_count(),
        "summary": summary,
        "raw": raw,
    }


def render_fable7_ablation(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    b = s["all-off"]
    lines = [
        "# Mechanism ablation, fable7 + fable8 + fable9 (Structured+State, UDS, same host, rotated order)",
        "",
        f"> repeats={payload['repeat']}; host CPUs={payload['cpu_count']}; medians over repeats; "
        "latency Δ uses paired analysis against the all-off arm.",
        "",
        "| Arm | Success | Verified | Tokens | Control B | Artifact B | Full physical B | Socket calls | Task latency ms | Latency Δ vs all-off [95% CI] |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm, x in s.items():
        pa = x.get("latency_paired_vs_all_off")
        if pa:
            ci = pa.get("ci95", {})
            delta = f"{pa.get('mean_difference', 0):+.2f} [{ci.get('low', 0):+.2f}, {ci.get('high', 0):+.2f}]"
        else:
            delta = "—"
        lines.append(
            f"| {arm} | {x['success_rate']:.0%} | {x['all_frames_verified']} | {x['tokens_median']:.0f} | "
            f"{x['control_bytes_median']:.0f} | {x['artifact_bytes_median']:.0f} | {x['full_bytes_median']:.0f} | "
            f"{x['socket_calls_median']:.0f} | {x['latency_ms_median']:.2f} | {delta} |"
        )
    f7 = s["fable9"]
    lines += [
        "",
        f"- fable9 (all mechanisms on) vs all-off: tokens **{1 - f7['tokens_median'] / max(1, b['tokens_median']):.1%}** fewer, "
        f"full physical bytes **{1 - f7['full_bytes_median'] / max(1, b['full_bytes_median']):.1%}** fewer, "
        f"task latency **{1 - f7['latency_ms_median'] / max(1e-9, b['latency_ms_median']):.1%}** lower.",
        "- Each `-x` arm removes one mechanism from fable9; its gap to `fable9` is that mechanism's contribution.",
        "",
    ]
    return "\n".join(lines)


def write_fable7_ablation(payload: dict[str, Any], root: str = "results") -> Path:
    payload = dict(payload)
    payload["env"] = _env_info()
    out = _allocate_result_dir(root, "fable7_ablation")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(
        render_fable7_ablation(payload) + f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`\n", encoding="utf-8"
    )
    _write_checksum_manifest(out)
    return out


def run_fable7_ablation_cli(args) -> int:
    payload = run_fable7_ablation(repeat=getattr(args, "repeat", 8))
    out = write_fable7_ablation(payload)
    print(render_fable7_ablation(payload))
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["ARMS", "run_fable7_ablation", "render_fable7_ablation", "write_fable7_ablation"]
