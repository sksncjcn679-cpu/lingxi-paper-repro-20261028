#!/usr/bin/env python3
"""Run the contest requirement scorecard against this checkout.

This wrapper intentionally prepends ``<repo>/src`` so an older site-packages install
cannot be mistaken for the submitted source tree.  ``--require-openeuler`` turns the
openEuler 24.03-LTS-SP3 target-host requirement into a hard non-zero gate.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-openeuler", action="store_true")
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()

    import agent_runtime
    from agent_runtime.cli import main as cli_main

    imported = Path(agent_runtime.__file__).resolve()
    try:
        imported.relative_to(SRC.resolve())
    except ValueError:
        print(f"[FAIL] agent_runtime imported outside checkout: {imported}", file=sys.stderr)
        return 3

    argv = ["competition-check", "--rounds", str(args.rounds)]
    if args.require_openeuler:
        argv.append("--require-openeuler")
    return int(cli_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
