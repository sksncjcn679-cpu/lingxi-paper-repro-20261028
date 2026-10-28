"""One paid M1 task through the real runtime, for checking a local installation."""
import json
from deployment.local_server import RunRequest, run_case

if __name__ == "__main__":
    result = run_case(RunRequest())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["success"] else 1)
