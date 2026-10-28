"""Serial execution of the remaining FROZEN matrices; safe phase boundaries.

This orchestration adds no experimental conditions. Existing terminal units are not replayed.
"""
from pathlib import Path
from collections import Counter
import json
import subprocess
import sys
import time
from formal_recording import now,sha
from formal_real_driver import OUT,validate_freeze

PYTHON='/opt/lingxi/formal-venv/bin/python'


def event(kind,**values):
    with (OUT/'logs/complete_sequence_events.jsonl').open('a') as stream:
        stream.write(json.dumps({'utc':now(),'event':kind,**values})+'\n')
        stream.flush()


def stop_requested():
    if (OUT/'analysis/STOP_REAL_AFTER_PHASE').exists():
        event('stopped_at_phase_boundary',reason='explicit stop flag exists')
        raise SystemExit(2)


def rows():
    path=OUT/'raw/task_runs.jsonl'
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []


def completed(phases):
    plan=json.loads((OUT/'manifests/formal_plan.json').read_text())
    expected={u['run_id'] for u in plan if u['phase'] in phases}
    done=[r['run_id'] for r in rows() if r['phase'] in phases]
    if len(done)!=len(set(done)):
        raise RuntimeError('duplicate terminal units; no phase continuation')
    return set(done)==expected


def launch(name,command,timeout=54000):
    stop_requested();validate_freeze()
    result_path=OUT/'logs/mechanisms'/f'{name}.result.json'
    if result_path.exists():
        record=json.loads(result_path.read_text())
        if record['exit_code']!=0:raise RuntimeError('previous phase failed; explicit recovery required: '+name)
        return
    if (OUT/'raw'/f'resources_{name}.jsonl').exists():
        raise RuntimeError('phase recording already exists without result; no duplicate launch: '+name)
    command=[PYTHON,'/drivers/formal_monitor.py',name,str(timeout),*command]
    event('phase_launch',name=name,command=command)
    code=subprocess.call(command)
    event('phase_terminal',name=name,exit_code=code)
    if code:raise SystemExit(code)
    subprocess.check_call([PYTHON,'/drivers/formal_status.py'])


def main():
    validate_freeze()
    event('orchestration_started',source_sha256=sha(Path(__file__).read_bytes()),
          frozen_manifest_sha256=sha((OUT/'manifests/formal-freeze.json').read_bytes()))
    main_result=OUT/'logs/mechanisms/formal_main_v1.result.json'
    event('waiting_existing_main',scope='no second main driver launched')
    while not main_result.exists():
        stop_requested();time.sleep(5)
    record=json.loads(main_result.read_text())
    if record['exit_code'] or not completed(['main']):
        raise RuntimeError('main not completely terminal; no automatic phase continuation')
    plan=json.loads((OUT/'manifests/formal_plan.json').read_text())
    phases=list(dict.fromkeys(u['phase'] for u in plan
        if u['phase'] not in ('main','public_calibration') and not u['phase'].startswith('external_')))
    if not completed(phases):
        launch('formal_native_remaining_v1',[PYTHON,'/drivers/formal_real_driver.py','run','--phases',*phases])
    if not completed(phases):raise RuntimeError('remaining native matrix incomplete')
    external=['external_autogen','external_langgraph']
    if not completed(external):
        launch('formal_external_v1',[PYTHON,'/drivers/formal_external_runner.py','run','--phases',*external])
    if not completed(external):raise RuntimeError('external matrix incomplete')
    event('all_real_model_matrices_terminal',formal_units=6360,public_units=144,
          note='terminal includes quality failures; this does not certify final paper delivery')
    # All provider latency measurements end before heavy offline pressure starts.
    launch('stress_matrix_v3',[PYTHON,'/drivers/formal_stress_matrix.py'])
    subprocess.check_call([PYTHON,'/drivers/formal_stress_analyze.py'])
    event('offline_pressure_terminal',note='full pressure and continuous rounds must pass independent completion audit')
    subprocess.check_call([PYTHON,'/drivers/formal_status.py'])
    event('sequence_completed',remaining='independent audits, statistics, plots, source/raw checksums and reproduction packaging')


if __name__=='__main__':main()
