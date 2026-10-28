"""Execute only the failed report stage in its registered new destination.

The original twelve-step verifier and its actual failure are immutable evidence.
This command makes no model calls and cannot certify overall study completion.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

from formal_postrun_verify import digest, enable_child_subreaper, execute_step, save, utc

OUT = Path('/evidence/formal_20261019')
SELECTION_SHA = 'fc8dcde3bded0cdeb4b77c93adde80eb21b954d632a9e4459b0e90bfaf357a00'
REPORT_SHA = 'da34bb1eb1f10f3e1d0c92a6e0df756edc7e55dcd2e931737671a27b22f1c707'


def selected_path(selection, key):
    relative = Path(selection[key])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('unsafe registered relative path')
    path = (OUT / relative).resolve()
    if not path.is_relative_to(OUT.resolve()):
        raise ValueError('registered path outside experiment')
    return path


def main():
    selection_path = OUT / 'analysis/final_artifact_selection_v2.json'
    if digest(selection_path) != SELECTION_SHA:
        raise RuntimeError('registered artifact selection changed')
    selection = json.loads(selection_path.read_text())
    target = selected_path(selection, 'report_recovery_root')
    report_dir = selected_path(selection, 'report_root')
    retrieval = selected_path(selection, 'authoritative_retrieval_root')
    if target.exists() or report_dir.exists():
        raise RuntimeError('new recovery and report destinations required; never overwrite')

    # Fail without starting any load if another experiment still owns a lock.
    locks = []
    for name in ('analysis/postrun_verification.lock', 'manifests/real_provider_execution.lock'):
        lock = (OUT / name).open('a+')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(lock)
    original = OUT / selection['postrun_prior_evidence']
    previous = json.loads(original.read_text())
    steps = previous.get('steps', [])
    if len(steps) != 12 or any(s.get('status') != 'PASS' or s.get('exit_code') != 0 for s in steps[:11]) or \
            steps[11].get('name') != '12_formal_report' or steps[11].get('status') != 'FAILED_EXIT' or steps[11].get('exit_code') != 1:
        raise RuntimeError('original verifier must retain actual01-11PASS/12FAIL')
    execution = retrieval / 'analysis/retrieval_v2_execution.json'
    result = json.loads(execution.read_text())
    if result.get('status') != 'MATRIX_EXECUTED_ANALYSIS_PENDING' or len(result.get('steps', [])) != 12 or \
            any(s.get('status') != 'PASS' or s.get('returncode') != 0 for s in result['steps']):
        raise RuntimeError('corrected retrieval timing matrix has not closed')
    analysis = retrieval / 'analysis/retrieval_v2_analysis.json'
    analyzed = json.loads(analysis.read_text())
    if analyzed.get('observed_query_units') != 14400 or analyzed.get('cells') != 60:
        raise RuntimeError('corrected retrieval analysis is incomplete')
    source = Path('/drivers/formal_report.py')
    if digest(source) != REPORT_SHA:
        raise RuntimeError('reviewed report source changed')

    target.mkdir()
    manifest = dict(status='RUNNING', started_utc=utc(), pid=os.getpid(), paid_api_calls=0,
                    source_sha256=digest(Path(__file__)), report_source_sha256=digest(source),
                    original_postrun_manifest_sha256=digest(original),
                    original_postrun_policy='Original01-11PASS/12FAIL retained without changes or replay',
                    final_artifact_selection_sha256=digest(selection_path),
                    retrieval_execution_sha256=digest(execution), retrieval_analysis_sha256=digest(analysis),
                    visual_qa='NOT_PERFORMED', goal_completion='NOT_CERTIFIED', steps=[])
    persist = lambda: save(target / 'manifest.json', manifest)
    persist()
    try:
        manifest['child_subreaper'] = enable_child_subreaper()
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'MPLBACKEND': 'Agg',
               'MPLCONFIGDIR': str(target / 'matplotlib-cache'),
               'PYTHONPATH': '/drivers:/opt/lingxi/formal-checkout/src'}
        for key in list(env):
            if any(token in key.upper() for token in ('API_KEY', 'API_TOKEN', 'ACCESS_TOKEN', 'AUTH_TOKEN', 'PASSWORD', 'SECRET')):
                env.pop(key, None)
        command = [sys.executable, str(source), '--out', str(OUT), '--report-dir', str(report_dir),
                   '--retrieval-root', str(retrieval)]
        step = dict(name='12_formal_report_recovery_v2', command=command, started_utc=utc(), source_sha256=digest(source))
        manifest['steps'].append(step)
        execute_step(command, env, target, step, 7200, persist)
        if digest(original) != manifest['original_postrun_manifest_sha256']:
            raise RuntimeError('original verifier evidence changed')
        availability = report_dir / 'analysis/report_availability.json'
        manifest.update(status='PASS', report_availability_sha256=digest(availability),
                        report_availability_status=json.loads(availability.read_text()).get('status'),
                        scope='Report command executed only; numeric/visual QA, reproduction and completion audit remain separate')
    except BaseException as error:
        manifest.update(status='ERROR_RETAINED_FOR_DIAGNOSIS', error_type=type(error).__name__,
                        error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
        raise
    finally:
        manifest['finished_utc'] = utc()
        persist()
        for lock in reversed(locks):
            lock.close()
    print(json.dumps({'status': manifest['status'], 'output': str(target), 'paid_api_calls': 0}), flush=True)


if __name__ == '__main__':
    main()
