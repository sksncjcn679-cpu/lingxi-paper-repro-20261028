"""Capture the protocol's preflight commands without hiding skips, failures or unsupported paths."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys

OUT = Path("/evidence")
ROOT = Path("/opt/lingxi/checkout")
LOGS = OUT / "logs/preflight"


def execute(name, argv, timeout=900):
    start = datetime.now(timezone.utc).isoformat()
    stdout = LOGS / f"{name}.stdout.log"
    stderr = LOGS / f"{name}.stderr.log"
    try:
        with stdout.open("w") as out, stderr.open("w") as err:
            p = subprocess.run(argv, cwd=ROOT, stdout=out, stderr=err, timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        code = 124
    row = {"name": name, "argv": argv, "cwd": str(ROOT), "start_utc": start,
           "end_utc": datetime.now(timezone.utc).isoformat(), "exit_code": code}
    with (LOGS / "commands.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests-only", action="store_true")
    args = parser.parse_args()
    LOGS.mkdir(parents=True, exist_ok=True)
    if args.tests_only:
        return execute("pytest", [sys.executable, "-m", "pytest", "../tests/unit", "../tests/integration", "-q", "--tb=short", "--junitxml=/evidence/logs/preflight/pytest.xml"])
    from agent_runtime.evaluation.runner import _env_info
    from agent_runtime.evaluation.os_probe import run_os_probe
    environment = _env_info()
    environment.update(os_release=Path("/etc/os-release").read_text(), cpuinfo=Path("/proc/cpuinfo").read_text(),
                       cgroup=Path("/proc/self/cgroup").read_text(), mountinfo=Path("/proc/self/mountinfo").read_text(),
                       topology="Docker openEuler user space on WSL2 kernel", native_openeuler_kernel=False,
                       vm_config={"wsl_cpus": 4, "wsl_memory_gb": 3, "container_cpu_quota": 2, "container_memory_gb": 2},
                       cpu_affinity=sorted(os.sched_getaffinity(0)),
                       host_background_activity="not isolated; Windows application load and WSL scheduling are not controlled",
                       timing_scope="pilot diagnostics only; not native openEuler performance evidence")
    (OUT / "manifests/environment.json").write_text(json.dumps(environment, ensure_ascii=False, indent=2))
    packages = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
    (OUT / "manifests/dependencies.json").write_text(json.dumps(packages, indent=2, sort_keys=True))
    (OUT / "manifests/dependencies.lock").write_text("\n".join(f"{k}=={v}" for k, v in sorted(packages.items()) if k.lower() != "agent-runtime") + "\n")
    paths = [p for base in [ROOT / "src", ROOT / "scripts", ROOT / "scenarios"] for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    paths += [ROOT / "pyproject.toml"]
    (OUT / "manifests/source.json").write_text(json.dumps({"source_tree_sha256": environment["source_tree_sha256"], "files": [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(paths)]}, indent=2))
    commands = [
        ("source-identity", [sys.executable, "scripts/verify_source_identity.py"]),
        ("version", [sys.executable, "scripts/rt.py", "version"]),
        ("os-probe", [sys.executable, "scripts/rt.py", "os-probe"]),
        ("uname", ["uname", "-a"]),
        ("os-release", ["cat", "/etc/os-release"]),
        ("python-version", [sys.executable, "--version"]),
        ("dependency-check", [sys.executable, "-m", "pip", "check"]),
    ]
    codes = [execute(name, argv) for name, argv in commands]
    (OUT / "manifests/capabilities.json").write_text(json.dumps(run_os_probe(), indent=2))
    return int(any(c != 0 for c in codes))


if __name__ == "__main__":
    raise SystemExit(main())
