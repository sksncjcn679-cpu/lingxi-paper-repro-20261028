"""Export the private-container deployment and its evidence without copying credentials."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
import urllib.request
import urllib.error

OUT = Path("/evidence")
ROOT = Path("/opt/lingxi/checkout")
details = {"recorded_utc": datetime.now(timezone.utc).isoformat(), "files": {}}
for name in ["/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.swap.max",
             "/proc/loadavg", "/proc/uptime", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
             "/sys/devices/system/clocksource/clocksource0/current_clocksource"]:
    p = Path(name)
    details["files"][name] = p.read_text().strip() if p.exists() else "UNAVAILABLE"
details.update(clock_sync="WSL shared host clock; no independent NTP verification", frequency_control="host/WSL controlled; no dedicated frequency policy",
               scheduling="Docker CPU quota enforced by host; host contention and preemption uncontrolled",
               native_kernel="UNSUPPORTED: container shares Microsoft WSL2 kernel")
network = []
for _ in range(3):
    start = time.perf_counter()
    try:
        with urllib.request.urlopen("https://www.dmxapi.cn/v1/models", timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except Exception as error:
        network.append({"status": "UNAVAILABLE", "error_type": type(error).__name__})
        continue
    network.append({"http_status": status, "wall_sec": time.perf_counter()-start, "measurement": "unauthenticated HTTPS connect/TLS/response; not pure RTT and no model invocation"})
details["network_probe"] = network
(OUT / "manifests/environment_supplement.json").write_text(json.dumps(details, indent=2))
rpm = subprocess.run(["rpm", "-qa", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n"], capture_output=True, text=True, check=True)
(OUT / "manifests/rpm-packages.txt").write_text("\n".join(sorted(rpm.stdout.splitlines()))+"\n")
for path in Path("/drivers").glob("*.py"):
    if "openeuler" in path.name or path.name in ["pilot_corpus.py", "verify_agent_topology.py"]:
        shutil.copy2(path, OUT / "analysis" / path.name)
shutil.copy2("/drivers/bootstrap_openeuler.sh", OUT / "analysis/bootstrap_openeuler.sh")
with tarfile.open(OUT / "source_snapshot.tar.gz", "w:gz") as archive:
    archive.add("/bootstrap/project.tar", arcname="project.tar")
shutil.copytree(ROOT / "results", OUT / "logs/mechanisms/runtime-results", dirs_exist_ok=True)
with tarfile.open(OUT / "raw_snapshot.tar.gz", "w:gz") as archive:
    archive.add(OUT / "raw", arcname="raw")
print(json.dumps({"network_samples": network, "snapshot_created": True}), flush=True)
