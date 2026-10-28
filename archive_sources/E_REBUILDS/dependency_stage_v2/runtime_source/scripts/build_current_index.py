#!/usr/bin/env python3
"""Build release indexes from result suites produced by the current source tree."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_runtime import RELEASE_NAME, __version__  # noqa: E402
from agent_runtime.evaluation.runner import _source_tree_sha256  # noqa: E402


def main() -> int:
    results = ROOT / "results"
    source_sha = _source_tree_sha256(ROOT)
    candidates: dict[str, tuple[str, str]] = {}
    for directory in results.iterdir() if results.is_dir() else []:
        summary_path = directory / "summary.json"
        if not directory.is_dir() or not summary_path.is_file():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        env = payload.get("env") or {}
        if env.get("source_tree_sha256") != source_sha:
            continue
        suite = str(payload.get("suite") or directory.name.rsplit("-", 1)[-1])
        generated = str(payload.get("generated_at_utc") or directory.name)
        previous = candidates.get(suite)
        if previous is None or generated > previous[0]:
            candidates[suite] = (generated, directory.name)

    current = {suite: dirname for suite, (_generated, dirname) in sorted(candidates.items())}
    payload = {
        "release": RELEASE_NAME,
        "version": __version__,
        "source_tree_sha256": source_sha,
        "current_results": current,
        "generated_suite_paths": {suite: f"results/{dirname}" for suite, dirname in current.items()},
        "notes": [
            "Only result suites with the current source SHA are indexed.",
            "Run scripts/verify_results.py --require-linux before treating this index as submission evidence.",
        ],
    }
    for name in (f"CURRENT_v{__version__}.json", f"CURRENT_{RELEASE_NAME}.json"):
        (results / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_tree_sha256": source_sha, "suites": current}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
