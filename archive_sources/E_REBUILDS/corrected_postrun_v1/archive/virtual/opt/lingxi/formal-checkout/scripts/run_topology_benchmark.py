#!/usr/bin/env python3
"""Run paired request/reply versus forward topology measurements."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_runtime.evaluation.topology_benchmark import (  # noqa: E402
    run_topology_benchmark,
    write_topology_results,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=24)
    parser.add_argument("--results-root", type=Path, default=ROOT / "results")
    parser.add_argument("--require-uds", action="store_true")
    args = parser.parse_args(argv)
    if args.require_uds and not hasattr(socket, "AF_UNIX"):
        parser.error("AF_UNIX is required for this validation")
    payload = run_topology_benchmark(repeat=args.repeat)
    out = write_topology_results(payload, args.results_root)
    print(f"control_transport={payload['methodology']['variants']['forward']['control_transport']}")
    print(f"results={out}")
    print((out / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
