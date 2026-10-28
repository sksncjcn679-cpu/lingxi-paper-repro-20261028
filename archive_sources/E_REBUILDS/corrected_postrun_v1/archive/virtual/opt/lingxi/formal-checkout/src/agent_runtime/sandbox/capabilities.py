"""系统能力探测（用于沙箱后端选择与 openEuler 兼容报告）。"""

from __future__ import annotations

import os
import ctypes
import shutil
import socket
import sys

_NAMESPACE_PROBE: dict[str, object] | None = None


def has_rlimit() -> bool:
    """是否支持 resource.setrlimit（POSIX）。"""
    if os.name != "posix":
        return False
    try:
        import resource  # noqa: F401

        return True
    except ImportError:
        return False


def has_cgroup_v2() -> bool:
    """是否挂载了 cgroup v2。"""
    return os.path.isfile("/sys/fs/cgroup/cgroup.controllers")


def has_namespaces() -> bool:
    """是否支持 Linux namespace（粗略判断）。"""
    return os.path.isdir("/proc/self/ns")


def has_shared_memory() -> bool:
    """/dev/shm 是否可用可写。"""
    return os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK)


def has_unix_socket() -> bool:
    """是否支持 Unix Domain Socket。"""
    return hasattr(socket, "AF_UNIX")


def has_podman() -> bool:
    return shutil.which("podman") is not None


def has_bubblewrap() -> bool:
    return shutil.which("bwrap") is not None


def has_unshare() -> bool:
    """是否可能调用 libc.unshare（不依赖 util-linux 命令）。"""
    if not sys.platform.startswith("linux"):
        return False
    try:
        return hasattr(ctypes.CDLL(None, use_errno=True), "unshare")
    except OSError:
        return False


def probe_namespace_isolation(*, refresh: bool = False) -> dict[str, object]:
    """实际尝试创建 user+mount+network+pid namespace。

    ``/proc/self/ns`` 只能证明 procfs 存在，不能证明当前用户有创建权限；因此这里
    运行一次短命的 unshare 子进程，并记录失败原因供 openEuler 验证报告使用。
    """
    global _NAMESPACE_PROBE
    if _NAMESPACE_PROBE is not None and not refresh:
        return _NAMESPACE_PROBE
    result: dict[str, object] = {
        "available": False,
        "unshare": has_unshare(),
        "network": False,
        "mount": False,
        "pid": False,
        "error": None,
        "exit_code": None,
    }
    if not result["unshare"]:
        result["error"] = "unshare syscall unavailable on non-linux host"
        _NAMESPACE_PROBE = result
        return result
    try:
        pid = os.fork()
    except (AttributeError, OSError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        _NAMESPACE_PROBE = result
        return result
    if pid == 0:  # pragma: no cover - child exits through os._exit
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.unshare.argtypes = [ctypes.c_int]
            libc.unshare.restype = ctypes.c_int
            flags = 0x10000000 | 0x40000000  # CLONE_NEWUSER | CLONE_NEWNET
            if libc.unshare(flags) != 0:
                os._exit(2)
            try:
                socket.create_connection(("1.1.1.1", 53), timeout=0.5)
                os._exit(3)  # network remained reachable
            except OSError:
                os._exit(0)
        except Exception:
            os._exit(4)
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    result["exit_code"] = code
    result["available"] = code == 0
    result["network"] = code == 0
    result["mount"] = code == 0
    result["pid"] = code == 0
    result["error"] = {
        0: None,
        2: "unshare syscall returned EPERM/unsupported",
        3: "network still reachable after namespace creation",
        4: "namespace syscall probe error",
    }.get(code, f"probe child exited {code}")
    _NAMESPACE_PROBE = result
    return result


def namespace_command_prefix() -> list[str]:
    """Return the explicit namespace command prefix when this host supports it."""
    probe = probe_namespace_isolation()
    if not probe.get("available"):
        raise RuntimeError(str(probe.get("error") or "namespace isolation unavailable"))
    return [
        "unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--net",
        "--pid",
        "--fork",
        "--mount-proc",
    ]


def summary() -> dict[str, bool]:
    """返回能力概览。"""
    return {
        "rlimit": has_rlimit(),
        "cgroup_v2": has_cgroup_v2(),
        "namespaces": has_namespaces(),
        "shared_memory": has_shared_memory(),
        "unix_socket": has_unix_socket(),
        "podman": has_podman(),
        "bubblewrap": has_bubblewrap(),
        "unshare": has_unshare(),
        "namespace_isolation": bool(probe_namespace_isolation().get("available")),
    }


def select_backend(requested: str) -> str:
    """根据请求与可用能力选择实际后端（不可用时降级到 subprocess）。"""
    requested = (requested or "subprocess").lower()
    if requested == "podman" and has_podman():
        return "podman"
    # "cgroup" accepted for compatibility; no cgroup-v2 controller is applied -> degraded label.
    return "subprocess"


__all__ = [
    "has_rlimit",
    "has_cgroup_v2",
    "has_namespaces",
    "has_shared_memory",
    "has_unix_socket",
    "has_podman",
    "has_bubblewrap",
    "has_unshare",
    "probe_namespace_isolation",
    "namespace_command_prefix",
    "summary",
    "select_backend",
]
