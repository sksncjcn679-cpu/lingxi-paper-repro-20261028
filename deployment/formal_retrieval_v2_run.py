"""Run the unchanged full retrieval matrix against registered v2 query inputs."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

DEFAULT_OUT=Path('/evidence/formal_20261019')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def validate_inputs(destination):
    inventory=read(destination/'analysis/input_inventory_sha256.json')
    for name,digest in inventory.items():
        path=(destination/name).resolve()
        if destination.resolve() not in path.parents or sha(path)!=digest:raise RuntimeError('registered input/source changed: '+name)
    registration=read(destination/'analysis/before_new_outcome_registration.json')
    if not registration['before_new_outcome'] or registration['corrected_unique_texts']!=240:raise RuntimeError('invalid v2 registration')
    if sha(destination/'analysis/input_inventory_sha256.json')!=registration['input_inventory_sha256']:raise RuntimeError('inventory changed')
    return registration

def cell_worker(out,destination,size,build):
    registration=validate_inputs(destination)
    native=load(destination/'drivers/formal_retrieval_matrix.py','unchanged_native_retrieval_v2')
    native.OUT=destination;native.CORPUS=destination/'corpus'
    old_records=native.records
    def verified_records(size_arg,build_arg,tasks,queries):
        records=old_records(size_arg,build_arg,tasks,queries)
        # The unchanged worker writes these exact bytes to its pool ledger.
        digest=hashlib.sha256(''.join(r.model_dump_json()+'\n' for r in records).encode()).hexdigest()
        original=read(out/'analysis'/f'retrieval_n{size_arg}_build{build_arg}_build.json')['pool_sha256']
        if digest!=original:raise RuntimeError('candidate pool changed relative to original actual pool')
        write(destination/'analysis'/f'retrieval_n{size_arg}_build{build_arg}_pool_identity.json',
            dict(actual_pool_sha256=digest,original_pool_sha256=original,identical=True,
                queries_v2_sha256=registration['queries_v2_sha256']))
        return records
    native.records=verified_records
    native.worker(size,build)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--size',type=int,choices=(100,1000,5000,20000))
    parser.add_argument('--build',type=int,choices=(1,2,3))
    args=parser.parse_args();out=args.out.resolve();destination=(args.destination or out/'retrieval_input_correction_v2').resolve()
    if out not in destination.parents or destination==out:raise RuntimeError('v2 output must be independent child directory')
    if args.size:
        if args.build is None:parser.error('--build required for cell')
        cell_worker(out,destination,args.size,args.build);return
    registration=validate_inputs(destination)
    locks=[]
    for name in ('analysis/postrun_verification.lock','manifests/real_provider_execution.lock'):
        lock=(out/name).open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(lock)
    run=destination/'analysis/retrieval_v2_execution.json'
    if run.exists():raise RuntimeError('execution record exists; preserve incomplete run and inspect before versioned resumption')
    record=dict(status='RUNNING',started_utc=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),
        real_model_calls=0,planned_query_units=14400,registration_sha256=sha(destination/'analysis/before_new_outcome_registration.json'),
        all_inputs_sha256=registration['input_inventory_sha256'],sequential=True,steps=[])
    def save():
        tmp=run.with_suffix('.tmp');tmp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(run)
    save()
    try:
        for build in (1,2,3):
            for size in (100,1000,5000,20000):
                validate_inputs(destination)
                stem=f'retrieval_n{size}_build{build}'
                if (destination/'raw'/f'{stem}.jsonl').exists():raise RuntimeError('raw already exists; no automatic append/overwrite')
                command=[sys.executable,str(destination/'drivers/formal_retrieval_v2_run.py'),'--out',str(out),
                    '--destination',str(destination),'--size',str(size),'--build',str(build)]
                step=dict(size=size,build=build,command=command,started_utc=datetime.now(timezone.utc).isoformat(),status='RUNNING')
                record['steps'].append(step);save();start=time.perf_counter();process=None
                with (destination/'logs/mechanisms'/f'{stem}.stdout.log').open('x') as stdout,(destination/'logs/mechanisms'/f'{stem}.stderr.log').open('x') as stderr:
                    try:
                        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,start_new_session=True)
                        step['pid']=process.pid;save()
                        step['returncode']=process.wait(timeout=1200);step['timed_out']=False
                    except subprocess.TimeoutExpired:
                        step['timed_out']=True;os.killpg(process.pid,signal.SIGKILL);step['returncode']=process.wait()
                    finally:
                        if process is not None and process.poll() is None:
                            os.killpg(process.pid,signal.SIGKILL);process.wait()
                step.update(wall_sec=time.perf_counter()-start,finished_utc=datetime.now(timezone.utc).isoformat(),
                    status='PASS' if step.get('returncode')==0 and not step.get('timed_out') else 'ERROR')
                write(destination/'analysis'/f'{stem}_process.json',step);save()
                if step['status']!='PASS':raise RuntimeError('v2 cell failed; no synthetic substitution or automatic retries')
                cell=read(destination/'analysis'/f'{stem}_status.json')
                if cell['rows']!=1200 or cell['status']!='COMPLETE':raise RuntimeError('cell not fully closed')
                print(json.dumps({k:step[k] for k in ('size','build','status','wall_sec')}),flush=True)
        validate_inputs(destination)
        write(destination/'analysis/retrieval_matrix_process_status.json',record['steps'])
        record['status']='MATRIX_EXECUTED_ANALYSIS_PENDING'
    except BaseException as exc:
        record['status']='ERROR_RETAINED_FOR_DIAGNOSIS';record['exception_type']=type(exc).__name__;save();raise
    finally:
        record['finished_utc']=datetime.now(timezone.utc).isoformat();save()
        for lock in reversed(locks):lock.close()

if __name__=='__main__':main()
