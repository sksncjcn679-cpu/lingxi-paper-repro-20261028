"""Host launcher: one bounded no-network execution, retaining actual exit/log hashes."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    target = ROOT / 'execution_receipt.json'
    if target.exists() or (ROOT / 'output/guard_result.json').exists():
        raise RuntimeError('Refuse to rerun or replace an existing actual execution')
    inspected = subprocess.run(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], capture_output=True, text=True, check=True)
    assert inspected.stdout.strip() == IMAGE
    controls = {p.name: sha(p) for p in (ROOT / 'control').iterdir() if p.is_file()}
    command = ['docker', 'run', '--pull', 'never', '--name', 'lingxi-completion-stress-ci-v1',
               '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--cpus', '2', '--memory', '2g', '--pids-limit', '256', '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m',
               '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'OPENBLAS_NUM_THREADS=1', '--env', 'OMP_NUM_THREADS=1',
               '--env', 'MKL_NUM_THREADS=1',
               '--mount', f'type=bind,source={(ROOT / "input").as_posix()},target=/input,readonly',
               '--mount', f'type=bind,source={(ROOT / "control").as_posix()},target=/control,readonly',
               '--mount', f'type=bind,source={(ROOT / "output").as_posix()},target=/output',
               '--entrypoint', '/opt/lingxi/formal-venv/bin/python', IMAGE, '-X', 'utf8', '-B',
               '/control/offline_analysis_runner.py', '/control/guard_configuration.json']
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    with (ROOT / 'stdout.log').open('xb') as stdout, (ROOT / 'stderr.log').open('xb') as stderr:
        result = subprocess.run(command, stdout=stdout, stderr=stderr)
    elapsed = time.monotonic() - clock
    unchanged = controls == {p.name: sha(p) for p in (ROOT / 'control').iterdir() if p.is_file()}
    receipt = {'status': 'ACTUAL_ONE_GUARDED_SUPPLEMENT_EXECUTION', 'started_utc': started,
               'finished_utc': datetime.now(timezone.utc).isoformat(), 'elapsed_host_seconds': elapsed,
               'actual_exit_code': result.returncode, 'command': command, 'image': IMAGE,
               'control_sha256': controls, 'control_files_unchanged': unchanged,
               'stdout_sha256': sha(ROOT / 'stdout.log'), 'stderr_sha256': sha(ROOT / 'stderr.log'),
               'guard_result_sha256': sha(ROOT / 'output/guard_result.json') if (ROOT / 'output/guard_result.json').exists() else None,
               'new_model_API_benchmark_calls': 0, 'old_outputs_modified': False}
    with target.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'actual_exit_code': result.returncode, 'elapsed_host_seconds': elapsed, 'receipt_sha256': sha(target)}), flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
