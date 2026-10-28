"""Record actual sandbox guard activation and harmless allow/deny checks."""
from pathlib import Path
import json
import os
import tempfile
from agent_runtime.sandbox.executor import CodeActExecutor

executor = CodeActExecutor()
benign = executor.run("print('pilot-guard-ok')")
guard = executor.last_guard
report = {"guard_report": guard, "benign_exit_code": benign.exit_code, "status": "UNSUPPORTED"}
if guard and guard.get("seccomp") and guard.get("landlock_abi"):
    fd, filename = tempfile.mkstemp(prefix="pilot_guard_fixture_")
    os.write(fd, b"PUBLIC_TEST_FIXTURE")
    os.close(fd)
    try:
        network = executor.run("import socket; socket.socket()")
        filesystem = executor.run(f"open({filename!r}).read()")
        report.update(status="VERIFIED_LIMITED_PROBES", network_socket_exit_code=network.exit_code,
                      outside_fixture_read_exit_code=filesystem.exit_code,
                      network_denied=network.exit_code != 0, outside_fixture_read_denied=filesystem.exit_code != 0)
    finally:
        os.unlink(filename)
Path("/evidence/analysis/kernel_guard_verification.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
raise SystemExit(0 if report.get("network_denied") and report.get("outside_fixture_read_denied") and benign.exit_code == 0 else 1)
