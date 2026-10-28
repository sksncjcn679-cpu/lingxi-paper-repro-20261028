"""阶段边界 checkpoint / resume 集成回归。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime.config import load_config
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.checkpoint import (
    CheckpointCorruptionError,
    FileCheckpointStore,
)
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task


def test_pause_after_retriever_and_resume_without_reexecuting_completed_stages(
    tmp_path: Path,
):
    config = load_config(load_env=False, pipeline_topology="forward")
    task = make_demo_task()
    store = FileCheckpointStore(tmp_path / "checkpoints")

    first_ctx = build_context(
        config,
        "structured",
        task_id=task.task_id,
        use_state_exchange=True,
    )
    first = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task,
            first_ctx,
            checkpoint_store=store,
            pause_after_stage="retriever",
        )
    )

    assert first.paused is True
    assert first.success is False
    assert first.checkpoint is not None
    assert first.checkpoint.next_stage_index == 2
    assert first.metrics.llm_calls == 2  # planner + retriever 已完成
    assert first.metrics.message_count == 2  # two requests; intermediate replies stay local

    checkpoint = store.load(first.checkpoint.checkpoint_id)
    resumed_ctx = build_context(
        config,
        "structured",
        task_id=task.task_id,
        use_state_exchange=True,
    )
    resumed = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task,
            resumed_ctx,
            checkpoint_store=store,
            resume_from=checkpoint,
        )
    )

    assert resumed.paused is False
    assert resumed.success is True
    assert resumed.outputs["answer"] == 12
    # 恢复后只执行 executor + reviewer；总 LLM 调用应等于一次完整运行的 4 次，
    # 而不是把 planner/retriever 再跑一遍后的 6 次。
    assert resumed.metrics.llm_calls == 4
    assert resumed.metrics.message_count == 5  # four requests + final reviewer reply across resume
    # Two plan-state handoffs + four versioned control-state revisions.
    assert resumed.metrics.state_transfers == 6
    assert resumed.metrics.versioned_state_transfers == 4
    assert resumed.metrics.versioned_state_delta_updates >= 2
    assert resumed.metrics.state_dedup_hits == 1
    assert resumed_ctx.trace_id == checkpoint.trace_id


def test_checkpoint_rejects_task_fingerprint_mismatch(tmp_path: Path):
    config = load_config(load_env=False)
    task = make_demo_task()
    store = FileCheckpointStore(tmp_path / "checkpoints")

    ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
    paused = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task,
            ctx,
            checkpoint_store=store,
            pause_after_stage="planner",
        )
    )
    assert paused.checkpoint is not None

    modified = replace(task, description=task.description + " [modified]")
    resume_ctx = build_context(
        config,
        "structured",
        task_id=modified.task_id,
        use_state_exchange=True,
    )
    with pytest.raises(ValueError, match="checkpoint task"):
        asyncio.run(
            Orchestrator(build_default_agents(config)).run_task(
                modified,
                resume_ctx,
                resume_from=paused.checkpoint,
            )
        )


def test_checkpoint_rejects_runtime_configuration_mismatch(tmp_path: Path):
    config = load_config(load_env=False)
    task = make_demo_task()
    store = FileCheckpointStore(tmp_path / "checkpoints")

    ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
    paused = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task,
            ctx,
            checkpoint_store=store,
            pause_after_stage="planner",
        )
    )
    assert paused.checkpoint is not None

    # 改成 text/no-state 会改变恢复语义，因此必须拒绝，而不是“尽量接着跑”。
    resume_ctx = build_context(config, "text", task_id=task.task_id, use_state_exchange=False)
    with pytest.raises(ValueError, match="runtime configuration"):
        asyncio.run(
            Orchestrator(build_default_agents(config)).run_task(
                task,
                resume_ctx,
                resume_from=paused.checkpoint,
            )
        )


def test_file_checkpoint_resumes_in_fresh_python_process(tmp_path: Path):
    config = load_config(load_env=False, pipeline_topology="forward")
    task = make_demo_task()
    root = tmp_path / "checkpoints"
    store = FileCheckpointStore(root)

    ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
    paused = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task,
            ctx,
            checkpoint_store=store,
            pause_after_stage="retriever",
        )
    )
    assert paused.checkpoint is not None

    code = """
import asyncio, json, sys
from dataclasses import replace
from agent_runtime.config import load_config
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.checkpoint import (
    CheckpointCorruptionError,
    FileCheckpointStore,
)
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.scenarios.demo import make_demo_task
store = FileCheckpointStore(sys.argv[1])
checkpoint = store.load(sys.argv[2])
task = replace(make_demo_task(), task_id=checkpoint.task_id)
config = load_config(load_env=False, pipeline_topology="forward")
ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
result = asyncio.run(Orchestrator(build_default_agents(config)).run_task(
    task, ctx, resume_from=checkpoint
))
print(json.dumps({
    "success": result.success,
    "answer": result.outputs["answer"],
    "llm_calls": result.metrics.llm_calls,
    "messages": result.metrics.message_count,
    "trace_id": ctx.trace_id,
}))
"""
    out = subprocess.check_output(
        [
            sys.executable,
            "-c",
            code,
            str(root),
            paused.checkpoint.checkpoint_id,
        ],
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")
            + os.pathsep
            + os.environ.get("PYTHONPATH", ""),
        },
    )
    payload = json.loads(out)
    assert payload["success"] is True
    assert payload["answer"] == 12
    assert payload["llm_calls"] == 4
    assert payload["messages"] == 5
    assert payload["trace_id"] == paused.checkpoint.trace_id


def test_checkpoint_storage_detects_payload_tampering(tmp_path: Path):
    config = load_config(load_env=False)
    task = make_demo_task()
    store = FileCheckpointStore(tmp_path / "checkpoints")
    ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
    paused = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task, ctx, checkpoint_store=store, pause_after_stage="planner"
        )
    )
    assert paused.checkpoint is not None
    path = store.root / f"{paused.checkpoint.checkpoint_id}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["checkpoint"]["next_stage_index"] = 4
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CheckpointCorruptionError, match="integrity"):
        store.load(paused.checkpoint.checkpoint_id)


def test_checkpoint_store_rejects_path_traversal_id(tmp_path: Path):
    store = FileCheckpointStore(tmp_path / "checkpoints")
    with pytest.raises(ValueError, match="invalid checkpoint id"):
        store.load("../../outside")


def test_checkpoint_store_reads_legacy_bare_json(tmp_path: Path):
    config = load_config(load_env=False)
    task = make_demo_task()
    store = FileCheckpointStore(tmp_path / "checkpoints")
    ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
    paused = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task, ctx, pause_after_stage="planner"
        )
    )
    assert paused.checkpoint is not None
    path = store.root / f"{paused.checkpoint.checkpoint_id}.json"
    path.write_text(json.dumps(paused.checkpoint.to_dict()), encoding="utf-8")
    loaded = store.load(paused.checkpoint.checkpoint_id)
    assert loaded == paused.checkpoint


def test_latest_skips_corrupt_checkpoint(tmp_path: Path):
    config = load_config(load_env=False)
    task = make_demo_task()
    store = FileCheckpointStore(tmp_path / "checkpoints")
    ctx = build_context(config, "structured", task_id=task.task_id, use_state_exchange=True)
    paused = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task, ctx, checkpoint_store=store, pause_after_stage="planner"
        )
    )
    assert paused.checkpoint is not None
    (store.root / "ckpt_aaaaaaaaaaaaaaaa.json").write_text("{broken", encoding="utf-8")
    latest = store.latest(task.task_id)
    assert latest is not None
    assert latest.checkpoint_id == paused.checkpoint.checkpoint_id


def test_checkpoint_signature_includes_memory_gate_and_tokenizer_measurement(tmp_path: Path):
    config = load_config(load_env=False)
    task = make_demo_task()
    store = FileCheckpointStore(tmp_path / "checkpoints")
    ctx = build_context(config, "structured", task_id=task.task_id, use_memory=True)
    paused = asyncio.run(
        Orchestrator(build_default_agents(config)).run_task(
            task,
            ctx,
            checkpoint_store=store,
            pause_after_stage="planner",
        )
    )
    assert paused.checkpoint is not None

    changed = config.with_overrides(memory_compatibility_gate=False, tokenizer_backend="auto")
    resume_ctx = build_context(changed, "structured", task_id=task.task_id, use_memory=True)
    with pytest.raises(ValueError, match="runtime configuration"):
        asyncio.run(
            Orchestrator(build_default_agents(changed)).run_task(
                task,
                resume_ctx,
                resume_from=paused.checkpoint,
            )
        )


def test_checkpoint_load_rejects_valid_payload_under_wrong_filename(tmp_path: Path):
    import shutil

    store = FileCheckpointStore(tmp_path / "checkpoints")
    task = replace(make_demo_task(), task_id="task_ckpt_wrong_name")
    ctx = build_context(load_config(), "structured", task_id=task.task_id)
    paused = asyncio.run(
        Orchestrator(build_default_agents(ctx.config)).run_task(
            task, ctx, checkpoint_store=store, pause_after_stage="planner"
        )
    )
    assert paused.checkpoint is not None
    source = store.root / f"{paused.checkpoint.checkpoint_id}.json"
    wrong_id = "ckpt_aaaaaaaaaaaaaaaa"
    target = store.root / f"{wrong_id}.json"
    shutil.copyfile(source, target)
    with pytest.raises(CheckpointCorruptionError, match="filename/id mismatch"):
        store.load(wrong_id)


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW unavailable")
def test_checkpoint_load_rejects_symlink(tmp_path: Path):
    store = FileCheckpointStore(tmp_path / "checkpoints")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    checkpoint_id = "ckpt_bbbbbbbbbbbbbbbb"
    (store.root / f"{checkpoint_id}.json").symlink_to(outside)
    with pytest.raises(CheckpointCorruptionError, match="cannot decode checkpoint"):
        store.load(checkpoint_id)
