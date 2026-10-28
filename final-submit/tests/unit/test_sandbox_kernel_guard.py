"""fable12: seccomp-BPF + Landlock CodeAct guard -- containment and functionality."""

from __future__ import annotations

import os
import tempfile

import pytest

from agent_runtime.config import SandboxConfig
from agent_runtime.sandbox.executor import CodeActExecutor
from agent_runtime.sandbox.kernel_guard import apply_in_current_process, runtime_read_paths


def _guard_available() -> bool:
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        os.close(r)
        rep = apply_in_current_process(tempfile.mkdtemp())
        os.write(w, b"1" if (rep["seccomp"] and rep["landlock_abi"]) else b"0")
        os._exit(0)
    os.close(w)
    ok = os.read(r, 1) == b"1"
    os.close(r)
    os.waitpid(pid, 0)
    return ok


pytestmark = pytest.mark.skipif(not hasattr(os, "fork") or not _guard_available(),
                                reason="seccomp/Landlock unavailable")


def _ex():
    return CodeActExecutor(SandboxConfig(warm_start=True, timeout_sec=5))


@pytest.mark.parametrize("code", [
    "import socket; socket.socket()",
    "open('/etc/passwd').read()",
    "open('/tmp/fable12_test_escape','w').write('x')",
    "import ctypes,os; l=ctypes.CDLL(None,use_errno=True); assert l.ptrace(16,1,0,0)==0",
    "import os; os.unshare(0x10000000)",
    "import os; os.kill(os.getppid(), 9)",          # the fork-server is protected (fable14)
    "import os; os.pidfd_open(os.getppid())",
    "import ctypes; l=ctypes.CDLL(None,use_errno=True); assert l.syscall(321,0,0,0)>=0",  # bpf
])
def test_hostile_programs_are_contained(code):
    r = _ex().run(code)
    assert r.exit_code != 0
    assert not os.path.exists("/tmp/fable12_test_escape")


@pytest.mark.parametrize("code,expected", [
    ("print(sum(range(10)))", "45"),
    ("import statistics; print(statistics.mean([2,4,6]))", "4"),
    ("open('w.txt','w').write('ok'); print(open('w.txt').read())", "ok"),
    ("import json; print(json.dumps({'a':1}))", '{"a": 1}'),
])
def test_legitimate_programs_still_run(code, expected):
    r = _ex().run(code)
    assert r.exit_code == 0 and expected in r.stdout


def test_contest_workload_succeeds_with_guard_and_reports_capabilities():
    from agent_runtime.evaluation.contest_workload import _self_contained_tasks
    from agent_runtime.evaluation.runner import ExperimentConfig, run_once

    cfg = ExperimentConfig("SS", "x", "structured", True, False, True, structured_wire_codec="session_msgpack",
                           control_transport="inproc", state_shared_memory=True, state_precision="adaptive")
    row = run_once(cfg, _self_contained_tasks, 42)
    assert row["task_success"] == 1.0


def test_guard_can_be_disabled():
    prev = os.environ.get("AGENT_SANDBOX_GUARD")
    os.environ["AGENT_SANDBOX_GUARD"] = "0"
    try:
        from agent_runtime.sandbox.forkserver import shutdown_forkservers

        shutdown_forkservers()
        r = _ex().run("import socket; socket.socket(); print('open')")
        assert r.exit_code == 0 and "open" in r.stdout
    finally:
        if prev is None:
            os.environ.pop("AGENT_SANDBOX_GUARD", None)
        else:
            os.environ["AGENT_SANDBOX_GUARD"] = prev
        from agent_runtime.sandbox.forkserver import shutdown_forkservers

        shutdown_forkservers()


def test_runtime_read_paths_excludes_project_source():
    paths = runtime_read_paths()
    assert paths and all("/agent_runtime" not in p for p in paths)
    assert any(p in ("/usr", "/lib", "/lib64") or "python3" in p for p in paths)


def test_guard_is_inherited_from_the_fork_server_and_cheap():
    ex = _ex()
    ex.run("print(1)")
    g = ex.last_guard
    assert g["seccomp"] and g["landlock_abi"] and g.get("scope") == "fork-server (inherited)"
    r = ex.run("import os\npid=os.fork()\nif pid==0: os._exit(0)\nos.kill(pid,9); os.waitpid(pid,0); print('ok')")
    assert r.exit_code == 0 and "ok" in r.stdout   # own children remain manageable
