"""Warm fork-server sandbox: same isolation contract as the cold path (v0.6.5)."""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from agent_runtime.config import SandboxConfig, load_config
from agent_runtime.sandbox.executor import CodeActExecutor
from agent_runtime.sandbox.forkserver import forkserver_supported, get_forkserver, shutdown_forkservers

pytestmark = pytest.mark.skipif(not forkserver_supported(), reason="fork-server requires Linux")


def _ex(**kw) -> CodeActExecutor:
    return CodeActExecutor(SandboxConfig(warm_start=True, **kw))


def test_warm_path_is_used_and_matches_cold_output():
    code = "import json\nprint(json.dumps({'value': sum(range(10))}))\n"
    warm = _ex().run(code)
    cold = CodeActExecutor(SandboxConfig(warm_start=False)).run(code)
    assert warm.backend == "subprocess+forkserver"
    assert cold.backend == "subprocess"
    assert warm.stdout == cold.stdout and warm.exit_code == cold.exit_code == 0


def test_exit_code_stderr_and_uncaught_exception():
    ex = _ex()
    r = ex.run("import sys; sys.stderr.write('boom'); sys.exit(3)")
    assert r.exit_code == 3 and "boom" in r.stderr
    r = ex.run("raise ValueError('bad input')")
    assert r.exit_code == 1 and "ValueError: bad input" in r.stderr


def test_timeout_kills_process_group():
    ex = _ex(timeout_sec=1)
    t0 = time.perf_counter()
    r = ex.run("while True:\n    pass\n")
    assert r.timed_out and r.exit_code < 0
    assert time.perf_counter() - t0 < 5


def test_memory_limit_applies_in_child():
    r = _ex(mem_mb=128).run("x = bytearray(1024 * 1024 * 1024)")
    assert r.exit_code != 0 and "MemoryError" in r.stderr


def test_env_whitelist_and_no_inherited_descriptors(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN_FOR_TEST", "leak-me")
    r = _ex().run(
        "import os\n"
        "print('SECRET_TOKEN_FOR_TEST' in os.environ)\n"
        # fable12: /proc is outside the Landlock domain (it would expose other processes'
        # environ/cmdline), so probe descriptors directly instead of listing /proc/self/fd.
        "def _open(fd):\n"
        "    try:\n        os.fstat(fd); return True\n"
        "    except OSError:\n        return False\n"
        "print(sorted(fd for fd in range(0, 256) if _open(fd)))\n"
    )
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "False"
    fds = [int(x) for x in lines[1].strip("[]").split(",")]
    assert set(fds) <= {0, 1, 2}


def test_stdin_and_output_truncation():
    ex = _ex()
    assert ex.run("print(input())", stdin="hello\n").stdout.strip() == "hello"
    r = ex.run("print('x' * 200000)")
    assert r.truncated and len(r.stdout) < 70000


def test_background_grandchildren_are_cleaned_up():
    marker = f"sleep_marker_{os.getpid()}_{time.time_ns()}"
    r = _ex().run(
        "import os, time\n"
        f"if os.fork() == 0:\n    time.sleep(30)  # {marker}\n    os._exit(0)\n"
        "print('parent done')\n"
    )
    assert r.stdout.strip() == "parent done"
    time.sleep(0.2)
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout
    assert out.strip() == ""


def test_workdir_is_private_and_removed():
    r = _ex().run("import os; open('f.txt','w').write('x'); print(os.getcwd())")
    workdir = r.stdout.strip()
    assert os.path.basename(workdir).startswith("codeact_")
    assert not os.path.exists(workdir)


def test_crashed_server_falls_back_then_recovers():
    ex = _ex()
    assert ex.run("print(1)").backend == "subprocess+forkserver"
    from agent_runtime.sandbox.executor import _MAX_OUTPUT_BYTES, _safe_env

    server = get_forkserver(_safe_env(ex.config), _MAX_OUTPUT_BYTES)
    os.killpg(server.pid, 9)
    time.sleep(0.1)
    r = ex.run("print(2)")
    assert r.stdout.strip() == "2" and r.exit_code == 0  # restarted or cold fallback, never wrong
    assert ex.run("print(3)").backend == "subprocess+forkserver"


def test_warm_start_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("AGENT_SANDBOX_WARM", "false")
    cfg = load_config(load_env=False)
    assert cfg.sandbox.warm_start is False
    assert CodeActExecutor(cfg.sandbox).run("print(1)").backend == "subprocess"


def test_warm_path_is_faster_than_cold():
    code = "print(1)"
    warm, cold = _ex(), CodeActExecutor(SandboxConfig(warm_start=False))
    warm.run(code)
    cold.run(code)

    def best(ex):
        vals = []
        for _ in range(5):
            t0 = time.perf_counter()
            ex.run(code)
            vals.append(time.perf_counter() - t0)
        return min(vals)

    assert best(warm) < best(cold)


def test_shutdown_is_idempotent():
    _ex().run("print(1)")
    shutdown_forkservers()
    shutdown_forkservers()
    assert _ex().run("print(4)").stdout.strip() == "4"


def test_background_warmup_starts_pooled_server_without_user_execution(monkeypatch):
    import threading

    from agent_runtime.sandbox import forkserver as fs

    started = threading.Event()
    release = threading.Event()
    calls = []

    class FakeServer:
        def warm(self):
            calls.append("warm")
            started.set()
            release.wait(1)

    monkeypatch.setattr(fs, "_POOL_WARMERS", {})
    monkeypatch.setattr(fs, "forkserver_supported", lambda: True)
    monkeypatch.setattr(fs, "get_forkserver", lambda *_args: FakeServer())
    fs.warm_forkserver({"PATH": "/usr/bin"}, 1024)
    assert started.wait(1)
    fs.warm_forkserver({"PATH": "/usr/bin"}, 1024)
    assert calls == ["warm"]
    release.set()
    next(iter(fs._POOL_WARMERS.values())).join(1)


def test_background_warmup_skips_unsupported_platform(monkeypatch):
    from agent_runtime.sandbox import forkserver as fs

    monkeypatch.setattr(fs, "_POOL_WARMERS", {})
    monkeypatch.setattr(fs, "forkserver_supported", lambda: False)
    fs.warm_forkserver({"PATH": "/usr/bin"}, 1024)
    assert fs._POOL_WARMERS == {}
