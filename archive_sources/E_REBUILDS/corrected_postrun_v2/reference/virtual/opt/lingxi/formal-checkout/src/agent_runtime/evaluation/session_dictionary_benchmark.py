"""Session value-dictionary ablation (physical bytes only, expanded token view)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.contest_workload import _self_contained_tasks
from agent_runtime.evaluation.runner import ExperimentConfig, _allocate_result_dir, _env_info, _write_checksum_manifest, run_once


def run_session_dictionary_benchmark(*, repeat: int = 3) -> dict[str, Any]:
    configs = {
        "S": ExperimentConfig("S", "Session Structured", "structured", False, False, True, structured_wire_codec="session_msgpack", control_transport="uds"),
        "S+dict": ExperimentConfig("S+dict", "Session Structured + value dictionary", "structured", False, False, True, structured_wire_codec="session_msgpack_dict", control_transport="uds"),
    }
    raw = {name: [run_once(cfg, _self_contained_tasks, 9200 + index) for index in range(repeat)] for name, cfg in configs.items()}
    summary = {}
    for name, rows in raw.items():
        summary[name] = {
            "success_rate": sum(int(row["task_success"]) for row in rows) / len(rows),
            "message_count_mean": sum(row["message_count"] for row in rows) / len(rows),
            "text_tokens_mean": sum(row["text_tokens"] for row in rows) / len(rows),
            "wire_bytes_mean": sum(row["wire_bytes"] for row in rows) / len(rows),
            "strict_transport_bytes_mean": sum(row["ipc_framed_bytes"] + row["ipc_ack_bytes"] + row["state_bytes"] + row["artifact_bytes"] for row in rows) / len(rows),
        }
    return {
        "suite": "session_dictionary",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "env": _env_info(),
        "methodology": {"dictionary_scope": "connection-scoped string values >=64 bytes; JSON token view stays fully expanded", "repeat": repeat},
        "summary": summary,
        "raw": raw,
    }


def render_session_dictionary(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    return "\n".join([
        "# Session Value Dictionary", "",
        "| Variant | Success | Token view | Physical wire B | Strict physical B |",
        "| --- | ---: | ---: | ---: | ---: |",
        *[f"| {name} | {row['success_rate']:.0%} | {row['text_tokens_mean']:.1f} | {row['wire_bytes_mean']:.1f} | {row['strict_transport_bytes_mean']:.1f} |" for name, row in s.items()],
        "",
        "The dictionary is reported as a separate S+dict ablation and never replaces the original S baseline.",
        f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`", "",
    ])


def write_session_dictionary_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "session_dictionary")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_session_dictionary(payload), encoding="utf-8")
    _write_checksum_manifest(out)
    return out


def run_session_dictionary_cli(args) -> int:
    payload = run_session_dictionary_benchmark(repeat=args.repeat)
    out = write_session_dictionary_results(payload)
    print(render_session_dictionary(payload))
    print(f"\nResults: {out}")
    return 0


__all__ = ["run_session_dictionary_benchmark", "write_session_dictionary_results"]
