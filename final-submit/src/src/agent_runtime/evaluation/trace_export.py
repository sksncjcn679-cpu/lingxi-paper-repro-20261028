"""Convert the hash-chained runtime event journal to Chrome Trace JSON."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest
from agent_runtime.runtime.event_log import EventJournal, journal_path_for


def export_trace(task_id: str, *, journal_root: str | Path = ".agent_data/events", output_root: str | Path = "results") -> Path:
    path = journal_path_for(journal_root, task_id)
    events = EventJournal(path, durability="buffered").read()
    base = min((event.timestamp for event in events), default=0.0)
    trace: list[dict[str, Any]] = []
    starts: dict[tuple[str, str | None], float] = {}
    for event in events:
        ts = (event.timestamp - base) * 1_000_000
        trace.append({
            "name": event.event_type,
            "ph": "i",
            "ts": ts,
            "pid": 1,
            "tid": event.stage or "runtime",
            "args": event.payload,
        })
        if event.event_type.endswith(".started"):
            starts[(event.event_type.removesuffix(".started"), event.stage)] = ts
        if event.event_type.endswith(".completed") or event.event_type.endswith(".failed"):
            key = (event.event_type.removesuffix(".completed").removesuffix(".failed"), event.stage)
            started = starts.get(key)
            if started is not None and ts >= started:
                trace.append({"name": key[0], "ph": "X", "ts": started, "dur": ts - started, "pid": 1, "tid": event.stage or "runtime", "args": {"task_id": task_id}})
    out = _allocate_result_dir(str(output_root), "trace")
    (out / "trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({"suite": "trace", "task_id": task_id, "event_count": len(events), "trace_event_count": len(trace), "source_tree_sha256": _env_info()["source_tree_sha256"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(f"# Chrome Trace Export\n\nTask: `{task_id}`\n\nEvents: {len(events)}\n", encoding="utf-8")
    _write_checksum_manifest(out)
    return out


def run_trace_export_cli(args) -> int:
    out = export_trace(args.task, journal_root=args.journal_root, output_root=args.output_root)
    print(f"Trace: {out / 'trace.json'}")
    return 0


__all__ = ["export_trace"]
