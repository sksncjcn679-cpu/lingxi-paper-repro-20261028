"""fable10 suite: one OS process per Agent -- equivalence, fault isolation, overhead.

For T / S / SS on the four contest tasks the runtime is executed twice (Agents in-process vs one
worker process per Agent).  Reported: identical answers / message counts / verified frames /
control, state and artifact bytes; distinct worker PIDs; RPC round trips vs operations
(pipelining); per-task latency of both modes; and a crash drill that kills the Retriever process
mid-run and checks respawn + stage retry + task success without touching the other Agents.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from datetime import datetime, timezone
from typing import Any


def _run(mode: str, use_state: bool, procs: bool, *, crash: str | None = None, repeat: int = 3) -> dict[str, Any]:
    from agent_runtime.config import load_config
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.protocol.routine import reset_routine_registry
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator

    prev = os.environ.get("AGENT_AGENT_PROCESSES")
    os.environ["AGENT_AGENT_PROCESSES"] = "1" if procs else "0"
    try:
        cfg = load_config(load_env=False, control_transport="uds", structured_wire_codec="session_msgpack")
        orch = Orchestrator(build_default_agents(cfg))
        rows, lat = [], []
        pids_before = pids_after = None
        try:
            for r in range(repeat):
                reset_routine_registry()
                for i, task in enumerate(_self_contained_tasks()):
                    ctx = build_context(cfg, mode, task_id=task.task_id, use_state_exchange=use_state, use_sandbox=True)
                    if crash and r == repeat - 1 and i == 1:
                        pids_before = dict(orch._agent_host().pids())
                        orch._agent_host().inject_crash(crash)
                    t0 = time.perf_counter()
                    res = asyncio.run(orch.run_task(task, ctx))
                    if r > 0:
                        lat.append((time.perf_counter() - t0) * 1000)
                    m = res.metrics
                    if r == repeat - 1:
                        rows.append({
                            "success": res.success, "answer": res.outputs.get("answer"), "messages": m.message_count,
                            "frames_verified": f"{m.ipc_verified}/{m.ipc_frames}", "wire": m.wire_bytes,
                            "state": m.state_bytes, "artifact": m.artifact_bytes, "crashes": m.agent_process_crashes,
                        })
            host = getattr(orch, "_host", None)
            info = {"pids": dict(host.pids()), "rpc_round_trips": host.rpc_calls, "rpc_ops": host.rpc_ops,
                    "crashes": host.crashes} if host else None
            pids_after = info["pids"] if info else None
        finally:
            orch.close_agent_processes()
        return {"rows": rows, "latency_ms_median": statistics.median(lat) if lat else None, "host": info,
                "pids_before_crash": pids_before, "pids_after": pids_after}
    finally:
        if prev is None:
            os.environ.pop("AGENT_AGENT_PROCESSES", None)
        else:
            os.environ["AGENT_AGENT_PROCESSES"] = prev


def run_agent_isolation_benchmark(repeat: int = 3) -> dict[str, Any]:
    modes = {"T": ("text", False), "S": ("structured", False), "SS": ("structured", True)}
    out: dict[str, Any] = {}
    for label, (mode, st) in modes.items():
        a = _run(mode, st, False, repeat=repeat)
        b = _run(mode, st, True, repeat=repeat)
        keys = ("success", "answer", "messages", "frames_verified", "wire", "state", "artifact")
        out[label] = {
            "equivalent": all(all(x[k] == y[k] for k in keys) for x, y in zip(a["rows"], b["rows"])),
            "inproc_latency_ms": a["latency_ms_median"],
            "process_latency_ms": b["latency_ms_median"],
            "worker_pids": b["host"]["pids"],
            "runtime_pid": os.getpid(),
            "rpc_round_trips_per_task": b["host"]["rpc_round_trips"] / (repeat * 4),
            "rpc_ops_per_task": b["host"]["rpc_ops"] / (repeat * 4),
        }
    drill = _run("structured", True, True, crash="retriever", repeat=1)
    out["crash_drill"] = {
        "all_tasks_succeeded": all(r["success"] for r in drill["rows"]),
        "crashes_recorded": sum(r["crashes"] for r in drill["rows"]),
        "retriever_pid_changed": drill["pids_before_crash"]["retriever"] != drill["pids_after"]["retriever"],
        "other_agents_untouched": all(drill["pids_before_crash"][k] == drill["pids_after"][k]
                                      for k in ("planner", "executor", "reviewer")),
    }
    return {"suite": "agent_isolation", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "repeat": repeat, "cpu_count": os.cpu_count(), "results": out}


def render_agent_isolation(p: dict[str, Any]) -> str:
    r = p["results"]
    L = ["# One OS process per Agent (fable10)", "",
         f"> repeat={p['repeat']}; host CPUs={p['cpu_count']}; equivalence = identical answers, messages, verified frames, control/state/artifact bytes.", "",
         "| Mode | Equivalent | In-process ms/task | Agent-process ms/task | RPC round trips/task | RPC ops/task | Worker PIDs |",
         "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for k in ("T", "S", "SS"):
        x = r[k]
        L.append(f"| {k} | {x['equivalent']} | {x['inproc_latency_ms']:.2f} | {x['process_latency_ms']:.2f} | "
                 f"{x['rpc_round_trips_per_task']:.1f} | {x['rpc_ops_per_task']:.1f} | {sorted(x['worker_pids'].values())} (runtime {x['runtime_pid']}) |")
    d = r["crash_drill"]
    L += ["", f"- Crash drill (Retriever process killed mid-run): all tasks succeeded={d['all_tasks_succeeded']}; "
              f"crashes recorded={d['crashes_recorded']}; retriever respawned={d['retriever_pid_changed']}; "
              f"other agents untouched={d['other_agents_untouched']}.",
          "- Overhead is the price of isolation (every ctx access is an IPC); the mode is opt-in (`AGENT_AGENT_PROCESSES=1`).", ""]
    return "\n".join(L)


def run_agent_isolation_cli(args) -> int:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest

    payload = run_agent_isolation_benchmark(repeat=getattr(args, "repeat", 3))
    payload["env"] = _env_info()
    out = _allocate_result_dir("results", "agent_isolation")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    text = render_agent_isolation(payload) + f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    _write_checksum_manifest(out)
    print(text)
    return 0 if all(payload["results"][k]["equivalent"] for k in ("T", "S", "SS")) and payload["results"]["crash_drill"]["all_tasks_succeeded"] else 1
