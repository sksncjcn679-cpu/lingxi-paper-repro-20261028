"""Small offline smoke matrix. Repetitions intentionally do not satisfy the formal paper matrix."""
from pathlib import Path
import shutil
import sys
import preflight_openeuler as preflight

preflight.LOGS = Path("/evidence/logs/mechanisms")
preflight.LOGS.mkdir(parents=True, exist_ok=True)
commands = [
    ("ipc-smoke", ["ipc-benchmark", "--sizes", "1024,65536,1048576", "--repeat", "5", "--warmup", "1"]),
    ("fd-smoke", ["fd-passing-benchmark", "--repeat", "5"]),
    ("quantization-smoke", ["state-quantization", "--samples", "20", "--dim", "256"]),
    ("agent-process-smoke", ["agent-isolation", "--repeat", "2"]),
    ("cgroup-enforcement", ["cgroup-benchmark"]),
]
codes = []
for label, args in commands:
    codes.append(preflight.execute(label, [sys.executable, "scripts/rt.py"] + args, timeout=300))
destination = Path("/evidence/logs/mechanisms/runtime-results")
shutil.copytree(preflight.ROOT / "results", destination, dirs_exist_ok=True)
raise SystemExit(0 if all(c == 0 for c in codes) else 1)
