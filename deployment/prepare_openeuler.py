"""Create an immutable source archive and a bounded openEuler container; no credentials copied."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / ".local/openeuler-bootstrap"
RESULT = ROOT / "paper_experiments_20261019_pilot"
CONTAINER = "lingxi-openeuler-2403"
IMAGE = "openeuler/openeuler@sha256:03b3f365bbba13e9f90c70b08bd9aa0002bc69db858c2808cfab8a55d6e184e3"


def prepare():
    BOOTSTRAP.mkdir(parents=True, exist_ok=True)
    RESULT.mkdir(exist_ok=False)
    for sub in ["manifests", "logs/preflight", "logs/mechanisms", "logs/main", "raw", "corpus", "analysis", "tables"]:
        (RESULT / sub).mkdir(parents=True, exist_ok=True)
    files = []
    source = ROOT / "final-submit/src"
    for p in sorted(source.rglob("*")):
        if p.is_file() and not any(x in p.parts for x in ["__pycache__", ".agent_data", "results", ".pytest_cache"]) and not any(x.endswith(".egg-info") for x in p.parts):
            files.append((p, p.relative_to(source).as_posix()))
    for section in ["unit", "integration"]:
        for p in sorted((ROOT / "final-submit/tests" / section).rglob("*.py")):
            files.append((p, "tests/" + p.relative_to(ROOT / "final-submit/tests").as_posix()))
    files.append((ROOT / "final-submit/tests/conftest.py", "tests/conftest.py"))
    for p in (ROOT / "final-submit/demo/dashboard").glob("*"):
        if p.is_file():
            files.append((p, "dashboard/" + p.name))
    files.append((ROOT / "final-submit/tests/最新实测/full_offline186_20261007/evidence/real_llm_rerun_20260924/cells.csv", "evidence/real_llm_rerun_20260924/cells.csv"))
    manifest = []
    with tarfile.open(BOOTSTRAP / "project.tar", "w") as archive:
        for p, name in files:
            assert p.name != ".env"
            archive.add(p, arcname=name, recursive=False)
            manifest.append({"path": name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    (RESULT / "manifests/source-files.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (RESULT / "manifests/host.json").write_text(json.dumps({"host_platform": "Windows 10 / Docker Desktop WSL2", "prepared_at": datetime.now().astimezone().isoformat(), "image": IMAGE, "container": CONTAINER, "constraints": {"cpus": 2, "memory": "2g", "shm": "256m", "pids": 512}, "topology": "openEuler user space on WSL2 kernel; not native openEuler kernel", "archive_layout": "source root and test fixtures reassembled; ../tests symlink for protocol command; archived CSV is regression fixture only"}, ensure_ascii=False, indent=2), encoding="utf-8")
    args = ["docker", "create", "--name", CONTAINER, "--label", "lingxi.workspace=" + str(ROOT), "--init", "--cpus", "2", "--memory", "2g", "--memory-swap", "3g", "--shm-size", "256m", "--pids-limit", "512",
            "--mount", f"type=volume,source=lingxi-openeuler-data,target=/opt/lingxi",
            "--mount", f"type=bind,source={BOOTSTRAP},target=/bootstrap,readonly",
            "--mount", f"type=bind,source={ROOT / 'deployment'},target=/drivers,readonly",
            "--mount", f"type=bind,source={RESULT},target=/evidence",
            "--mount", f"type=bind,source={ROOT / '.env'},target=/run/secrets/model.env,readonly",
            "--workdir", "/opt/lingxi/checkout", IMAGE, "sleep", "infinity"]
    subprocess.run(args, check=True)
    subprocess.run(["docker", "start", CONTAINER], check=True)
    print(RESULT)


if __name__ == "__main__":
    prepare()
