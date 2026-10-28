"""The openEuler validation entry point must not accidentally import an old installation."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_identity_checker_prefers_submitted_source_over_fake_old_install(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    fake = tmp_path / "fake"
    pkg = fake / "agent_runtime"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(fake)
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "verify_source_identity.py"), str(root)],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert str((root / "src" / "agent_runtime").resolve()) in proc.stdout
    assert "version: 0.0.0" not in proc.stdout
