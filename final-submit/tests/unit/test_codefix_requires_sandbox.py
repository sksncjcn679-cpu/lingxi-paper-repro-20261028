from __future__ import annotations

import asyncio

from agent_runtime.agents.codeact_executor import CodeActExecutorAgent
from agent_runtime.config import load_config
from agent_runtime.runtime.builder import build_context


def test_codefix_without_sandbox_fails_closed_and_does_not_execute_task_code(tmp_path):
    cfg = load_config(load_env=False)
    ctx = build_context(cfg, "structured", task_id="unsafe", use_sandbox=False)
    marker = tmp_path / "should-not-exist"
    evidence = {
        "module": "def safe_divide(a, b):\n    return a / b\n",
        "strategy": "guard_zero_division",
        "test": f"open({str(marker)!r}, 'w').write('executed')\n",
    }

    ok = asyncio.run(CodeActExecutorAgent()._handle_codefix(evidence, ctx))

    assert ok is False
    assert marker.exists() is False
    assert ctx.blackboard["execution_provenance"]["failure_class"] == "sandbox_required"
    assert ctx.metrics.snapshot.degradation_count == 1
