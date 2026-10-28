"""Kernel capability probe and backend selection (opus3).

Different kernels expose different semantics for the same syscalls (e.g. read-only
``MAP_SHARED`` mappings of write-sealed memfds are rejected before Linux 6.7).  Instead of
assuming, the runtime *measures* what the host allows and records which backend each subsystem
will use.  The probe performs real syscalls; nothing is inferred from version strings.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone
from typing import Any


def _try(fn):
    try:
        return bool(fn()), None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def run_os_probe() -> dict[str, Any]:
    from agent_runtime.evaluation.runner import _env_info
    from agent_runtime.sandbox.capabilities import (
        has_cgroup_v2,
        has_rlimit,
        probe_namespace_isolation,
    )
    from agent_runtime.sandbox.forkserver import forkserver_supported
    from agent_runtime.state_exchange.fd_passing import (
        DEFAULT_FD_HANDOFF_MIN_BYTES,
        calibrate_fd_policy,
        policy_thresholds,
        probe_fd_capabilities,
    )

    checks: dict[str, Any] = {}

    def pidfd():
        fd = os.pidfd_open(os.getpid())
        os.close(fd)
        return True

    def af_unix():
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        a.sendall(b"x")
        ok = b.recv(1) == b"x"
        a.close()
        b.close()
        return ok

    def dev_shm():
        from multiprocessing import shared_memory

        shm = shared_memory.SharedMemory(create=True, size=64)
        shm.close()
        shm.unlink()
        return True

    for name, fn in (("rlimit", has_rlimit), ("cgroup_v2", has_cgroup_v2), ("pidfd_open", pidfd),
                     ("af_unix", af_unix), ("posix_shm", dev_shm), ("fork_server", forkserver_supported)):
        ok, err = _try(fn)
        checks[name] = {"ok": ok, **({"error": err} if err else {})}
    namespace = probe_namespace_isolation(refresh=True)
    fd = probe_fd_capabilities(refresh=True)
    # fable5: measure this host's copy/materialise costs instead of trusting hard-coded fan-out
    # thresholds, and cache the result for choose_handoff().
    calibration = calibrate_fd_policy(reps=9) if fd.get("supported") else {"supported": False}
    thresholds = policy_thresholds()
    selection = {
        "sandbox": "subprocess+forkserver" if checks["fork_server"]["ok"] and checks["rlimit"]["ok"] else "subprocess",
        "sandbox_isolation": (
            "rlimit+setsid+user/mount/net/pid namespace"
            if namespace.get("available")
            else "rlimit+setsid (namespace unavailable: degraded)"
        ),
        "control_plane": "AF_UNIX verified router" if checks["af_unix"]["ok"] else "in-process",
        "numeric_state_plane": "POSIX shared memory" if checks["posix_shm"]["ok"] else "in-process",
        "large_state_handoff": (
            f"sealed memfd + SCM_RIGHTS via {fd['consumer_read_mode']} for >= {DEFAULT_FD_HANDOFF_MIN_BYTES:,} B"
            f" and fan-out >= {thresholds['k_copy_in']} (copy-in) / {thresholds['k_in_place']} (in-place),"
            f" thresholds from {thresholds['source']}"
            if fd.get("supported") else "copy"
        ),
    }
    kernel = platform.release()
    return {"suite": "os_probe", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "env": _env_info(),
            "kernel": kernel, "python": sys.version.split()[0], "checks": checks,
            "namespace": namespace,
            "memfd": fd, "fd_calibration": calibration, "fd_policy": thresholds, "selection": selection}


def render_os_probe(p: dict[str, Any]) -> str:
    lines = [f"# OS capability probe — kernel {p['kernel']} / Python {p['python']}", "", "| Capability | Available | Note |", "| --- | --- | --- |"]
    for k, v in p["checks"].items():
        lines.append(f"| {k} | {v['ok']} | {v.get('error', '')} |")
    m = p["memfd"]
    for k in ("seal", "mmap_shared_ro", "mmap_private_ro", "pread", "scm_rights"):
        lines.append(f"| memfd.{k} | {m.get(k)} | {m.get('errors', {}).get(k, '')} |")
    ns = p.get("namespace", {})
    lines += [
        "",
        "## Namespace probe",
        "",
        f"- available={ns.get('available')}; unshare={ns.get('unshare')}; "
        f"network={ns.get('network')}; mount={ns.get('mount')}; pid={ns.get('pid')}; "
        f"error={ns.get('error') or ''}",
    ]
    cal = p.get("fd_calibration") or {}
    if cal.get("supported"):
        bucket_rows = []
        for key, b in sorted((cal.get("buckets") or {}).items(), key=lambda kv: int(kv[0])):
            mad = b.get("mad_ratio") or {}
            bucket_rows.append(
                f"| {int(key):,} | {b['copy_ms']:.2f} | {b['fd_handoff_ms']:.2f} | {b['materialise_copy_in_ms']:.2f} | "
                f"{b['materialise_in_place_ms']:.2f} | {b.get('samples', '?')} | {mad.get('copy', 0):.0%}/{mad.get('handoff', 0):.0%} | "
                f"{b.get('stable', 'n/a')} | k={b['k_copy_in']}/{b['k_in_place']} |"
            )
        cons = cal.get("consistency") or {}
        lines += ["", "## Sealed-fd cost calibration by size bucket (medians, shuffled arm order)", "",
                  "| Bytes | copy ms | fd handoff ms | copy-in ms | in-place ms | samples | MAD copy/handoff | stable | break-even |",
                  "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |", *bucket_rows, "",
                  f"- Cross-bucket per-MiB copy-cost spread: **{cons.get('per_mib_copy_ms_spread', 'n/a')}x** "
                  f"(consistent={cons.get('consistent', 'n/a')}); unstable or inconsistent calibrations are rejected "
                  "and the conservative defaults stay in force.", ""]
        lines += ["", f"## Largest-bucket summary ({cal['size']:,} B, {cal['reps']} reps requested)", "",
                  f"- copy per consumer **{cal['copy_ms']:.2f} ms**; fd handoff **{cal['fd_handoff_ms']:.2f} ms**; "
                  f"materialise copy-in **{cal['materialise_copy_in_ms']:.2f} ms**; in-place **{cal['materialise_in_place_ms']:.2f} ms**.",
                  f"- Derived break-even fan-out: copy-in **k={cal['k_copy_in']}**, in-place **k={cal['k_in_place']}** "
                  f"(effective thresholds from {p['fd_policy']['source']}).",
                  f"- Cached at `{cal.get('path', 'n/a')}` for kernel `{cal['kernel']}`."]
    lines += ["", "## Backend selection", ""] + [f"- **{k}**: {v}" for k, v in p["selection"].items()]
    lines += ["", f"Source SHA-256: `{p['env']['source_tree_sha256']}`", ""]
    return "\n".join(lines)


def run_os_probe_cli(args) -> int:
    from agent_runtime.evaluation.runner import _allocate_result_dir, _write_checksum_manifest

    p = run_os_probe()
    out = _allocate_result_dir("results", "os_probe")
    (out / "summary.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_os_probe(p), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({"suite": "os_probe", "environment": p["env"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_checksum_manifest(out)
    print(render_os_probe(p))
    print(f"\n结果已保存到: {out}")
    # Probe never fails the host; it records what is available and what was selected.
    return 0


__all__ = ["run_os_probe", "render_os_probe", "run_os_probe_cli"]
