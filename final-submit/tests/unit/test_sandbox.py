"""CodeAct 沙箱单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.config import SandboxConfig, load_config
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.sandbox.capabilities import probe_namespace_isolation, summary
from agent_runtime.sandbox.executor import CodeActExecutor


def test_windows_sandbox_keeps_system_environment_without_credentials(monkeypatch):
    from agent_runtime.sandbox import executor
    monkeypatch.setattr(executor.os, "name", "nt")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setenv("VLM_API_KEY", "must-not-inherit")
    env = executor._safe_env(SandboxConfig())
    assert env["SystemRoot"] == "C:\\Windows"
    assert "VLM_API_KEY" not in env
from agent_runtime.scenarios.demo import make_demo_task


def test_basic_execution_returns_stdout():
    ex = CodeActExecutor(SandboxConfig(timeout_sec=10))
    result = ex.run("print('hello sandbox')")
    assert result.ok
    assert "hello sandbox" in result.stdout
    assert result.exit_code == 0


def test_nonzero_exit_captured():
    ex = CodeActExecutor(SandboxConfig(timeout_sec=10))
    result = ex.run("import sys; sys.stderr.write('boom'); sys.exit(3)")
    assert result.exit_code == 3
    assert "boom" in result.stderr
    assert not result.ok


def test_timeout_is_enforced():
    ex = CodeActExecutor(SandboxConfig(timeout_sec=1))
    result = ex.run("import time; time.sleep(5)")
    assert result.timed_out is True
    assert not result.ok


def test_output_truncation():
    ex = CodeActExecutor(SandboxConfig(timeout_sec=10))
    result = ex.run("print('x' * (200 * 1024))")
    assert result.truncated is True


def test_capability_summary_keys():
    s = summary()
    for key in [
        "rlimit",
        "cgroup_v2",
        "shared_memory",
        "unix_socket",
        "podman",
        "namespace_isolation",
    ]:
        assert key in s


def test_namespace_probe_is_real_and_machine_readable():
    probe = probe_namespace_isolation(refresh=True)
    assert {"available", "unshare", "network", "mount", "pid", "error"} <= set(probe)
    assert probe["available"] is bool(probe["available"])
    if not probe["available"]:
        assert isinstance(probe["error"], str) and probe["error"]


def test_namespace_sandbox_fails_closed_when_host_cannot_provide_it():
    probe = probe_namespace_isolation()
    if probe["available"]:
        pytest.skip("host supports namespace isolation; positive path is tested in Linux evidence")
    result = CodeActExecutor(
        SandboxConfig(namespace_isolation=True, warm_start=False)
    ).run("print('must not run')")
    assert not result.ok
    assert result.backend == "subprocess+namespace-unavailable"


def test_namespace_sandbox_executes_when_probe_succeeds():
    if not probe_namespace_isolation().get("available"):
        pytest.skip("user/mount/network/pid namespaces unavailable")
    result = CodeActExecutor(
        SandboxConfig(namespace_isolation=True, warm_start=True)
    ).run("print('namespace sandbox')")
    assert result.ok
    assert result.backend == "subprocess+namespace"
    assert "namespace sandbox" in result.stdout


def test_codeact_in_full_pipeline():
    config = load_config(load_env=False)
    ctx = build_context(config, "structured", task_id="t", use_sandbox=True)
    orch = Orchestrator(build_default_agents(config))
    result = asyncio.run(orch.run_task(make_demo_task(), ctx))
    assert result.success is True
    assert result.outputs["answer"] == 12
    # 通过沙箱执行：记录了 sandbox 工具调用
    assert any(sig.startswith("sandbox:") for sig in ctx.metrics.snapshot._tool_signatures)
