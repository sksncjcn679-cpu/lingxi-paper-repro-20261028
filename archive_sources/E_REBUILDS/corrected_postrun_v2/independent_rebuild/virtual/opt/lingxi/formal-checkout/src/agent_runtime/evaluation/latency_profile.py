"""Measured SS-vs-text latency and option-by-option optimization audit."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any

from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import ExperimentConfig, _allocate_result_dir, _env_info, _write_checksum_manifest, run_once


def _measure(cfg: ExperimentConfig, repeat: int) -> dict[str, Any]:
    warm = run_once(cfg, _self_contained_tasks, 7000)
    rows = [run_once(cfg, _self_contained_tasks, 7001 + i) for i in range(repeat)]
    per_task = [float(row["latency_sec"]) / 4.0 * 1000.0 for row in rows]
    return {"warmup_success": bool(warm["task_success"]), "success_rate": sum(int(row["task_success"]) for row in rows) / len(rows), "p50_ms_per_task": statistics.median(per_task), "samples": per_task}


def run_latency_profile(*, repeat: int = 10) -> dict[str, Any]:
    text = ExperimentConfig("T", "Lean Text", "text", False, False, True, text_style="lean", control_transport="uds")
    ss = ExperimentConfig("SS", "Structured+State", "structured", True, False, True, structured_wire_codec="session_msgpack", control_transport="uds", state_shared_memory=True)
    measured = {"T": _measure(text, repeat), "SS": _measure(ss, repeat)}
    measured["SS"]["overhead_vs_T"] = measured["SS"]["p50_ms_per_task"] / max(1e-9, measured["T"]["p50_ms_per_task"]) - 1.0
    return {
        "suite": "latency_profile",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "env": _env_info(),
        "measured": measured,
        "options": {
            "D08-a codec JSON caching": {"status": "not_separately_changed", "reason": "token accounting remains mandatory; profile result is retained for future optimization"},
            "D08-b embedding LRU": {"status": "not_enabled", "reason": "provider embedding cache is not part of the default state path"},
            "D08-c router affinity": {"status": "not_enabled", "reason": "platform-specific affinity would confound the default comparison"},
            "D08-d SHM arena": {"status": "not_enabled", "reason": "existing state-create cost is below the measured end-to-end threshold"},
        },
    }


def render_latency_profile(payload: dict[str, Any]) -> str:
    m = payload["measured"]
    return "\n".join([
        "# Structured+State Latency Profile", "",
        f"- Lean Text p50: **{m['T']['p50_ms_per_task']:.3f} ms/task**",
        f"- Structured+State p50: **{m['SS']['p50_ms_per_task']:.3f} ms/task**",
        f"- SS overhead: **{m['SS']['overhead_vs_T']:.1%}**",
        "",
        "All D08 candidate options are listed with their measured/explicitly unenabled status; no option is claimed as an optimization without a before/after run.",
        f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`", "",
    ])


def write_latency_profile_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "latency_profile")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_latency_profile(payload), encoding="utf-8")
    _write_checksum_manifest(out)
    return out


def run_latency_profile_cli(args) -> int:
    payload = run_latency_profile(repeat=args.repeat)
    out = write_latency_profile_results(payload)
    print(render_latency_profile(payload))
    print(f"\nResults: {out}")
    return 0


__all__ = ["run_latency_profile", "write_latency_profile_results"]
