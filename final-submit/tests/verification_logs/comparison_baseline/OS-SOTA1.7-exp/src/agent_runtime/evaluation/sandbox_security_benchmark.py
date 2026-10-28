"""fable12 suite: kernel-enforced CodeAct sandbox -- attacker containment, functionality, cost.

The contest rewards a "轻量、可隔离" CodeAct sandbox for LLM-generated code.  Resource limits
alone do not stop data exfiltration or lateral movement.  This suite runs a battery of hostile
programs (network, arbitrary file read/write, signalling the runtime, ptrace, namespace/mount,
bpf, io_uring) and confirms each is denied, that legitimate Python still runs, and reports the
per-execution overhead of the guard.
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

from agent_runtime.config import SandboxConfig
from agent_runtime.sandbox.executor import CodeActExecutor

_SECRET = None


def _attacks() -> dict[str, tuple[str, str]]:
    global _SECRET
    fd, _SECRET = tempfile.mkstemp(prefix="fable12_secret_")
    os.write(fd, b"TOPSECRET-CONTEST-KEY")
    os.close(fd)
    return {
        "network_socket": ("import socket; socket.socket(socket.AF_INET, socket.SOCK_STREAM)", "block"),
        "network_connect_raw": ("import socket,struct\ns=socket.socket()\ns.connect(('10.255.255.1',9))", "block"),
        "read_secret_file": (f"open({_SECRET!r}).read()", "block"),
        "read_etc_passwd": ("open('/etc/passwd').read()", "block"),
        "write_outside_workdir": ("open('/tmp/fable12_pwned','w').write('x')", "block"),
        "signal_runtime": (f"import os; os.kill({os.getpid()}, 9)", "block"),
        "signal_forkserver": ("import os; os.kill(os.getppid(), 9)", "block"),
        "pidfd_forkserver": ("import os; os.pidfd_open(os.getppid())", "block"),
        "ptrace_attach": ("import ctypes; l=ctypes.CDLL(None,use_errno=True)\n"
                          "assert l.ptrace(16,1,0,0)==0, 'ptrace denied'", "block"),
        "unshare_userns": ("import os; os.unshare(0x10000000)", "block"),
        "mount_proc": ("import ctypes; l=ctypes.CDLL(None,use_errno=True)\n"
                       "assert l.mount(b'proc',b'/mnt',b'proc',0,None)==0,'mount denied'", "block"),
        "bpf_syscall": ("import ctypes; l=ctypes.CDLL(None,use_errno=True)\n"
                        "assert l.syscall(321,0,0,0)>=0,'bpf denied'", "block"),
        "io_uring_setup": ("import ctypes; l=ctypes.CDLL(None,use_errno=True)\n"
                           "assert l.syscall(425,1,0)>=0,'io_uring denied'", "block"),
        # legitimate programs the sandbox must keep running
        "ok_arithmetic": ("import json; print(json.dumps({'v': sum(range(100))}))", "allow"),
        "ok_stdlib": ("import statistics, decimal, re\nprint(statistics.mean([1,2,3]), decimal.Decimal(1)/7)", "allow"),
        "ok_workdir_io": ("open('scratch.txt','w').write('ok'); print(open('scratch.txt').read())", "allow"),
        "ok_subprocess_fork": ("import os\npid=os.fork()\nif pid==0: os._exit(3)\nprint(os.waitpid(pid,0)[1]>>8)", "allow"),
    }


def _classify(code: str, intent: str, ex: CodeActExecutor) -> dict[str, Any]:
    r = ex.run(code)
    contained = r.exit_code != 0
    return {"intent": intent, "exit_code": r.exit_code,
            "contained": contained if intent == "block" else None,
            "ran_ok": r.exit_code == 0 if intent == "allow" else None,
            "evidence": (r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.stdout.strip())[:120]}


def run_sandbox_security_benchmark(repeat: int = 40) -> dict[str, Any]:
    from agent_runtime.sandbox.kernel_guard import apply_in_current_process  # noqa: F401  (import proves availability)

    guarded = CodeActExecutor(SandboxConfig(warm_start=True, timeout_sec=5))
    # capability report from a probe execution
    probe = guarded.run("print(1)")
    guard_report = getattr(guarded, "last_guard", None)
    if not guard_report or not guard_report.get("seccomp") or not guard_report.get("landlock_abi"):
        # Linux alone is insufficient: never launch the hostile battery without filesystem
        # and syscall containment. In particular, this prevents killing the benchmark host.
        return {"suite": "sandbox_security", "status": "unsupported_security_layer",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "reason": "seccomp and Landlock required; hostile programs not executed",
                "guard_report": guard_report, "attacks_blocked": "not_run", "legitimate_ran": "probe_only",
                "all_contained": False, "all_functional": probe.exit_code == 0, "no_file_escaped": None,
                "warm_overhead_ms": {"guard_off": 0, "guard_on": 0, "delta": 0}, "results": {}}
    attacks = _attacks()
    results = {name: _classify(code, intent, guarded) for name, (code, intent) in attacks.items()}
    blocked = [n for n, r in results.items() if r["intent"] == "block"]
    allowed = [n for n, r in results.items() if r["intent"] == "allow"]
    contained = sum(1 for n in blocked if results[n]["contained"])
    functional = sum(1 for n in allowed if results[n]["ran_ok"])

    # overhead: guard on vs off, warm path
    def bench(guard: str) -> float:
        prev = os.environ.get("AGENT_SANDBOX_GUARD")
        os.environ["AGENT_SANDBOX_GUARD"] = guard
        try:
            from agent_runtime.sandbox.forkserver import shutdown_forkservers

            shutdown_forkservers()
            ex = CodeActExecutor(SandboxConfig(warm_start=True, timeout_sec=5))
            for _ in range(5):
                ex.run("print(1)")
            ts = [(_t(ex)) for _ in range(repeat)]
            return statistics.median(ts)
        finally:
            if prev is None:
                os.environ.pop("AGENT_SANDBOX_GUARD", None)
            else:
                os.environ["AGENT_SANDBOX_GUARD"] = prev
            from agent_runtime.sandbox.forkserver import shutdown_forkservers

            shutdown_forkservers()

    off_ms = bench("0")
    on_ms = bench("seccomp,landlock")
    if _SECRET and os.path.exists(_SECRET):
        os.remove(_SECRET)
    pwned = os.path.exists("/tmp/fable12_pwned")
    if pwned:
        os.remove("/tmp/fable12_pwned")
    return {
        "suite": "sandbox_security",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "guard_report": guard_report,
        "attacks_blocked": f"{contained}/{len(blocked)}",
        "legitimate_ran": f"{functional}/{len(allowed)}",
        "all_contained": contained == len(blocked),
        "all_functional": functional == len(allowed),
        "no_file_escaped": not pwned,
        "warm_overhead_ms": {"guard_off": off_ms, "guard_on": on_ms, "delta": on_ms - off_ms},
        "results": results,
    }


def _t(ex: CodeActExecutor) -> float:
    t = time.perf_counter()
    ex.run("print(1)")
    return (time.perf_counter() - t) * 1000


def render_sandbox_security(p: dict[str, Any]) -> str:
    g = p.get("guard_report") or {}
    L = ["# Kernel-enforced CodeAct sandbox (seccomp-BPF + Landlock)", "",
         f"> guard: seccomp={g.get('seccomp')}, landlock_abi={g.get('landlock_abi')}, no_new_privs={g.get('no_new_privs')}.", "",
         f"- Hostile programs contained: **{p['attacks_blocked']}**; legitimate programs still run: **{p['legitimate_ran']}**.",
         f"- No file written outside the workdir: **{p['no_file_escaped']}**.",
         f"- Warm per-execution overhead: guard off **{p['warm_overhead_ms']['guard_off']:.2f} ms**, "
         f"on **{p['warm_overhead_ms']['guard_on']:.2f} ms** (delta **{p['warm_overhead_ms']['delta']:.2f} ms**).",
         "", "| Program | Intent | Exit | Outcome | Evidence |", "| --- | --- | ---: | --- | --- |"]
    for name, r in p["results"].items():
        outcome = ("contained" if r["contained"] else "ESCAPED") if r["intent"] == "block" else ("ran" if r["ran_ok"] else "BROKEN")
        L.append(f"| {name} | {r['intent']} | {r['exit_code']} | {outcome} | {r['evidence']} |")
    L += ["", "- Guard is opt-out (`AGENT_SANDBOX_GUARD=0`) and degrades gracefully on kernels without seccomp/Landlock.", ""]
    return "\n".join(L)


def run_sandbox_security_cli(args) -> int:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest

    import sys as _sys

    if not _sys.platform.startswith("linux") and os.environ.get("AGENT_SANDBOX_SECURITY_FORCE") != "1":
        # OS-SOTA1.1: on Windows the hostile battery is NOT contained (no seccomp/Landlock) --
        # the OS-SOTA1 rerun died with exit 9 because the "signal the runtime" program killed
        # the benchmark itself.  Never execute hostile programs on an unguarded host.
        out = _allocate_result_dir("results", "sandbox_security")
        payload = {"suite": "sandbox_security", "status": "unsupported_platform", "env": _env_info(),
                   "reason": "kernel guard (seccomp-BPF + Landlock) requires Linux; hostile programs not executed"}
        (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "report.md").write_text(f"# Sandbox security\n\nUNSUPPORTED: {payload['reason']}\n", encoding="utf-8")
        _write_checksum_manifest(out)
        print(f"UNSUPPORTED: {payload['reason']}\n结果已保存到: {out}")
        return 0
    payload = run_sandbox_security_benchmark(repeat=getattr(args, "repeat", 40))
    payload["env"] = _env_info()
    out = _allocate_result_dir("results", "sandbox_security")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    text = render_sandbox_security(payload) + f"\nSource SHA-256: `{payload['env']['source_tree_sha256']}`\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    _write_checksum_manifest(out)
    print(text)
    return 0 if payload["all_contained"] and payload["all_functional"] and payload["no_file_escaped"] else 1
