"""Run a user-requested regression without replacing the original preflight logs."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import preflight_openeuler as preflight

stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
preflight.LOGS = Path("/evidence/logs/rechecks") / stamp
preflight.LOGS.mkdir(parents=True)
raise SystemExit(preflight.execute("pytest", [sys.executable, "-m", "pytest", "../tests/unit", "../tests/integration",
                                           "-q", "--tb=short", f"--junitxml={preflight.LOGS}/pytest.xml"]))
