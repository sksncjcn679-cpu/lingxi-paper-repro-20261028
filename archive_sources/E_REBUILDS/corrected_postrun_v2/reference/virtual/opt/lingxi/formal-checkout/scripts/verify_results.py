#!/usr/bin/env python3
"""Verify the current-result index, source identity and per-suite manifests."""
from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def project_version() -> str:
    import re

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("cannot determine project version")
    return match.group(1)


INDEX = RESULTS / f"CURRENT_v{project_version()}.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_tree_sha() -> str:
    # One canonical definition also covers scripts, model weights and dataset files.
    sys.path.insert(0, str(ROOT / "src"))
    from agent_runtime.evaluation.runner import _source_tree_sha256
    return _source_tree_sha256(ROOT)


def verify_manifest(directory: Path) -> list[str]:
    errors: list[str] = []
    mf = directory / "MANIFEST.sha256"
    if not mf.is_file():
        return [f"missing manifest: {mf}"]
    for no, raw in enumerate(mf.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            digest, rel = line.split(None, 1)
        except ValueError:
            errors.append(f"{mf}:{no}: malformed line")
            continue
        rel = rel.lstrip("* ")
        target = directory / rel
        if not target.is_file():
            errors.append(f"{mf}:{no}: missing {rel}")
        elif sha256(target) != digest:
            errors.append(f"{mf}:{no}: hash mismatch {rel}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-linux",
        action="store_true",
        help="require every indexed result suite to be produced on native Linux",
    )
    args = parser.parse_args()
    if not INDEX.is_file():
        print(f"[FAIL] missing {INDEX}", file=sys.stderr)
        return 2
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    expected = idx["source_tree_sha256"]
    actual = source_tree_sha()
    errors: list[str] = []
    if actual != expected:
        errors.append(f"source tree SHA mismatch: expected={expected} actual={actual}")

    current = idx.get("current_results") or {
        k: Path(v).name for k, v in idx.get("generated_suite_paths", {}).items()
    }
    if not current:
        errors.append("index lists no current result suites")
    for suite, dirname in current.items():
        d = RESULTS / Path(dirname).name
        if not d.is_dir():
            errors.append(f"{suite}: missing result dir {d}")
            continue
        s = d / "summary.json"
        if not s.is_file():
            errors.append(f"{suite}: missing summary.json")
            continue
        payload = json.loads(s.read_text(encoding="utf-8"))
        observed = payload.get("env", {}).get("source_tree_sha256")
        if observed != expected:
            errors.append(f"{suite}: result source SHA {observed!r} != {expected}")
        if args.require_linux and payload.get("env", {}).get("platform_class") != "linux":
            errors.append(
                f"{suite}: platform_class={payload.get('env', {}).get('platform_class')!r}; "
                "native Linux evidence required"
            )
        errors.extend(f"{suite}: {x}" for x in verify_manifest(d))

    if errors:
        for e in errors:
            print(f"[FAIL] {e}", file=sys.stderr)
        return 1
    print(f"[PASS] {len(current)} current suites; source SHA {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
