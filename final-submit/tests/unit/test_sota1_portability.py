"""OS-SOTA1: non-Linux hosts degrade instead of crashing (11 non-zero exits in the fable16 Windows rerun)."""
from __future__ import annotations

import json
import socket
import types

from agent_runtime.config import load_config


def test_uname_helper_is_portable(monkeypatch):
    import os

    from agent_runtime.state_exchange import fd_passing

    monkeypatch.delattr(os, "uname", raising=False)
    assert fd_passing._uname().machine is not None
    fd_passing.policy_thresholds()  # crashed with AttributeError on Windows in fable16


def test_uds_request_degrades_to_inproc_without_af_unix(monkeypatch):
    from agent_runtime.runtime.builder import _degrade_unsupported_transport

    cfg = load_config(load_env=False).with_overrides(control_transport="uds", state_fd_handoff=True)
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)
    monkeypatch.delenv("AGENT_STRICT_TRANSPORT", raising=False)
    out = _degrade_unsupported_transport(cfg)
    assert out.control_transport == "inproc" and out.state_fd_handoff is False
    monkeypatch.setenv("AGENT_STRICT_TRANSPORT", "1")
    assert _degrade_unsupported_transport(cfg).control_transport == "uds"


def test_linux_keeps_uds():
    from agent_runtime.runtime.builder import _degrade_unsupported_transport

    cfg = load_config(load_env=False).with_overrides(control_transport="uds")
    if hasattr(socket, "AF_UNIX"):
        assert _degrade_unsupported_transport(cfg).control_transport == "uds"


def test_cli_reports_unsupported_on_non_linux(monkeypatch, tmp_path):
    import sys

    from agent_runtime import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")

    def boom(_args):
        raise RuntimeError("AF_UNIX is not available on this platform")

    args = types.SimpleNamespace(command="fd-passing-benchmark", func=boom)
    assert cli._run_guarded(args) == 0
    found = list((tmp_path / "results").glob("*fd_passing_benchmark_unsupported/summary.json"))
    assert found and json.loads(found[0].read_text())["status"] == "unsupported_platform"


def test_cli_still_raises_on_linux(monkeypatch):
    import sys

    import pytest

    from agent_runtime import cli

    monkeypatch.setattr(sys, "platform", "linux")
    args = types.SimpleNamespace(command="x", func=lambda a: (_ for _ in ()).throw(RuntimeError("real bug")))
    with pytest.raises(RuntimeError):
        cli._run_guarded(args)


def test_environment_checks_pending_on_non_linux(monkeypatch):
    import sys

    from agent_runtime.evaluation.competition_scorecard import _environment_check_ids

    monkeypatch.setattr(sys, "platform", "win32")
    assert {"openeuler", "platform", "ipc_integrity"} <= _environment_check_ids()
    monkeypatch.setattr(sys, "platform", "linux")
    assert _environment_check_ids() == {"openeuler"}


def test_deliverable_path_prefers_ascii_then_legacy(tmp_path):
    from agent_runtime import RELEASE_NAME
    from agent_runtime.evaluation.competition_scorecard import deliverable_path

    d = tmp_path / "deliverables"
    d.mkdir()
    assert deliverable_path(tmp_path, "ppt").name == f"defense_deck_{RELEASE_NAME}.pptx"  # default target
    (d / f"答辩PPT_{RELEASE_NAME}.pptx").write_bytes(b"x")
    assert deliverable_path(tmp_path, "ppt").name.startswith("答辩PPT_")
    (d / f"defense_deck_{RELEASE_NAME}.pptx").write_bytes(b"x")
    assert deliverable_path(tmp_path, "ppt").name.startswith("defense_deck_")


def test_sandbox_security_never_runs_hostile_programs_unguarded(monkeypatch, tmp_path):
    import sys

    from agent_runtime.evaluation import runner
    from agent_runtime.evaluation import sandbox_security_benchmark as sb

    monkeypatch.setattr(runner, "_env_info", lambda **k: {"source_tree_sha256": "x"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("AGENT_SANDBOX_SECURITY_FORCE", raising=False)
    monkeypatch.setattr(sb, "run_sandbox_security_benchmark", lambda **k: (_ for _ in ()).throw(AssertionError("executed")))
    assert sb.run_sandbox_security_cli(types.SimpleNamespace(repeat=1)) == 0
    found = list((tmp_path / "results").glob("*sandbox_security/summary.json"))
    assert json.loads(found[0].read_text())["status"] == "unsupported_platform"


def test_fd_passing_unsupported_exit_code_depends_on_platform(monkeypatch, tmp_path):
    import sys

    from agent_runtime.state_exchange import fd_passing as fp

    monkeypatch.chdir(tmp_path)
    fake = {"supported": False, "env": {"source_tree_sha256": "x"}}
    monkeypatch.setattr(fp, "run_fd_passing_benchmark", lambda **k: fake)
    monkeypatch.setattr(fp, "render_fd_passing", lambda p: "unsupported")
    monkeypatch.setattr(sys, "platform", "win32")
    assert fp.run_fd_passing_cli(types.SimpleNamespace(repeat=1)) == 0
    monkeypatch.setattr(sys, "platform", "linux")
    assert fp.run_fd_passing_cli(types.SimpleNamespace(repeat=1)) == 2


def test_rt_launcher_runs_this_checkout_even_with_bogus_pythonpath(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    fake = tmp_path / "agent_runtime"
    fake.mkdir()
    (fake / "__init__.py").write_text("raise SystemExit('stale package imported')\n")
    env = {**os.environ, "PYTHONPATH": f"{tmp_path}:{root / 'src'}"}  # stale first, ':'-joined
    r = subprocess.run([sys.executable, str(root / "scripts" / "rt.py"), "version"], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "agent-runtime" in r.stdout
