from __future__ import annotations

import asyncio

from agent_runtime.config import load_config
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.mock import MockEmbedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task


def test_runtime_closes_owned_artifact_and_ephemeral_memory(monkeypatch, tmp_path):
    config = load_config(
        load_env=False,
        data_dir=str(tmp_path / "data"),
        artifact_persistent=True,
    )
    task = make_demo_task()
    ctx = build_context(
        config,
        "structured",
        task_id=task.task_id,
        use_memory=True,
        use_state_exchange=True,
    )
    assert ctx.memory_owned is True and ctx.artifact_store_owned is True
    calls = {"artifact": 0, "memory": 0}
    artifact_close = ctx.artifact_store.close
    memory_close = ctx.memory.close

    def close_artifact():
        calls["artifact"] += 1
        artifact_close()

    def close_memory():
        calls["memory"] += 1
        memory_close()

    monkeypatch.setattr(ctx.artifact_store, "close", close_artifact)
    monkeypatch.setattr(ctx.memory, "close", close_memory)
    result = asyncio.run(Orchestrator(build_default_agents(config)).run_task(task, ctx))
    assert result.success
    assert calls == {"artifact": 1, "memory": 1}


def test_runtime_does_not_close_caller_owned_shared_memory(monkeypatch, tmp_path):
    config = load_config(load_env=False, data_dir=str(tmp_path / "data"))
    shared = MemoryStore(MockEmbedding(dim=config.embedding.dim, seed=1))
    task = make_demo_task()
    ctx = build_context(
        config,
        "structured",
        task_id=task.task_id,
        use_memory=True,
        memory=shared,
    )
    assert ctx.memory_owned is False
    calls = {"memory": 0}
    original = shared.close

    def close_shared():
        calls["memory"] += 1
        original()

    monkeypatch.setattr(shared, "close", close_shared)
    result = asyncio.run(Orchestrator(build_default_agents(config)).run_task(task, ctx))
    assert result.success
    assert calls["memory"] == 0
    shared.close()
