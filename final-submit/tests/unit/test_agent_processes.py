"""fable10: one OS process per Agent -- equivalence, fault isolation, RPC semantics."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="fork unavailable")


def _run_all(mode, use_state, procs, monkeypatch, *, inject=None):
    from agent_runtime.config import load_config
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.protocol.routine import reset_routine_registry
    from agent_runtime.runtime.builder import build_context, build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator

    monkeypatch.setenv("AGENT_AGENT_PROCESSES", "1" if procs else "0")
    reset_routine_registry()
    cfg = load_config(load_env=False, control_transport="uds", structured_wire_codec="session_msgpack")
    orch = Orchestrator(build_default_agents(cfg))
    rows, pids = [], None
    try:
        for i, task in enumerate(_self_contained_tasks()):
            ctx = build_context(cfg, mode, task_id=task.task_id, use_state_exchange=use_state, use_sandbox=True)
            if inject is not None and i == 1:
                pids = dict(orch._agent_host().pids())
                orch._agent_host().inject_crash(inject)
            r = asyncio.run(orch.run_task(task, ctx))
            m = r.metrics
            # Actual wall-clock timestamp deltas have different decimal lengths in
            # process-host mode. Compare their semantic-free normalized token cost.
            stable_tokens = 0
            for message in ctx.channel.history:
                if mode == "structured":
                    frame = json.loads(ctx.channel.last_wire_by_id[message.message_id])
                    frame.pop("dt", None)
                    frame.pop("ts", None)
                    normalized = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
                else:
                    from agent_runtime.protocol.serialization import render_text, render_text_lean
                    render = render_text_lean if cfg.text_style == "lean" else render_text
                    normalized = render(message.model_copy(update={"timestamp": 0.0}))
                stable_tokens += ctx.channel.tokenizer.count(normalized)
            rows.append({
                "success": r.success, "answer": r.outputs.get("answer"), "messages": m.message_count,
                "wire": m.wire_bytes, "state": m.state_bytes, "artifact": m.artifact_bytes,
                "tokens": m.text_tokens, "crashes": m.agent_process_crashes,
                "stable_tokens": stable_tokens,
                "verified": m.ipc_verified, "frames": m.ipc_frames,
            })
        host = getattr(orch, "_host", None)
        after = dict(host.pids()) if host else None
    finally:
        orch.close_agent_processes()
    return rows, pids, after


@pytest.mark.parametrize("mode,use_state", [("text", False), ("structured", False), ("structured", True)])
def test_agent_processes_are_behaviourally_equivalent(monkeypatch, mode, use_state):
    a, _, _ = _run_all(mode, use_state, False, monkeypatch)
    b, _, pids = _run_all(mode, use_state, True, monkeypatch)
    assert pids is not None and len(set(pids.values())) == 4 and os.getpid() not in pids.values()
    for x, y in zip(a, b):
        assert y["success"] and x["answer"] == y["answer"]
        for k in ("messages", "wire", "state", "artifact", "verified", "frames"):
            assert x[k] == y[k], k
        assert x["stable_tokens"] == y["stable_tokens"]


def test_crashed_agent_process_is_isolated_respawned_and_stage_retried(monkeypatch):
    rows, before, after = _run_all("structured", True, True, monkeypatch, inject="retriever")
    assert all(r["success"] for r in rows)
    assert rows[1]["crashes"] == 1 and sum(r["crashes"] for r in rows) == 1
    assert before["retriever"] != after["retriever"]            # respawned with a new PID
    assert before["planner"] == after["planner"]                # others untouched


def test_remote_exceptions_keep_agent_try_except_semantics(monkeypatch):
    from agent_runtime.runtime.agent_host import AgentProcessHost
    from agent_runtime.runtime.errors import ReferenceError_

    class Ctx:
        def __init__(self):
            self.blackboard = {"a": 1}
            self.calls = []

        def boom(self):
            raise ReferenceError_("missing ref")

    class Agent:
        async def run(self, message, ctx):
            try:
                ctx.boom()
            except ReferenceError_ as exc:
                ctx.blackboard["caught"] = str(exc)
            ctx.blackboard["n"] = len(ctx.blackboard)
            return {"seen": ctx.blackboard["a"], "has": "caught" in ctx.blackboard}

    host = AgentProcessHost({"x": Agent()})
    try:
        ctx = Ctx()
        out = host.run("x", "msg", ctx)
        assert out == {"seen": 1, "has": True}
        assert ctx.blackboard == {"a": 1, "caught": "missing ref", "n": 2}  # pipelined effects applied in order
        assert host.rpc_ops > host.rpc_calls  # effects were batched
    finally:
        host.close()
