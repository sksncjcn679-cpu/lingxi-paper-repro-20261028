from __future__ import annotations

from types import SimpleNamespace


def test_build_context_schedules_enabled_forkserver_warmup(monkeypatch):
    from agent_runtime.config import load_config
    from agent_runtime.runtime import builder
    from agent_runtime.sandbox import forkserver

    calls = []
    sandbox = SimpleNamespace(warm_enabled=True, config=load_config(load_env=False).sandbox)
    monkeypatch.setattr(builder, "CodeActExecutor", lambda config: sandbox)
    monkeypatch.setattr(
        forkserver,
        "warm_forkserver",
        lambda env, max_output: calls.append((env, max_output)),
    )
    config = load_config(load_env=False, control_transport="inproc")

    context = builder.build_context(config, "structured", task_id="warmup-test", use_sandbox=True)
    try:
        assert context.sandbox is sandbox
        assert len(calls) == 1
        assert calls[0][0]["AGENT_SANDBOX"] == "1"
        assert calls[0][1] > 0
    finally:
        context.channel.close()


def test_build_context_does_not_schedule_disabled_sandbox_warmup(monkeypatch):
    from agent_runtime.config import load_config
    from agent_runtime.runtime import builder
    from agent_runtime.sandbox import forkserver

    calls = []
    monkeypatch.setattr(
        builder,
        "CodeActExecutor",
        lambda config: SimpleNamespace(warm_enabled=False, config=config),
    )
    monkeypatch.setattr(forkserver, "warm_forkserver", lambda *args: calls.append(args))
    config = load_config(load_env=False, control_transport="inproc")

    context = builder.build_context(config, "structured", task_id="warmup-disabled", use_sandbox=True)
    try:
        assert calls == []
    finally:
        context.channel.close()
