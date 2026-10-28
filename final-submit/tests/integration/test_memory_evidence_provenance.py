import asyncio

from agent_runtime.config import load_config
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.linux_service import make_l1


def test_reviewer_memory_carries_summary_strategy_and_evidence_provenance():
    cfg = load_config(load_env=False)
    memory = MemoryStore(build_embedding(cfg))
    task = make_l1()
    try:
        ctx = build_context(
            cfg, "structured", task_id=task.task_id,
            use_memory=True, use_state_exchange=True, use_sandbox=True, memory=memory,
        )
        result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
        assert result.success
        records = memory.all_records()
        assert records
        rec = records[-1]
        assert rec.task_topic == "linux-service-diagnosis"
        assert rec.summary
        assert rec.payload["solution"] == "diagnose_service_v1"
        assert "journal_excerpt" in rec.payload["evidence_keys"]
        assert rec.artifact_refs
        assert rec.provenance["trace_id"]
    finally:
        memory.close()
