"""Execute a separate post-measurement schema-validation environment install."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

OUT = Path('/evidence/formal_20261019')
DEST = OUT / 'analysis/schema_environment_v1'
VENV = Path('/opt/lingxi/schema-analysis-venv-v1')
REQ = Path('/drivers/formal_schema_analysis_requirements_v1.txt')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    assert sys.version_info[:2] == (3, 11), sys.version
    assert not DEST.exists(), 'Preserve existing environment evidence; use a new version.'
    assert not VENV.exists(), 'Refusing to modify an existing environment.'
    assert (OUT / 'logs/mechanisms/stress_matrix_v3.result.json').is_file()
    pressure = json.loads((OUT / 'logs/mechanisms/stress_matrix_v3.result.json').read_text())
    assert pressure['exit_code'] == 0 and not pressure['timed_out']
    DEST.mkdir()
    (DEST / 'wheels').mkdir()
    shutil.copy2(__file__, DEST / Path(__file__).name)
    shutil.copy2(REQ, DEST / REQ.name)
    shutil.copy2(__file__, OUT / 'drivers' / Path(__file__).name)
    record = {'status': 'RUNNING', 'scope': 'POST_MEASUREMENT_SCHEMA_VALIDATION_ONLY',
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'python': sys.version, 'venv': str(VENV), 'source_sha256': sha(Path(__file__)),
              'requirements_sha256': sha(REQ), 'steps': [], 'paid_api_calls': 0}
    def save():
        (DEST / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    env = dict(os.environ)
    for key in list(env):
        if any(part in key.upper() for part in ('API_KEY', 'ACCESS_TOKEN', 'AUTHORIZATION')):
            env.pop(key, None)
    def run(name, argv, timeout=600):
        item = {'name': name, 'argv': [str(a) for a in argv], 'exit_code': None}
        record['steps'].append(item)
        save()
        with (DEST / (name + '.stdout.log')).open('wb') as stdout, (DEST / (name + '.stderr.log')).open('wb') as stderr:
            result = subprocess.run(argv, stdout=stdout, stderr=stderr, env=env, timeout=timeout)
        item['exit_code'] = result.returncode
        save()
        if result.returncode:
            raise RuntimeError(f'{name} failed; inspect preserved logs')
    save()
    try:
        run('01_create', [sys.executable, '-m', 'venv', str(VENV)])
        python = str(VENV / 'bin/python')
        run('02_download', [python, '-m', 'pip', '--disable-pip-version-check', 'download', '--no-input',
            '--index-url', 'https://pypi.org/simple', '--only-binary=:all:', '--dest', str(DEST / 'wheels'), '-r', str(REQ)])
        record['wheels'] = {p.name: sha(p) for p in sorted((DEST / 'wheels').iterdir()) if p.is_file()}
        save()
        run('03_install', [python, '-m', 'pip', '--disable-pip-version-check', 'install', '--no-input',
            '--no-index', '--find-links', str(DEST / 'wheels'), '-r', str(REQ)])
        run('04_freeze', [python, '-m', 'pip', '--disable-pip-version-check', 'freeze', '--all'])
        run('05_check', [python, '-m', 'pip', '--disable-pip-version-check', 'check'])
        run('06_import', [python, '-c', 'import jsonschema, pyarrow; import importlib.metadata as m; print(m.version("jsonschema")); print(pyarrow.__version__)'])
        record['status'] = 'PASS'
    except Exception as exc:
        record['status'] = 'ERROR'
        record['error_type'] = type(exc).__name__
        raise
    finally:
        record['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({'status': record['status'], 'manifest': str(DEST / 'manifest.json')}))

if __name__ == '__main__':
    main()
