from __future__ import annotations

import asyncio
from dataclasses import replace

from agent_runtime.config import load_config
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task


def test_completed_pipeline_is_replayed_after_fresh_runtime_without_reexecuting_agents(tmp_path):
    task = replace(make_demo_task(), task_id="task-idempotent-fixed")
    cfg = load_config(
        load_env=False,
        data_dir=str(tmp_path),
        idempotency_persistent=True,
        idempotency_ttl_sec=3600,
        event_journal_enabled=True,
        event_journal_durable=False,
    )

    first_ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
    first = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, first_ctx))
    assert first.success is True
    assert first.metrics.llm_calls == 4

    # New agents + context emulate a fresh Runtime instance. The completed-result ledger
    # replays all four stage results and binds them to the second trace/message lineage.
    second_ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
    second = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, second_ctx))
    assert second.success is True
    assert second.outputs["answer"] == first.outputs["answer"] == 12
    assert second.metrics.llm_calls == 0
    assert second.metrics.tool_calls == 0
    assert second.metrics.idempotency_replays == 4
    assert second_ctx.trace_id != first_ctx.trace_id

    events = second_ctx.event_journal.verify() if second_ctx.event_journal else []
    second_trace = [e for e in events if e.trace_id == second_ctx.trace_id]
    assert sum(e.event_type == "idempotency.replayed" for e in second_trace) == 4


def test_persistent_idempotency_replays_across_fresh_python_processes(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    code = r'''
import asyncio, json, sys
from dataclasses import replace
from agent_runtime.config import load_config
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task

data_dir = sys.argv[1]
task = replace(make_demo_task(), task_id="task-idempotent-subprocess")
cfg = load_config(
    load_env=False,
    data_dir=data_dir,
    idempotency_persistent=True,
    idempotency_ttl_sec=3600,
)
ctx = build_context(cfg, "structured", task_id=task.task_id, use_state_exchange=True)
result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
print(json.dumps({
    "success": result.success,
    "answer": result.outputs.get("answer"),
    "llm_calls": result.metrics.llm_calls,
    "tool_calls": result.metrics.tool_calls,
    "replays": result.metrics.idempotency_replays,
}))
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    argv = [sys.executable, "-c", code, str(tmp_path)]
    first = subprocess.run(argv, cwd=tmp_path, env=env, text=True, capture_output=True, check=False)
    second = subprocess.run(argv, cwd=tmp_path, env=env, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    a = json.loads(first.stdout.strip().splitlines()[-1])
    b = json.loads(second.stdout.strip().splitlines()[-1])
    assert a == {"success": True, "answer": 12, "llm_calls": 4, "tool_calls": a["tool_calls"], "replays": 0}
    assert a["llm_calls"] == 4
    assert b["success"] is True and b["answer"] == 12
    assert b["llm_calls"] == 0
    assert b["tool_calls"] == 0
    assert b["replays"] == 4
