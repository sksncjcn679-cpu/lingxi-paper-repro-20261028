"""CodeAct 执行器与沙箱。

让 LLM 生成的 Python 代码在受限环境中执行并回传 stdout / stderr / exit code /
资源占用 / artifact 引用。隔离手段（按可用性自动降级）：

- subprocess + timeout（基础，跨平台）；
- `resource.setrlimit`：CPU / 内存(AS) / 文件大小 / 打开文件 / 进程数（POSIX）；
- 独立工作目录（tempdir）+ 隔离环境变量白名单 + Python `-I -S` 隔离模式（不加载 site-packages）；
- 独立进程组 + 超时强制清理，避免残留进程；
- 输出大小限制；
- openEuler 上可选 Podman（`--network=none`）作为更强隔离，缺失自动降级。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

from agent_runtime.config import SandboxConfig
from agent_runtime.sandbox.capabilities import (
    has_rlimit,
    namespace_command_prefix,
    probe_namespace_isolation,
    select_backend,
)
from agent_runtime.sandbox.forkserver import ForkServerError, forkserver_supported, get_forkserver
from agent_runtime.sandbox.result import ExecutionResult

_MAX_OUTPUT_BYTES = 64 * 1024


def _cold_guard_snippet() -> str:
    """fable12: the cold launcher applies the same kernel guard before ``execve`` (seccomp and
    Landlock survive exec under no_new_privs)."""
    import json as _json

    from agent_runtime.sandbox.forkserver import _guard_layers, _guard_ro_paths
    from agent_runtime.sandbox.kernel_guard import GUARD_SOURCE

    layers = _guard_layers()
    if not layers:
        return ""
    return GUARD_SOURCE + f"\n_kernel_guard(os.getcwd(), {_json.dumps(_guard_ro_paths())}, tuple({_json.dumps(layers)}))\n"


_MAIN_BOOTSTRAP = "import sys; sys.argv=['main.py']; exec(compile(open('main.py', encoding='utf-8').read(), 'main.py', 'exec'), {'__name__':'__main__', '__file__':'main.py', '__builtins__':__builtins__})"

def _write_rlimit_launcher(path: str, cfg: SandboxConfig) -> None:
    """Write a tiny POSIX launcher that applies limits, then ``exec``s target Python.

    ``subprocess.Popen(preexec_fn=...)`` is intentionally avoided: Python documents
    that it is unsafe in threaded applications and may deadlock before ``exec``.
    The launcher runs as a normal child process, applies limits in its own process,
    then replaces itself with the isolated target interpreter.
    """
    code = f"""
import contextlib
import math
import os
import resource
import sys

# Give the target the configured CPU budget without charging the implementation
# overhead of starting this launcher itself. RLIMIT_CPU is cumulative per process
# and therefore survives exec().
usage = resource.getrusage(resource.RUSAGE_SELF)
used_cpu = usage.ru_utime + usage.ru_stime
soft_cpu = max(1, int(math.ceil(used_cpu + {int(cfg.cpu_sec)})))
hard_cpu = soft_cpu + 1

limits = [
    (resource.RLIMIT_CPU, (soft_cpu, hard_cpu)),
    (resource.RLIMIT_AS, ({int(cfg.mem_mb)} * 1024 * 1024, {int(cfg.mem_mb)} * 1024 * 1024)),
    (resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024)),
    (resource.RLIMIT_NOFILE, (64, 64)),
]
for res, value in limits:
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(res, value)
with contextlib.suppress(ValueError, OSError, AttributeError):
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))

{_cold_guard_snippet()}
os.execve(sys.executable, [sys.executable, "-I", "-S", "-X", "utf8", "-c", {_MAIN_BOOTSTRAP!r}], os.environ)
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


def _safe_env(cfg: SandboxConfig) -> dict[str, str]:
    """环境变量白名单。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "AGENT_SANDBOX": "1",
    }
    if os.name == "nt":
        # CPython uses Windows system services for entropy during startup. A stripped
        # environment without SystemRoot can fail before the user script executes.
        for name in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
            if name in os.environ:
                env[name] = os.environ[name]
    if cfg.network == "disabled":
        # 最佳努力：清除代理并标记；强网络隔离依赖 namespace / Podman。
        env["NO_PROXY"] = "*"
        env["AGENT_SANDBOX_NETWORK"] = "disabled"
    return env


def _peak_rss_delta_mb(before: int) -> float:
    if not has_rlimit():
        return 0.0
    import resource

    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    delta = max(0, after - before)
    # Linux: ru_maxrss 单位 KB；macOS: 单位 bytes。
    if sys.platform == "darwin":
        return delta / (1024 * 1024)
    return delta / 1024


class CodeActExecutor:
    """在受限环境执行 Python 代码。"""

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()
        self.backend = select_backend(self.config.backend)
        self.degraded = self.backend != (self.config.backend or "subprocess").lower()
        self.warm_enabled = bool(
            getattr(self.config, "warm_start", False)
            and has_rlimit()
            and forkserver_supported()
            and not getattr(self.config, "namespace_isolation", False)
        )
        self.namespace_requested = bool(getattr(self.config, "namespace_isolation", False))
        self.namespace_error = None
        if self.namespace_requested:
            probe = probe_namespace_isolation()
            if not probe.get("available"):
                self.namespace_error = str(probe.get("error") or "namespace isolation unavailable")
        self.warm_executions = 0
        self.warm_fallbacks = 0
        self.last_warm_error: str | None = None

    def run(self, code: str, *, stdin: str = "") -> ExecutionResult:
        """执行代码并返回结果。"""
        if self.namespace_error is not None:
            return ExecutionResult(
                stdout="",
                stderr=self.namespace_error,
                exit_code=-1,
                timed_out=False,
                duration_sec=0.0,
                backend="subprocess+namespace-unavailable",
                error=self.namespace_error,
            )
        if self.backend == "podman":
            try:
                return self._run_podman(code, stdin)
            except Exception:  # noqa: BLE001 - Podman 失败降级 subprocess
                self.degraded = True
                self.backend = "subprocess"
        if self.warm_enabled:
            try:
                return self._run_forkserver(code, stdin)
            except ForkServerError as exc:
                # Correctness never depends on the warm path: record and fall back to cold.
                self.warm_fallbacks += 1
                self.last_warm_error = str(exc)
        return self._run_subprocess(code, stdin)

    # ------------------------------------------------------------------ warm fork-server
    def _run_forkserver(self, code: str, stdin: str) -> ExecutionResult:
        server = get_forkserver(_safe_env(self.config), _MAX_OUTPUT_BYTES)
        start = time.perf_counter()
        reply = server.execute(
            code,
            stdin=stdin,
            timeout_sec=self.config.timeout_sec,
            cpu_sec=self.config.cpu_sec,
            mem_mb=self.config.mem_mb,
        )
        self.warm_executions += 1
        self.last_guard = reply.get("guard")
        if self.last_guard and self.last_guard.get("seccomp") and self.last_guard.get("landlock_abi"):
            self.guarded_executions = getattr(self, "guarded_executions", 0) + 1
        maxrss_kb = float(reply.get("maxrss_kb", 0) or 0)
        return ExecutionResult(
            stdout=str(reply.get("stdout", "")),
            stderr=str(reply.get("stderr", "")),
            exit_code=int(reply.get("exit_code", -1)),
            timed_out=bool(reply.get("timed_out", False)),
            duration_sec=time.perf_counter() - start,
            peak_rss_mb=maxrss_kb / 1024.0,
            truncated=bool(reply.get("truncated", False)),
            backend="subprocess+forkserver",
            error=None,
        )

    # ------------------------------------------------------------------ 子进程
    def _run_subprocess(self, code: str, stdin: str) -> ExecutionResult:
        workdir = tempfile.mkdtemp(prefix="codeact_")
        script = os.path.join(workdir, "main.py")
        stdout_path = os.path.join(workdir, "stdout.log")
        stderr_path = os.path.join(workdir, "stderr.log")
        launcher_path = os.path.join(workdir, "_sandbox_launcher.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(code)

        before_rss = 0
        if has_rlimit():
            import resource

            before_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            _write_rlimit_launcher(launcher_path, self.config)

        target_cmd = [sys.executable, "-I", "-S", "-X", "utf8", "_sandbox_launcher.py"] if has_rlimit() else [
            sys.executable,
            "-I",
            "-S",
            "-X", "utf8", "-c", _MAIN_BOOTSTRAP,
        ]
        cmd = (
            namespace_command_prefix() + target_cmd
            if self.namespace_requested
            else target_cmd
        )
        popen_kwargs: dict = {
            "cwd": workdir,
            "env": _safe_env(self.config),
            "stdin": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
        }
        if os.name == "posix":
            # Python explicitly recommends this over calling os.setsid() from preexec_fn.
            popen_kwargs["start_new_session"] = True
        elif os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        start = time.perf_counter()
        timed_out = False
        error: str | None = None
        out = err = ""
        exit_code = -1
        try:
            # File-backed capture keeps parent memory bounded even when generated code
            # writes very large output. We only read the capped prefix afterwards.
            with open(stdout_path, "wb") as stdout_f, open(stderr_path, "wb") as stderr_f:
                proc = subprocess.Popen(  # noqa: S603 - isolated interpreter + resource limits
                    cmd, stdout=stdout_f, stderr=stderr_f, **popen_kwargs
                )
                try:
                    proc.communicate(input=stdin, timeout=self.config.timeout_sec)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._kill(proc)
                    proc.communicate()
                exit_code = proc.returncode if proc.returncode is not None else -1
            out, truncated_o = self._read_capped_file(stdout_path)
            err, truncated_e = self._read_capped_file(stderr_path)
        except Exception as exc:  # noqa: BLE001
            truncated_o = truncated_e = False
            error = f"{type(exc).__name__}: {exc}"
        finally:
            duration = time.perf_counter() - start
            self._cleanup_dir(workdir)

        return ExecutionResult(
            stdout=out,
            stderr=err,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_sec=duration,
            peak_rss_mb=_peak_rss_delta_mb(before_rss),
            truncated=truncated_o or truncated_e,
            backend="subprocess+namespace" if self.namespace_requested else "subprocess",
            error=error,
        )

    # ------------------------------------------------------------------ Podman
    def _run_podman(self, code: str, stdin: str) -> ExecutionResult:
        start = time.perf_counter()
        cmd = [
            "podman",
            "run",
            "--rm",
            "-i",
            "--network=none",
            f"--memory={self.config.mem_mb}m",
            "--pids-limit=64",
            "python:3.11-slim",
            "python",
            "-I",
            "-",
        ]
        timed_out = False
        error: str | None = None
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                out, err = proc.communicate(input=code, timeout=self.config.timeout_sec)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                out, err = proc.communicate()
            exit_code = proc.returncode if proc.returncode is not None else -1
        except Exception as exc:  # noqa: BLE001
            out, err, exit_code, error = "", "", -1, f"{type(exc).__name__}: {exc}"

        duration = time.perf_counter() - start
        out, to = self._truncate(out)
        err, te = self._truncate(err)
        return ExecutionResult(
            stdout=out,
            stderr=err,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_sec=duration,
            truncated=to or te,
            backend="podman",
            error=error,
        )

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _read_capped_file(path: str) -> tuple[str, bool]:
        """Read at most the configured capture prefix from a file-backed stream."""
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                data = f.read(_MAX_OUTPUT_BYTES)
        except OSError:
            return "", False
        text = data.decode("utf-8", errors="ignore")
        if size > _MAX_OUTPUT_BYTES:
            text += "\n…[truncated]"
            return text, True
        return text, False

    @staticmethod
    def _truncate(text: str) -> tuple[str, bool]:
        data = (text or "").encode("utf-8")
        if len(data) <= _MAX_OUTPUT_BYTES:
            return text or "", False
        return data[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore") + "\n…[truncated]", True

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), 9)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass

    @staticmethod
    def _cleanup_dir(path: str) -> None:
        import shutil

        shutil.rmtree(path, ignore_errors=True)


__all__ = ["CodeActExecutor"]
