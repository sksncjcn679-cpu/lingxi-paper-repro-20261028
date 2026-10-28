"""fable12: kernel-enforced CodeAct sandbox (seccomp-BPF + Landlock), stdlib/ctypes only.

The previous sandbox isolated *resources* (rlimits, env whitelist, private workdir, process
group) but not *capabilities*: an LLM-generated program could still open sockets and exfiltrate
data, read any file the runtime user can read (other tasks, results, keys), write outside its
workdir, signal/kill the Runtime, ptrace, create namespaces or use io_uring (which bypasses
seccomp).  Two unprivileged Linux mechanisms now close these, applied in the child right before
user code runs (after ``no_new_privs``, so they also survive the cold path's ``execve``):

1. **seccomp-BPF** (classic BPF syscall filter): architecture-checked (a foreign-arch syscall
   kills the process); denies networking sockets, ptrace/process_vm_*, mount/namespace
   syscalls (unshare, setns, and ``clone`` with any CLONE_NEW* flag), module/kexec/reboot,
   bpf, perf_event_open, userfaultfd, keyctl, io_uring_*, handle-based opens; ``clone3``
   returns ENOSYS so libc falls back to the inspectable ``clone``.  Everything else is allowed,
   so ordinary Python keeps working.
2. **Landlock LSM** (ABI probed at run time): filesystem access limited to read/execute of the
   Python runtime and system library trees plus full access to the private workdir; TCP
   bind/connect denied (ABI>=4); abstract-UNIX-socket and signal scoping (ABI>=6) so the
   program cannot signal processes outside its own sandbox domain (e.g. kill the Runtime).

``GUARD_SOURCE`` is plain Python so it can be embedded in the ``python -I -S`` fork-server and
the cold launcher.  Unsupported kernels degrade gracefully and the applied layers are reported.
"""

from __future__ import annotations

GUARD_SOURCE = r'''
import ctypes as _kg_ctypes, errno as _kg_errno, platform as _kg_platform  # preloaded in the warm server
_KG = {}

class _KG_F(_kg_ctypes.Structure):
    _fields_ = [("code", _kg_ctypes.c_ushort), ("jt", _kg_ctypes.c_ubyte), ("jf", _kg_ctypes.c_ubyte), ("k", _kg_ctypes.c_uint)]

class _KG_P(_kg_ctypes.Structure):
    _fields_ = [("len", _kg_ctypes.c_ushort), ("filter", _kg_ctypes.POINTER(_KG_F))]

class _KG_RA(_kg_ctypes.Structure):
    _fields_ = [("fs", _kg_ctypes.c_uint64), ("net", _kg_ctypes.c_uint64), ("scoped", _kg_ctypes.c_uint64)]

class _KG_PB(_kg_ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed", _kg_ctypes.c_uint64), ("fd", _kg_ctypes.c_int32)]

def _kg_build_ruleset(libc, abi, rw_dir, ro_paths, report):
    import os, ctypes
    sysc = libc.syscall
    sysc.restype = ctypes.c_long
    fs_all = (1 << 13) - 1
    if abi >= 2: fs_all |= 1 << 13
    if abi >= 3: fs_all |= 1 << 14
    if abi >= 5: fs_all |= 1 << 15
    ra = _KG_RA(fs_all, 3 if abi >= 4 else 0, 3 if abi >= 6 else 0)
    size = 8 if abi < 4 else (16 if abi < 6 else 24)
    rfd = sysc(444, ctypes.byref(ra), ctypes.c_size_t(size), ctypes.c_uint(0))
    if rfd < 0:
        report["errors"].append("landlock:ruleset:%d" % ctypes.get_errno()); return -1
    EXEC, WRITE, READF, READD = 1, 2, 4, 8
    TRUNC = (1 << 14) if abi >= 3 else 0
    file_rights = EXEC | WRITE | READF | TRUNC | ((1 << 15) if abi >= 5 else 0)
    def rule(path, rights):
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return
        try:
            allowed = rights if os.path.isdir(path) else (rights & file_rights)
            if sysc(445, ctypes.c_int(rfd), ctypes.c_int(1), ctypes.byref(_KG_PB(allowed & fs_all, fd)), ctypes.c_uint(0)) != 0:
                report["errors"].append("landlock:rule:%s:%d" % (path, ctypes.get_errno()))
        finally:
            os.close(fd)
    for p in ro_paths:
        rule(p, EXEC | READF | READD)
    for p in ("/dev/null", "/dev/zero"):
        rule(p, READF | WRITE | TRUNC)
    for p in ("/dev/urandom", "/dev/random", "/etc/ld.so.cache", "/etc/localtime"):
        rule(p, READF)
    rule(rw_dir, fs_all)
    return int(rfd)

def _kernel_guard_prepare(ro_paths=()):
    """Run once in the warm server: build the BPF program and probe Landlock outside the
    per-execution critical path (children inherit the prepared objects copy-on-write)."""
    try:
        _kernel_guard(_KG.get("root"), ro_paths, want=("prepare",))
    except Exception:
        pass

def _kernel_guard(workdir, ro_paths, want=("seccomp", "landlock")):
    import ctypes, os, errno, platform
    report = {"seccomp": False, "landlock_abi": 0, "no_new_privs": False, "errors": []}
    try:
        libc = _KG.get("libc") or ctypes.CDLL(None, use_errno=True)
        _KG["libc"] = libc
    except OSError as exc:
        report["errors"].append("libc:" + str(exc)); return report
    if "prepare" not in want:
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            report["errors"].append("no_new_privs:%d" % ctypes.get_errno()); return report
        report["no_new_privs"] = True
    machine = _KG.get("machine") or platform.machine().lower()
    _KG["machine"] = machine
    if machine in ("x86_64", "amd64"):
        arch = 0xC000003E
        nr = dict(socket=41, socketpair=53, connect=42, bind=49, ptrace=101, process_vm_readv=310,
                  process_vm_writev=311, mount=165, umount2=166, pivot_root=155, chroot=161,
                  unshare=272, setns=308, kexec_load=246, kexec_file_load=320, init_module=175,
                  finit_module=313, delete_module=176, reboot=169, swapon=167, swapoff=168,
                  bpf=321, perf_event_open=298, userfaultfd=323, keyctl=250, add_key=248,
                  request_key=249, open_by_handle_at=304, name_to_handle_at=303, iopl=172,
                  ioperm=173, acct=163, quotactl=179, io_uring_setup=425, io_uring_enter=426,
                  io_uring_register=427, clone=56, clone3=435)
        sig = dict(kill=62, tkill=200, tgkill=234, pidfd_open=434, rt_sigqueueinfo=129, rt_tgsigqueueinfo=297, pidfd_send_signal=424)
    elif machine in ("aarch64", "arm64"):
        arch = 0xC00000B7
        nr = dict(socket=198, socketpair=199, connect=203, bind=200, ptrace=117, process_vm_readv=270,
                  process_vm_writev=271, mount=40, umount2=39, pivot_root=41, chroot=51, unshare=97,
                  setns=268, kexec_load=104, kexec_file_load=294, init_module=105, finit_module=273,
                  delete_module=106, reboot=142, swapon=224, swapoff=225, bpf=280,
                  perf_event_open=241, userfaultfd=282, keyctl=219, add_key=217, request_key=218,
                  open_by_handle_at=265, name_to_handle_at=264, acct=89, quotactl=60,
                  io_uring_setup=425, io_uring_enter=426, io_uring_register=427, clone=220, clone3=435)
        sig = dict(kill=129, tkill=130, tgkill=131, pidfd_open=434, rt_sigqueueinfo=138, rt_tgsigqueueinfo=240, pidfd_send_signal=424)
    else:
        arch = None
        sig = {}
        report["errors"].append("seccomp:unsupported-arch:" + machine)
    # ------------------------------------------------------------------ seccomp-BPF
    if "abi" not in _KG:
        _s = libc.syscall
        _s.restype = ctypes.c_long
        _KG["abi"] = int(_s(444, None, ctypes.c_size_t(0), ctypes.c_uint(1)))
    if ("seccomp" in want or "prepare" in want) and arch is not None and "prog" not in _KG:
        F, P = _KG_F, _KG_P
        LD, JEQ, JSET, RET = 0x20, 0x15, 0x45, 0x06
        ALLOW, ERRNO, KILL = 0x7FFF0000, 0x00050000, 0x80000000
        ns_mask = 0x7E020000  # CLONE_NEWNS|NEWCGROUP|NEWUTS|NEWIPC|NEWUSER|NEWPID|NEWNET
        prog = [F(LD, 0, 0, 4), F(JEQ, 1, 0, arch), F(RET, 0, 0, KILL), F(LD, 0, 0, 0)]
        deny = [v for k, v in nr.items() if k not in ("clone", "clone3")]
        if _KG.get("abi", 0) < 6:
            # ABI >=6 scopes signals to the Landlock sandbox domain and permits managing
            # own children. Older/absent Landlock needs the conservative syscall fallback.
            deny += [v for k, v in sig.items() if k != "pidfd_open"]
        for n in deny:
            prog += [F(JEQ, 0, 1, n), F(RET, 0, 0, ERRNO | errno.EPERM)]
        prog += [F(JEQ, 0, 1, nr["clone3"]), F(RET, 0, 0, ERRNO | errno.ENOSYS)]
        # fable14: the fork-server confines itself and children inherit its domain, so also
        # forbid signalling / pidfd-opening the protected fork-server pid (and its group).
        ppid = _KG.get("protect_pid")
        if ppid:
            targets = (int(ppid) & 0xFFFFFFFF, (-int(ppid)) & 0xFFFFFFFF)
            for call in ("kill", "tkill", "tgkill", "pidfd_open"):
                n = sig.get(call)
                if n is None:
                    continue
                # if nr == n: load args[0]; if equals a protected target -> EPERM; else reload nr
                # [nr==n ? : skip 5] [A=args0] [==t0 -> EPERM] [==t1 -> EPERM] [ja +1] [EPERM] [A=nr]
                prog += [F(JEQ, 0, 5, n), F(LD, 0, 0, 16), F(JEQ, 2, 0, targets[0]), F(JEQ, 1, 0, targets[1]),
                         F(0x05, 0, 0, 1), F(RET, 0, 0, ERRNO | errno.EPERM), F(LD, 0, 0, 0)]
        # clone(flags,...): reject any namespace-creating flag (args[0] low word at offset 16)
        prog += [F(JEQ, 0, 3, nr["clone"]), F(LD, 0, 0, 16), F(JSET, 0, 1, ns_mask),
                 F(RET, 0, 0, ERRNO | errno.EPERM), F(RET, 0, 0, ALLOW)]
        arr = (F * len(prog))(*prog)
        _KG["prog"] = (arr, P(len(prog), arr))
    if "seccomp" in want and "prog" in _KG:
        if libc.prctl(22, 2, ctypes.byref(_KG["prog"][1]), 0, 0) == 0:  # PR_SET_SECCOMP, FILTER
            report["seccomp"] = True
        else:
            report["errors"].append("seccomp:%d" % ctypes.get_errno())
    if "prepare" in want:
        if "abi" not in _KG:
            _s = libc.syscall
            _s.restype = ctypes.c_long
            _KG["abi"] = int(_s(444, None, ctypes.c_size_t(0), ctypes.c_uint(1)))
        if workdir and _KG["abi"] >= 1 and "ruleset_fd" not in _KG:
            fd = _kg_build_ruleset(libc, _KG["abi"], workdir, ro_paths, report)
            if fd >= 0:
                _KG["ruleset_fd"] = fd
        return report
    # ------------------------------------------------------------------ Landlock
    if "landlock" in want and _KG.get("ruleset_fd") is not None:
        sysc = libc.syscall
        sysc.restype = ctypes.c_long
        if sysc(446, ctypes.c_int(_KG["ruleset_fd"]), ctypes.c_uint(0)) == 0:
            report["landlock_abi"] = int(_KG["abi"])
        else:
            report["errors"].append("landlock:restrict:%d" % ctypes.get_errno())
        try:
            os.close(_KG.pop("ruleset_fd"))  # never leak the ruleset descriptor into user code
        except OSError:
            pass
        return report
    if "landlock" in want:
        sysc = libc.syscall
        sysc.restype = ctypes.c_long
        abi = _KG.get("abi")
        if abi is None:
            abi = sysc(444, None, ctypes.c_size_t(0), ctypes.c_uint(1))  # CREATE_RULESET_VERSION
        if abi < 1:
            report["errors"].append("landlock:unavailable:%d" % ctypes.get_errno())
        else:
            fs_all = (1 << 13) - 1
            if abi >= 2: fs_all |= 1 << 13        # REFER
            if abi >= 3: fs_all |= 1 << 14        # TRUNCATE
            if abi >= 5: fs_all |= 1 << 15        # IOCTL_DEV
            net = 3 if abi >= 4 else 0            # BIND_TCP | CONNECT_TCP
            scoped = 3 if abi >= 6 else 0         # ABSTRACT_UNIX_SOCKET | SIGNAL
            RA, PB = _KG_RA, _KG_PB
            size = 8 if abi < 4 else (16 if abi < 6 else 24)
            ra = RA(fs_all, net, scoped)
            rfd = sysc(444, ctypes.byref(ra), ctypes.c_size_t(size), ctypes.c_uint(0))
            if rfd < 0:
                report["errors"].append("landlock:ruleset:%d" % ctypes.get_errno())
            else:
                EXEC, WRITE, READF, READD, TRUNC = 1, 2, 4, 8, (1 << 14 if abi >= 3 else 0)
                file_rights = EXEC | WRITE | READF | TRUNC | ((1 << 15) if abi >= 5 else 0)
                def rule(path, rights):
                    try:
                        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                    except OSError:
                        return
                    try:
                        is_dir = os.path.isdir(path)
                        allowed = rights if is_dir else (rights & file_rights)
                        pb = PB(allowed & fs_all, fd)
                        if sysc(445, ctypes.c_int(rfd), ctypes.c_int(1), ctypes.byref(pb), ctypes.c_uint(0)) != 0:
                            report["errors"].append("landlock:rule:%s:%d" % (path, ctypes.get_errno()))
                    finally:
                        os.close(fd)
                for p in ro_paths:
                    rule(p, EXEC | READF | READD)
                for p in ("/dev/null", "/dev/zero"):
                    rule(p, READF | WRITE | TRUNC)
                for p in ("/dev/urandom", "/dev/random", "/etc/ld.so.cache", "/etc/localtime"):
                    rule(p, READF)
                rule(workdir, fs_all)
                if sysc(446, ctypes.c_int(rfd), ctypes.c_uint(0)) == 0:  # restrict_self
                    report["landlock_abi"] = int(abi)
                else:
                    report["errors"].append("landlock:restrict:%d" % ctypes.get_errno())
                os.close(rfd)
    return report
'''


def runtime_read_paths() -> list[str]:
    """Read/execute-only trees a sandboxed Python needs (interpreter, stdlib, system libs)."""
    import os
    import sys

    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(os.path.realpath(sys.executable)),
             "/usr", "/lib", "/lib64", "/bin"}
    roots = tuple(os.path.realpath(r) for r in (sys.base_prefix, sys.prefix, "/usr"))
    for p in sys.path:
        rp = os.path.realpath(p) if p else ""
        # only interpreter/stdlib trees -- never the project source or user directories
        if rp and os.path.isdir(rp) and rp.startswith(roots) and "packages" not in rp:
            paths.add(rp)
    return sorted(p for p in paths if os.path.exists(p))


def apply_in_current_process(workdir: str, ro_paths: list[str] | None = None) -> dict:
    """Apply the guard to the calling process (tests / probe only -- irreversible)."""
    ns: dict = {}
    exec(GUARD_SOURCE, ns)  # noqa: S102 - trusted, module-local source
    return ns["_kernel_guard"](workdir, ro_paths if ro_paths is not None else runtime_read_paths())


__all__ = ["GUARD_SOURCE", "apply_in_current_process", "runtime_read_paths"]
