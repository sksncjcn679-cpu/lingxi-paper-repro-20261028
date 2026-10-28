"""Best-effort real cgroup v2 controller probe with explicit permission results."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import os
import tempfile
from typing import Any

from agent_runtime.evaluation.runner import _allocate_result_dir, _env_info, _write_checksum_manifest


def run_cgroup_benchmark() -> dict[str, Any]:
    root = Path("/sys/fs/cgroup")
    result: dict[str, Any] = {"available": root.is_dir(), "controller_files": [], "enforced": False, "error": None}
    if not root.is_dir():
        result["error"] = "cgroup v2 mount unavailable"
    else:
        result["controller_files"] = [name for name in ("memory.max", "pids.max", "cpu.max") if (root / name).exists()]
        name = f"agent-runtime-probe-{os.getpid()}"
        child = root / name
        try:
            child.mkdir()
            (child / "memory.max").write_text("max", encoding="ascii")
            (child / "pids.max").write_text("max", encoding="ascii")
            (child / "cpu.max").write_text("max 100000", encoding="ascii")
            result["enforced"] = all((child / field).read_text(encoding="ascii").strip() for field in ("memory.max", "pids.max", "cpu.max"))
        except (OSError, PermissionError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                child.rmdir()
            except OSError:
                pass
    return {"suite": "cgroup", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "env": _env_info(), "result": result}


def render_cgroup_report(payload: dict[str, Any]) -> str:
    result = payload["result"]
    return "\n".join(["# cgroup v2 Controller Probe", "", f"- Available: **{result['available']}**", f"- Controllers: `{', '.join(result['controller_files'])}`", f"- Write/read verification: **{result['enforced']}**", f"- Error: `{result['error']}`", "", f"Source SHA-256: `{payload['env']['source_tree_sha256']}`", ""])


def write_cgroup_results(payload: dict[str, Any], root: str | Path = "results") -> Path:
    out = _allocate_result_dir(str(root), "cgroup")
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_cgroup_report(payload), encoding="utf-8")
    _write_checksum_manifest(out)
    return out


def run_cgroup_cli(_args) -> int:
    payload = run_cgroup_benchmark()
    out = write_cgroup_results(payload)
    print(render_cgroup_report(payload))
    print(f"\nResults: {out}")
    return 0


__all__ = ["run_cgroup_benchmark", "write_cgroup_results"]
