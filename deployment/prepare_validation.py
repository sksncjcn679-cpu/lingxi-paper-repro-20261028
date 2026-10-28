"""Reassemble the source/test layout expected by the archived tests in a private copy."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".local" / "validation"


def prepare():
    TARGET.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", ".pytest_cache", "results", ".agent_data")
    shutil.copytree(ROOT / "final-submit" / "src", TARGET, dirs_exist_ok=True, ignore=ignore)
    for name in ("unit", "integration"):
        shutil.copytree(ROOT / "final-submit" / "tests" / name, TARGET / "tests" / name,
                        dirs_exist_ok=True, ignore=ignore)
    shutil.copy2(ROOT / "final-submit" / "tests" / "conftest.py", TARGET / "tests" / "conftest.py")
    shutil.copytree(ROOT / "final-submit" / "demo" / "dashboard", TARGET / "dashboard",
                    dirs_exist_ok=True, ignore=ignore)
    archived = ROOT / "final-submit/tests/最新实测/full_offline186_20261007/evidence/real_llm_rerun_20260924/cells.csv"
    evidence = TARGET / "evidence/real_llm_rerun_20260924/cells.csv"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archived, evidence)
    print(TARGET)
    return TARGET


if __name__ == "__main__":
    prepare()
