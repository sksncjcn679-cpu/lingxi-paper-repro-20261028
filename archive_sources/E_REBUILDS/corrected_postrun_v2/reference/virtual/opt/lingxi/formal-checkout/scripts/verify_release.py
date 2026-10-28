#!/usr/bin/env python3
"""Offline release gate for packaging and evidence hygiene."""
from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def _read_version(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"cannot find version in {path}")
    return match.group(1)


def project_versions() -> tuple[str, str]:
    return (
        _read_version(ROOT / "pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        _read_version(ROOT / "src/agent_runtime/__init__.py", r'^__version__\s*=\s*"([^"]+)"'),
    )


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=180, check=False)


def check_static_hygiene(*, code_only: bool = False) -> list[str]:
    errors: list[str] = []
    project, package = project_versions()
    if project != package:
        errors.append(f"version mismatch: pyproject={project} package={package}")
    for folder in (ROOT / "src", ROOT / "scenarios"):
        for path in sorted(folder.rglob("*.py")):
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))
            except SyntaxError as exc:
                errors.append(f"Python 3.10 syntax {path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
    if not (ROOT / "LICENSE").is_file():
        errors.append("LICENSE missing")
    if not (ROOT / "SECURITY.md").is_file():
        errors.append("SECURITY.md missing")
    if not (ROOT / "docs/RELEASE_CHECKLIST.md").is_file():
        errors.append("docs/RELEASE_CHECKLIST.md missing")
    # fable5: deliverables are named after the release codename; accept either form.
    release_name = re.search(r'RELEASE_NAME\s*=\s*"([^"]+)"', (ROOT / "src/agent_runtime/__init__.py").read_text(encoding="utf-8"))
    ascii_video = ROOT / "deliverables" / f"demo_video_{release_name.group(1)}.mp4" if release_name else None
    named_video = ascii_video if (ascii_video and ascii_video.is_file()) else (
        ROOT / "deliverables" / f"演示视频_{release_name.group(1)}.mp4" if release_name else None)
    expected_video = named_video if (named_video and named_video.is_file()) else ROOT / "deliverables" / f"演示视频_v{project}.mp4"
    if not code_only and not expected_video.is_file():
        errors.append(f"demo video missing: {expected_video.name}")
    return errors


def check_results() -> list[str]:
    proc = _run([sys.executable, "scripts/verify_results.py"], cwd=ROOT)
    print(proc.stdout.strip())
    return [] if proc.returncode == 0 else ["result verification failed"]


def check_wheel() -> list[str]:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agent-runtime-release-") as td:
        tmp = Path(td); wheel_dir = tmp / "wheel"; wheel_dir.mkdir()
        build = _run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "-w", str(wheel_dir)], cwd=ROOT)
        if build.returncode != 0:
            return ["wheel build failed:\n" + build.stdout.strip()]
        wheels = list(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            return [f"expected one wheel, found {len(wheels)}"]
        with zipfile.ZipFile(wheels[0]) as zf:
            names = zf.namelist()
            required = (
                "agent_runtime/protocol/placement.py",
                "agent_runtime/state_exchange/delta.py",
                "agent_runtime/memory/store.py",
                "agent_runtime/runtime/event_log.py",
                "agent_runtime/runtime/checkpoint.py",
                "agent_runtime/scheduler/mailbox.py",
            )
            for suffix in required:
                if not any(n.endswith(suffix) for n in names):
                    errors.append(f"wheel missing {suffix}")
        target = tmp / "site"
        install = _run([sys.executable, "-m", "pip", "install", "--target", str(target), "--no-deps", str(wheels[0])], cwd=tmp)
        if install.returncode != 0:
            return errors + ["wheel target install failed:\n" + install.stdout.strip()]
        env = os.environ.copy(); env["PYTHONPATH"] = str(target)
        for cmd in (
            [sys.executable, "-m", "agent_runtime.cli", "version"],
            [sys.executable, "-m", "agent_runtime.cli", "--log-level", "ERROR", "demo"],
        ):
            proc = _run(cmd, cwd=tmp, env=env)
            if proc.returncode != 0:
                errors.append("wheel smoke failed:\n" + proc.stdout.strip())
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--skip-wheel", action="store_true")
    p.add_argument("--code-only", action="store_true", help="verify code/evidence/wheel without requiring presentation/video")
    args = p.parse_args(argv)
    errors = check_static_hygiene(code_only=args.code_only) + check_results()
    if not args.skip_wheel:
        errors += check_wheel()
    if errors:
        for e in errors: print(f"[FAIL] {e}", file=sys.stderr)
        return 1
    print(f"[PASS] release hygiene for agent-runtime {project_versions()[0]}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
