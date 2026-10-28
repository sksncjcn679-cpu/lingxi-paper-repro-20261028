"""Offline check of worker PIDs, state service location, and explicit process cleanup."""
from pathlib import Path
import asyncio
import json
import os
import time

import psutil
from agent_runtime.config import Config, LLMConfig
from agent_runtime.evaluation.decision_workload import FixtureDecisionProvider
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from pilot_corpus import corpus, make_task

cfg = Config(llm=LLMConfig(provider="mock"), llm_advisory=False, llm_coalesce="off",
             control_transport="uds", pipeline_topology="request_reply", state_shared_memory=True,
             agent_processes=True, data_dir="/opt/lingxi/topology-offline")
orch = Orchestrator(build_default_agents(cfg))
host = orch._agent_host()
pids = host.pids()
reads = []
task_results = []
try:
    for case in corpus()[:2]:
        ctx = build_context(cfg, "structured", task_id=case["task_id"], use_state_exchange=True)
        ctx.llm = FixtureDecisionProvider()
        original = ctx.state_exchange.get_plan_state
        def read(*args, **kwargs):
            reads.append({"task_id": case["task_id"], "pid": os.getpid(), "uri": args[0]})
            return original(*args, **kwargs)
        ctx.state_exchange.get_plan_state = read
        result = asyncio.run(orch.run_task(make_task(case), ctx))
        task_results.append({"task_id": case["task_id"], "success": result.success,
                             "state_consumed": bool(ctx.blackboard.get("state_consumed"))})
finally:
    orch.close_agent_processes()
    time.sleep(.1)
report = {"purpose": "offline topology and cleanup only; not real model evidence", "runtime_pid": os.getpid(),
          "worker_pids": pids, "distinct_workers": len(set(pids.values())) == 4 and os.getpid() not in pids.values(),
          "worker_processes_gone_after_explicit_close": all(not psutil.pid_exists(pid) for pid in pids.values()),
          "state_service_reads": reads, "task_results": task_results,
          "state_topology": "get_plan_state executes in the runtime process; arrays are returned to Agent workers through RPC/pickle",
          "child_direct_shm_mapping": "NOT_IMPLEMENTED_BY_THIS_MAIN_PIPELINE; do not claim direct mapping in child Agent"}
Path("/evidence/analysis/topology_verification.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
raise SystemExit(0 if report["distinct_workers"] and report["worker_processes_gone_after_explicit_close"] and all(r["success"] for r in task_results) else 1)
