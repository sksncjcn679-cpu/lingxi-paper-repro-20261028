#!/usr/bin/env python3
"""Verify that validation commands are importing the submitted source tree, not a stale install."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


def verify(root: Path) -> tuple[Path, str]:
    root = root.resolve()
    src_root = (root / "src").resolve()
    if not src_root.is_dir():
        raise RuntimeError(f"source directory missing: {src_root}")

    # Prepend before importing; this deliberately overrides any globally installed old version.
    sys.path.insert(0, str(src_root))
    import agent_runtime  # noqa: PLC0415

    loaded = Path(agent_runtime.__file__).resolve()
    if not loaded.is_relative_to(src_root):
        raise RuntimeError(f"agent_runtime loaded from stale/non-project path: {loaded}")

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("cannot resolve project version from pyproject.toml")
    expected = match.group(1)
    if agent_runtime.__version__ != expected:
        raise RuntimeError(
            f"version mismatch: imported={agent_runtime.__version__}, pyproject={expected}"
        )
    return loaded, expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        loaded, version = verify(Path(args.root))
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] source identity: {exc}", file=sys.stderr)
        return 1
    print(f"loaded: {loaded}")
    print(f"version: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
