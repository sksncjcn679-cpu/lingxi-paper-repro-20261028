"""Repeated OS-oriented communication stress matrix for competition evidence.

The single contest workload is intentionally human-readable.  This suite repeats the exact
same four successful Linux/openEuler tasks across Lean Text / Session Structured /
Session Structured + SHM State so that communication savings are reported as distributions,
not a one-off number.  Each round creates fresh task ids and fresh UDS router processes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import statistics
from typing import Any

from agent_runtime import PROTOCOL_VERSION
from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import (
    ExperimentConfig,
    _allocate_result_dir,
    _env_info,
    _write_checksum_manifest,
    run_once,
)
from agent_runtime.evaluation.stats import holm_bonferroni, paired_analysis


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(x) for x in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _dist(values: list[float]) -> dict[str, float]:
    vals = [float(v) for v in values]
    if not vals:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": float(statistics.fmean(vals)),
        "median": float(statistics.median(vals)),
        "p95": float(_pct(vals, 0.95)),
        "stdev": float(statistics.stdev(vals)) if len(vals) > 1 else 0.0,
        "min": float(min(vals)),
        "max": float(max(vals)),
    }


def run_competition_stress(*, rounds: int = 10, seed: int = 7000) -> dict[str, Any]:
    if rounds < 10:
        raise ValueError("competition stress evidence requires rounds >= 10")
    kernel_transport = "uds" if hasattr(socket, "AF_UNIX") else "inproc"
    cfgs = [
        ExperimentConfig(
            "T", "Lean Text + UDS", "text", False, False, True,
            text_style="lean", control_transport=kernel_transport,
        ),
        ExperimentConfig(
            "S", "Protocol-1.3 Session MessagePack + UDS", "structured", False, False, True,
            structured_wire_codec="session_msgpack", control_transport=kernel_transport,
        ),
        ExperimentConfig(
            "SS", "Session MessagePack + UDS + SHM Adaptive State", "structured", True, False, True,
            structured_wire_codec="session_msgpack", control_transport=kernel_transport,
            state_shared_memory=hasattr(socket, "AF_UNIX"), state_precision="adaptive",
        ),
    ]
    raw: dict[str, list[dict[str, Any]]] = {cfg.id: [] for cfg in cfgs}
    for round_index in range(rounds):
        for cfg in cfgs:
            row = run_once(cfg, _self_contained_tasks, seed + round_index)
            row["control_physical_bytes"] = (
                (row["ipc_framed_bytes"] + row["ipc_ack_bytes"]) if row["ipc_frames"] else row["wire_bytes"]
            )
            row["control_state_transport_bytes"] = row["control_physical_bytes"] + row["state_bytes"]
            row["strict_transport_bytes"] = row["control_state_transport_bytes"] + row.get("artifact_bytes", 0.0)
            raw[cfg.id].append(row)

    base = raw["T"]
    savings: dict[str, dict[str, list[float]]] = {"S": {}, "SS": {}}
    for cid in ("S", "SS"):
        for metric in ("text_tokens", "wire_bytes", "control_physical_bytes", "strict_transport_bytes"):
            vals = []
            for b, r in zip(base, raw[cid], strict=True):
                vals.append(1.0 - float(r[metric]) / max(1.0, float(b[metric])))
            savings[cid][metric] = vals

    paired: dict[str, dict[str, Any]] = {}
    for cid in ("S", "SS"):
        paired[cid] = {}
        p_values: dict[str, float] = {}
        for metric in ("text_tokens", "control_physical_bytes", "strict_transport_bytes", "latency_sec"):
            analysis = paired_analysis(
                [float(row[metric]) for row in raw["T"]],
                [float(row[metric]) for row in raw[cid]],
            )
            paired[cid][metric] = analysis
            p_values[metric] = analysis["p_value_normal_approx"]
        for metric, value in holm_bonferroni(p_values).items():
            paired[cid][metric]["holm_p_value_normal_approx"] = value

    summary: dict[str, Any] = {}
    for cid in ("T", "S", "SS"):
        rows = raw[cid]
        summary[cid] = {
            "success_rounds": sum(int(r["task_success"] == 1.0) for r in rows),
            "rounds": rounds,
            "messages": _dist([r["message_count"] for r in rows]),
            "tokens": _dist([r["text_tokens"] for r in rows]),
            "codec_payload_bytes": _dist([r["wire_bytes"] for r in rows]),
            "kernel_framed_control_bytes": _dist([r["control_physical_bytes"] for r in rows]),
            "state_bytes": _dist([r["state_bytes"] for r in rows]),
            "artifact_bytes": _dist([r.get("artifact_bytes", 0.0) for r in rows]),
            "strict_transport_bytes": _dist([r["strict_transport_bytes"] for r in rows]),
            "ipc_verified": _dist([r.get("ipc_verified", 0.0) for r in rows]),
            "ipc_socket_calls": _dist([r.get("ipc_socket_calls", 0.0) for r in rows]),
            "latency_sec": _dist([r["latency_sec"] for r in rows]),
            "ipc_roundtrip_sec": _dist([r["ipc_roundtrip_sec"] for r in rows]),
        }
        if cid != "T":
            summary[cid]["saving_vs_text"] = {
                metric: _dist(vals) for metric, vals in savings[cid].items()
            }
            summary[cid]["paired_vs_text"] = paired[cid]
    ss_rows = raw["SS"]
    summary["SS"]["state_materialization_saving"] = _dist([
        1.0 - r["state_bytes"] / max(1.0, r["state_logical_bytes"]) for r in ss_rows
    ])
    summary["SS"]["real_ipc_coverage"] = _dist([
        r["ipc_frames"] / max(1.0, r["message_count"]) for r in ss_rows
    ])
    summary["SS"]["shm_handoffs"] = _dist([r["state_shm_transfers"] for r in ss_rows])
    summary["SS"]["int8_handoffs"] = _dist([r["state_int8_transfers"] for r in ss_rows])

    return {
        "suite": "competition_stress",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rounds": rounds,
        "seed": seed,
        "env": _env_info(),
        "methodology": {
            "protocol_version": PROTOCOL_VERSION,
            "tasks_per_round": 4,
            "control_transport": kernel_transport,
            "structured_codec": "session_msgpack",
            "state": "shared memory + adaptive int8→float16 fallback on AF_UNIX",
            "strict_transport_accounting": "outbound UDS framing + SHA-256 verified ACK + state materialization + content-addressed artifact materialization",
            "claim_boundary": "controlled deterministic mock-first runtime evidence; no production-LLM latency claim",
            "paired_statistics": "paired differences by round; Student-t 95% CI, normal-approximate p/power, Holm correction within each variant",
        },
        "summary": summary,
        "raw": raw,
    }


def render_competition_stress(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        "# Protocol-1.3 Competition Stress Matrix",
        "",
        f"> {payload['rounds']} rounds × 4 OS tasks × 3 communication modes; same task semantics and 100% success are required.",
        "",
        "| Mode | Success rounds | Mean tokens | Mean kernel control B | Mean state B | Mean artifact B | Mean full physical B | P95 latency s |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cid, label in (("T", "Lean Text"), ("S", "Session Structured"), ("SS", "Session+State")):
        r = s[cid]
        lines.append(
            f"| {label} | {r['success_rounds']}/{r['rounds']} | {r['tokens']['mean']:.1f} | "
            f"{r['kernel_framed_control_bytes']['mean']:.1f} | {r['state_bytes']['mean']:.1f} | {r['artifact_bytes']['mean']:.1f} | "
            f"{r['strict_transport_bytes']['mean']:.1f} | {r['latency_sec']['p95']:.5f} |"
        )
    for cid, label in (("S", "Structured"), ("SS", "Structured+State")):
        d = s[cid]["saving_vs_text"]
        lines += [
            "",
            f"## {label} distribution vs Lean Text",
            "",
            f"- Token saving mean / min / P95: **{d['text_tokens']['mean']:.1%} / {d['text_tokens']['min']:.1%} / {d['text_tokens']['p95']:.1%}**.",
            f"- Kernel-framed control saving mean / min / P95: **{d['control_physical_bytes']['mean']:.1%} / {d['control_physical_bytes']['min']:.1%} / {d['control_physical_bytes']['p95']:.1%}**.",
            f"- Full physical transport saving mean / min / P95: **{d['strict_transport_bytes']['mean']:.1%} / {d['strict_transport_bytes']['min']:.1%} / {d['strict_transport_bytes']['p95']:.1%}**.",
            f"- Paired full-physical effect: Cohen's d_z={s[cid]['paired_vs_text']['strict_transport_bytes']['cohens_dz']:.3f}; "
            f"95% CI=[{s[cid]['paired_vs_text']['strict_transport_bytes']['ci95']['low']:.3f}, {s[cid]['paired_vs_text']['strict_transport_bytes']['ci95']['high']:.3f}] bytes; "
            f"Holm p (normal approx)={s[cid]['paired_vs_text']['strict_transport_bytes']['holm_p_value_normal_approx']:.4g}.",
        ]
    lines += [
        "",
        "## State / OS path",
        "",
        f"- State materialization saving mean: **{s['SS']['state_materialization_saving']['mean']:.1%}**.",
        f"- Real UDS coverage mean: **{s['SS']['real_ipc_coverage']['mean']:.1%}**; verified re-encode frames per round: **{s['SS']['ipc_verified']['mean']:.1f}**; socket calls: **{s['SS']['ipc_socket_calls']['mean']:.1f}**.",
        f"- SHM / int8 handoffs per round: **{s['SS']['shm_handoffs']['mean']:.1f} / {s['SS']['int8_handoffs']['mean']:.1f}**.",
        "",
        f"Source SHA-256: `{payload['env']['source_tree_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def write_competition_stress(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "competition_stress")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_competition_stress(payload), encoding="utf-8")
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "generated_at_utc": payload["generated_at_utc"],
                "suite": payload["suite"],
                "rounds": payload["rounds"],
                "environment": payload["env"],
                "methodology": payload["methodology"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_checksum_manifest(out)
    return out


def run_competition_stress_cli(args) -> int:
    payload = run_competition_stress(rounds=args.rounds, seed=args.seed)
    out = write_competition_stress(payload)
    print(render_competition_stress(payload))
    print(f"\n结果已保存到: {out}")
    return 0


__all__ = ["run_competition_stress", "write_competition_stress", "render_competition_stress"]
