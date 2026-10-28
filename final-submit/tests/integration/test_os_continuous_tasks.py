import asyncio

from agent_runtime.config import load_config
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.linux_service import make_group as make_service_group
from agent_runtime.scenarios.openeuler_migration import make_group as make_migration_group


def test_two_os_related_groups_reuse_memory_without_negative_transfer():
    cfg = load_config(load_env=False)
    memory = MemoryStore(build_embedding(cfg))
    adopted = effective = harmful = 0
    try:
        for factory in (make_service_group, make_migration_group):
            for task in factory():
                ctx = build_context(
                    cfg, "structured", task_id=task.task_id,
                    use_memory=True, use_state_exchange=True, use_sandbox=True, memory=memory,
                )
                result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
                assert result.success
                assert {"planner", "retriever", "executor", "reviewer"} <= {
                    m.source_agent for m in result.messages
                }
                adopted += result.metrics.memory_used
                effective += result.metrics.memory_effective
                harmful += result.metrics.memory_harmful
        assert adopted >= 2
        assert effective >= 2
        assert harmful == 0
    finally:
        memory.close()


def test_related_followups_fail_without_memory_strategy():
    cfg = load_config(load_env=False)
    for factory in (make_service_group, make_migration_group):
        tasks = factory()
        followup = tasks[1]
        ctx = build_context(
            cfg, "structured", task_id=followup.task_id,
            use_memory=False, use_state_exchange=True, use_sandbox=True,
        )
        result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(followup, ctx))
        assert result.success is False
