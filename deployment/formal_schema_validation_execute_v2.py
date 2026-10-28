"""One isolated, API-free schema/readback execution; preserve every outcome."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
from formal_postrun_verify import digest, enable_child_subreaper, execute_step, save, utc

OUT = Path('/evidence/formal_20261019')
VALIDATOR_SHA = 'ade0d73293cf4d0d4a191c2987b7340e921618163b36e856064005d92fbf554c'
CONTRACT_SHA = '3db924512dd2c2f529f2f361fb550509bfcc18e5087c19ed90ec12157632726d'


def main():
    target = OUT / 'analysis/schema_validation_execution_v2'
    destination = OUT / 'analysis/schema_validation_v2'
    source = Path('/drivers/formal_schema_validation.py')
    contract = OUT / 'analysis/schema_validation_contract_v4.json'
    if target.exists() or destination.exists():
        raise RuntimeError('new execution and validation destinations required')
    if digest(source) != VALIDATOR_SHA or digest(contract) != CONTRACT_SHA:
        raise RuntimeError('reviewed validator or contract changed')
    locks = []
    for relative in ('analysis/postrun_verification.lock', 'manifests/real_provider_execution.lock'):
        lock = (OUT / relative).open('a+')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(lock)
    target.mkdir()
    manifest = dict(status='RUNNING', started_utc=utc(), paid_api_calls=0,
                    source_sha256=digest(Path(__file__)), validator_sha256=digest(source),
                    contract_sha256=digest(contract), steps=[], goal_completion='NOT_CERTIFIED')
    persist = lambda: save(target / 'manifest.json', manifest)
    persist()
    try:
        manifest['child_subreaper'] = enable_child_subreaper()
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': '/drivers'}
        for key in list(env):
            if any(token in key.upper() for token in ('API_KEY', 'API_TOKEN', 'ACCESS_TOKEN', 'AUTH_TOKEN', 'PASSWORD', 'SECRET')):
                env.pop(key, None)
        prior = OUT / 'analysis/postrun_verification_v1'
        command = ['/opt/lingxi/schema-analysis-venv-v1/bin/python', str(source),
                   '--out', str(OUT), '--destination', str(destination),
                   '--core-exports', str(prior / 'exports'),
                   '--usage-exports', str(prior / 'provider_usage_authority'),
                   '--quality-exports', str(prior / 'quality_appendix'), '--contract', str(contract)]
        step = dict(name='schema_validation_v2', command=command, started_utc=utc())
        manifest['steps'].append(step)
        execute_step(command, env, target, step, 7200, persist)
        audit_path = destination / 'schema_validation_audit.json'
        manifest.update(status='PASS', audit_sha256=digest(audit_path),
                        audit_status=json.loads(audit_path.read_text())['status'],
                        scope='Format and lossless storage only; semantic coverage and study completion remain separate')
    except BaseException as error:
        manifest.update(status='ERROR_RETAINED_FOR_DIAGNOSIS', error_type=type(error).__name__,
                        error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
        raise
    finally:
        manifest['finished_utc'] = utc()
        persist()
        for lock in reversed(locks):
            lock.close()
    print(json.dumps({'status': manifest['status'], 'audit_status': manifest.get('audit_status'), 'paid_api_calls': 0}), flush=True)


if __name__ == '__main__':
    main()
