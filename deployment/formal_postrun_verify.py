"""Serial, API-free verification after the existing real+pressure sequence exits.

Does not launch/replay any experiment. New logs and outputs are retained on failure.
Full paper delivery and visual QA remain a separate, explicit completion gate.
"""
from __future__ import annotations
import argparse
from collections import Counter
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

OUT=Path('/evidence/formal_20261019')
DRIVERS=Path('/drivers')
LENGTHS=(1,2,4,8,16,32)
CONCURRENCIES=(1,2,4,8,16)
FAULTS=('router_crash','drop_ack','corrupt_state','timeout','duplicate_message','wrong_memory')


def utc():return datetime.now(timezone.utc).isoformat()


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while data:=stream.read(1024*1024):value.update(data)
    return value.hexdigest()


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def enable_child_subreaper():
    """Adopt this verifier's orphaned descendants so group cleanup can reap them."""
    library=ctypes.CDLL(None,use_errno=True)
    # Linux PR_SET_CHILD_SUBREAPER; this changes only the verifier process.
    if library.prctl(36,1,0,0,0)!=0:
        value=ctypes.get_errno();raise OSError(value,os.strerror(value))
    return {'enabled':True,'scope':'process-local orphan adoption for owned subprocess cleanup'}


def group_members(group_id):
    members=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            fields=(path/'stat').read_text().rsplit(')',1)[1].split()
            if int(fields[2])==group_id:members.append({'pid':int(path.name),'state':fields[0]})
        except (FileNotFoundError,ProcessLookupError):pass
    return sorted(members,key=lambda member:member['pid'])


def cleanup_step_group(process,step,persist):
    """Do not release the provider lock while an owned group remains alive."""
    group_id=process.pid
    record={'started_utc':utc(),'group_id':group_id,'members_before':group_members(group_id),'reaped_descendants':[]}
    step['cleanup']=record;persist()
    def send(sig):
        try:os.killpg(group_id,sig)
        except ProcessLookupError:pass
    def reap():
        # The direct child is waited through Popen first; only then reap adopted
        # descendants, preserving Popen's original exit status.
        while True:
            try:child,status=os.waitpid(-group_id,os.WNOHANG)
            except ChildProcessError:return
            if child==0:return
            record['reaped_descendants'].append({'pid':child,'wait_status':status})
    send(signal.SIGTERM)
    deadline=time.monotonic()+5
    while process.poll() is None and time.monotonic()<deadline:time.sleep(.1)
    # The wrapper may have exited while its auditor is still running.
    remaining=group_members(group_id)
    if process.poll() is None or any(item['state']!='Z' for item in remaining):send(signal.SIGKILL)
    process.wait()
    # A killed descendant may not be adopted until its old parent exits. Retain
    # the shared execution lock until all group members have exited and reaped.
    tick=time.monotonic()
    while True:
        reap();remaining=group_members(group_id)
        if not remaining:break
        if any(item['state']!='Z' for item in remaining):send(signal.SIGKILL)
        if time.monotonic()-tick>=1:
            record['members_still_waiting']=remaining;persist();tick=time.monotonic()
        time.sleep(.1)
    record.update(finished_utc=utc(),members_after=[],direct_child_exit_code=process.returncode)
    record.pop('members_still_waiting',None);persist()


def execute_step(command,env,target,step,timeout,persist):
    """Record exit/exception/log evidence even when a child times out."""
    process=None;tick=time.monotonic();stdout_path=target/f'{step["name"]}.stdout.log';stderr_path=target/f'{step["name"]}.stderr.log'
    step.update(status='STARTING',exit_code=None,timed_out=False,timeout_sec=timeout);persist()
    try:
        with stdout_path.open('x') as stdout,stderr_path.open('x') as stderr:
            process=subprocess.Popen(command,cwd='/opt/lingxi/formal-checkout',env=env,
                stdout=stdout,stderr=stderr,start_new_session=True)
            step.update(status='RUNNING',pid=process.pid,process_group_id=process.pid,timed_out=False);persist()
            try:code=process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                step.update(status='TIMEOUT',timed_out=True)
                cleanup_step_group(process,step,persist)
                raise
            unexpected=group_members(process.pid)
            if unexpected:
                step.update(status='ERROR_REMAINING_GROUP_MEMBERS',unexpected_group_members=unexpected)
                cleanup_step_group(process,step,persist)
                raise RuntimeError('step exited with remaining process-group members; reclaimed without continuing')
            step.update(status='PASS' if code==0 else 'FAILED_EXIT',exit_code=code)
            if code:raise RuntimeError('postrun step failed; inspect retained logs: '+step['name'])
    except BaseException as error:
        step.setdefault('status','ERROR')
        if step['status'] in ('STARTING','RUNNING'):step['status']='ERROR'
        step.update(error_type=type(error).__name__,error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
        if process is not None and 'cleanup' not in step:
            if process.poll() is None or group_members(process.pid):cleanup_step_group(process,step,persist)
        raise
    finally:
        if process is not None:step['exit_code']=process.returncode
        step.update(wall_sec=time.monotonic()-tick,finished_utc=utc())
        for label,path in [('stdout_sha256',stdout_path),('stderr_sha256',stderr_path)]:
            step[label]=digest(path) if path.is_file() else None
        persist()


def pressure_gate(out):
    """Stream the closed offline matrix; task failures remain valid observations.

    Status headers are checked before reading task ledgers, so a boundary-stop
    exit does not trigger the more expensive verification stages. This gate
    certifies coverage and record linkage, never fixture robustness or quality.
    """
    out=Path(out);issues=[];sources={};headers={};counts={}
    def issue(code,**detail):issues.append({'code':code,**detail})
    def fingerprint(path):
        value=path.stat()
        return {'device':value.st_dev,'inode':value.st_ino,'bytes':value.st_size,'mtime_ns':value.st_mtime_ns}
    def remember(path,before,sha256):
        after=fingerprint(path)
        if before!=after:issue('INPUT_CHANGED_DURING_READ',path=str(path.relative_to(out)))
        sources[str(path.relative_to(out))]={'sha256':sha256,'fingerprint':after}
    def header(path):
        if not path.is_file():issue('STATUS_MISSING',path=str(path.relative_to(out)));return {}
        try:
            before=fingerprint(path);data=path.read_bytes();value=json.loads(data)
            remember(path,before,hashlib.sha256(data).hexdigest())
            if not isinstance(value,dict):raise ValueError('status must be an object')
            return value
        except (OSError,ValueError,TypeError) as error:
            issue('STATUS_UNREADABLE',path=str(path.relative_to(out)),error_type=type(error).__name__);return {}
    def require_file(path):
        if not path.is_file():issue('RAW_MISSING',path=str(path.relative_to(out)))
    def equal(value,expected,code,**detail):
        if type(value) is not type(expected) or value!=expected:issue(code,**detail)
    def stream(path,consumer):
        before=fingerprint(path);value=hashlib.sha256();number=0
        with path.open('rb') as source:
            for line_number,line in enumerate(source,1):
                value.update(line)
                if not line.strip():continue
                number+=1
                try:
                    item=json.loads(line)
                    if not isinstance(item,dict):raise ValueError('row must be an object')
                    consumer(item,line_number)
                except (ValueError,TypeError,KeyError,AttributeError) as error:
                    issue('RAW_SCHEMA_ERROR',path=str(path.relative_to(out)),line=line_number,error_type=type(error).__name__)
        remember(path,before,value.hexdigest())
        return number,value.hexdigest()
    def linked_hash(status,sha256,stem):
        if status.get('raw_sha256')!=sha256:issue('STATUS_RAW_SHA256_MISMATCH',cell=stem)
    def boolean(row,key,**detail):
        if type(row.get(key)) is not bool:issue('BOOLEAN_FIELD_INVALID',field=key,**detail)
        return row.get(key) is True
    def result():
        return {'schema_version':1,'status':'GATE_INCOMPLETE' if issues else 'GATE_COMPLETE',
            'created_utc':utc(),'planned_pressure_cells':30,'planned_pressure_tasks':58590,
            'planned_continuous_rounds':100,'planned_fault_trials':180,'counts':counts,
            'issues':issues,'sources':sources,'paid_api_calls':0,
            'scope':'streaming offline closure/identity/hash gate; success=False retained; no quality or robustness certification',
            'limits':['frozen continuous status has no precommitted raw SHA; its current raw SHA, round coverage, counter agreement and input stability are recorded here',
                'batch ledgers have no precommitted status SHA; their current bytes and exact per-batch/task count joins are checked here']}
    # Check all lightweight status headers and expected paths before raw scans.
    for length in LENGTHS:
        for concurrency in CONCURRENCIES:
            stem=f'stress_v3_L{length}_K{concurrency}';expected=30*length*concurrency
            status=header(out/'analysis'/f'{stem}_status.json');headers[stem]=status
            for key,value in [('status','COMPLETE'),('planned_tasks',expected),('recorded_tasks',expected),('real_model_calls',0)]:
                equal(status.get(key),value,'PRESSURE_STATUS_INCOMPLETE',cell=stem,field=key)
            for suffix in ('','_batches'):require_file(out/'raw'/f'{stem}{suffix}.jsonl')
    continuous=header(out/'analysis/stress_continuous_100_status.json');headers['continuous']=continuous
    for key,value in [('status','COMPLETE'),('rounds',100),('real_model_calls',0)]:
        equal(continuous.get(key),value,'CONTINUOUS_STATUS_INCOMPLETE',field=key)
    require_file(out/'raw/stress_continuous_100.jsonl')
    for fault in FAULTS:
        stem='stress_fault_'+fault;status=header(out/'analysis'/f'{stem}_status.json');headers[stem]=status
        for key,value in [('status','COMPLETE'),('trials',30),('actual_injections',30),('real_provider_calls',0)]:
            equal(status.get(key),value,'FAULT_STATUS_INCOMPLETE',fault=fault,field=key)
        require_file(out/'raw'/f'{stem}.jsonl')
    if issues:return result()
    seen_ids=set();pressure_total=0;pressure_failures=0
    for length in LENGTHS:
        for concurrency in CONCURRENCIES:
            stem=f'stress_v3_L{length}_K{concurrency}';status=headers[stem]
            pattern=re.compile(re.escape(stem)+r'-b([0-9]{2})-c([0-9]{2})-s([0-9]{2})')
            by_batch=Counter();success_by_batch=Counter();cell_failures=0;state_missing=0
            def task(row,line):
                nonlocal cell_failures,state_missing
                task_id=row.get('task_id');matched=pattern.fullmatch(task_id) if isinstance(task_id,str) else None
                if not matched:issue('PRESSURE_TASK_ID_INVALID',cell=stem,line=line);return
                batch,chain,step=map(int,matched.groups())
                if batch not in range(30) or chain not in range(concurrency) or step not in range(length):
                    issue('PRESSURE_TASK_COORDINATE_OUT_OF_RANGE',cell=stem,line=line)
                if task_id in seen_ids:issue('DUPLICATE_OFFLINE_TASK_ID',cell=stem,line=line)
                seen_ids.add(task_id)
                for key,value in [('experiment_id',task_id),('length',length),('concurrency',concurrency),('batch',batch)]:
                    equal(row.get(key),value,'PRESSURE_TASK_IDENTITY_MISMATCH',cell=stem,line=line,field=key)
                success=boolean(row,'success',cell=stem,line=line)
                state=boolean(row,'state_consumed',cell=stem,line=line)
                by_batch[batch]+=1;success_by_batch[batch]+=int(success)
                cell_failures+=int(not success);state_missing+=int(not state)
            number,sha256=stream(out/'raw'/f'{stem}.jsonl',task)
            pressure_total+=number;pressure_failures+=cell_failures
            equal(number,30*length*concurrency,'PRESSURE_CELL_COUNT_MISMATCH',cell=stem)
            for batch in range(30):equal(by_batch[batch],length*concurrency,'PRESSURE_BATCH_TASK_COUNT_MISMATCH',cell=stem,batch=batch)
            linked_hash(status,sha256,stem)
            equal(status.get('failures'),cell_failures,'PRESSURE_FAILURE_COUNTER_MISMATCH',cell=stem)
            equal(status.get('state_not_consumed'),state_missing,'PRESSURE_STATE_COUNTER_MISMATCH',cell=stem)
            batch_ids=set()
            def batch_record(row,line):
                batch=row.get('batch')
                if type(batch) is not int or batch not in range(30):issue('BATCH_ID_INVALID',cell=stem,line=line);return
                if batch in batch_ids:issue('DUPLICATE_BATCH_ID',cell=stem,batch=batch)
                batch_ids.add(batch)
                for key,value in [('experiment_id',f'{stem}-b{batch:02}'),('length',length),('concurrency',concurrency),
                    ('tasks_planned',length*concurrency),('tasks_recorded',by_batch[batch]),('successes',success_by_batch[batch])]:
                    equal(row.get(key),value,'BATCH_TASK_LINK_MISMATCH',cell=stem,batch=batch,field=key)
            batch_number,_=stream(out/'raw'/f'{stem}_batches.jsonl',batch_record)
            if batch_number!=30 or batch_ids!=set(range(30)):issue('BATCH_LEDGER_NOT_EXACT_30',cell=stem)
    counts.update(pressure_tasks=pressure_total,pressure_cells_checked=30,pressure_task_failures=pressure_failures)
    equal(pressure_total,58590,'FULL_PRESSURE_TASK_COUNT_MISMATCH')
    rounds=set();continuous_failures=0;continuous_state_missing=0
    def continuous_record(row,line):
        nonlocal continuous_failures,continuous_state_missing
        index=row.get('round')
        if type(index) is not int or index not in range(100):issue('CONTINUOUS_ROUND_INVALID',line=line);return
        if index in rounds:issue('DUPLICATE_CONTINUOUS_ROUND',round=index)
        rounds.add(index);task_id=f'stress-continuous-{index:03}'
        equal(row.get('task_id'),task_id,'CONTINUOUS_TASK_ID_MISMATCH',round=index)
        if task_id in seen_ids:issue('DUPLICATE_OFFLINE_TASK_ID',round=index)
        seen_ids.add(task_id)
        continuous_failures+=int(not boolean(row,'success',round=index))
        continuous_state_missing+=int(not boolean(row,'state_consumed',round=index))
    continuous_number,continuous_sha=stream(out/'raw/stress_continuous_100.jsonl',continuous_record)
    if continuous_number!=100 or rounds!=set(range(100)):issue('CONTINUOUS_NOT_EXACT_100_ROUNDS')
    if 'raw_sha256' in continuous:linked_hash(continuous,continuous_sha,'continuous')
    equal(continuous.get('failures'),continuous_failures,'CONTINUOUS_FAILURE_COUNTER_MISMATCH')
    equal(continuous.get('state_not_consumed'),continuous_state_missing,'CONTINUOUS_STATE_COUNTER_MISMATCH')
    counts.update(continuous_rounds=continuous_number,continuous_task_failures=continuous_failures)
    fault_total=0
    for fault in FAULTS:
        stem='stress_fault_'+fault;status=headers[stem];trials=set();actual_injections=0;recoveries=0
        def fault_record(row,line):
            nonlocal actual_injections,recoveries
            trial=row.get('trial')
            if type(trial) is not int or trial not in range(30):issue('FAULT_TRIAL_INVALID',fault=fault,line=line);return
            if trial in trials:issue('DUPLICATE_FAULT_TRIAL',fault=fault,trial=trial)
            trials.add(trial);task_id=f'fault-{fault}-{trial:02}'
            equal(row.get('fault'),fault,'FAULT_CLASS_MISMATCH',fault=fault,trial=trial)
            equal(row.get('experiment_id'),task_id,'FAULT_TRIAL_ID_MISMATCH',fault=fault,trial=trial)
            if task_id in seen_ids:issue('DUPLICATE_OFFLINE_TASK_ID',fault=fault,trial=trial)
            seen_ids.add(task_id)
            injection=row.get('injection') or {};recovery=row.get('recovery') or {};hook=injection.get('hook_evidence') or {}
            equal(injection.get('task_id'),task_id,'FAULT_INJECTION_TASK_ID_MISMATCH',fault=fault,trial=trial)
            equal(recovery.get('task_id'),task_id+'-recovery','FAULT_RECOVERY_TASK_ID_MISMATCH',fault=fault,trial=trial)
            equal(hook.get('fault'),fault,'FAULT_HOOK_CLASS_MISMATCH',fault=fault,trial=trial)
            equal(hook.get('injected'),True,'FAULT_NOT_ACTUALLY_INJECTED',fault=fault,trial=trial)
            actual_injections+=int(hook.get('injected') is True)
            boolean(injection,'success',fault=fault,trial=trial)
            recovery_success=boolean(recovery,'success',fault=fault,trial=trial)
            equal(row.get('recovery_success'),recovery_success,'FAULT_RECOVERY_COUNTER_LINK_MISMATCH',fault=fault,trial=trial)
            recoveries+=int(recovery_success)
        number,sha256=stream(out/'raw'/f'{stem}.jsonl',fault_record);fault_total+=number
        if number!=30 or trials!=set(range(30)):issue('FAULT_NOT_EXACT_30_TRIALS',fault=fault)
        linked_hash(status,sha256,stem)
        equal(status.get('actual_injections'),actual_injections,'FAULT_INJECTION_COUNTER_MISMATCH',fault=fault)
        equal(status.get('recovered'),recoveries,'FAULT_RECOVERY_COUNTER_MISMATCH',fault=fault)
    counts['fault_trials']=fault_total;equal(fault_total,180,'FULL_FAULT_COUNT_MISMATCH')
    for relative,record in sources.items():
        if fingerprint(out/relative)!=record['fingerprint']:issue('INPUT_CHANGED_DURING_GATE',path=relative)
    return result()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--version',default='v1');args=parser.parse_args()
    if not args.version.replace('_','').isalnum():raise ValueError('simple version name required')
    target=OUT/'analysis'/f'postrun_verification_{args.version}'
    if target.exists():raise RuntimeError('postrun verification archive already exists; never overwrite/restart blindly')
    lock=(OUT/'analysis/postrun_verification.lock').open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    target.mkdir()
    manifest={'status':'WAITING_EXISTING_SEQUENCE','started_utc':utc(),'pid':os.getpid(),
        'source_sha256':digest(Path(__file__)),'paid_api_calls':0,'steps':[],
        'scope':'postrun verification only; no provider or pressure runner imported or launched',
        'visual_qa':'NOT_PERFORMED','goal_completion':'NOT_CERTIFIED'}
    save(target/'manifest.json',manifest)
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','MPLBACKEND':'Agg',
        'MPLCONFIGDIR':str(target/'matplotlib-cache'),'PYTHONPATH':str(DRIVERS)}
    # Inherited credential environment values are not needed by any analysis.
    for key in list(env):
        if any(token in key.upper() for token in ('API_KEY','API_TOKEN','ACCESS_TOKEN','AUTH_TOKEN','PASSWORD','SECRET')):
            env.pop(key,None)
    sequence=OUT/'logs/mechanisms/formal_complete_sequence_v1.result.json'
    try:
        while not sequence.exists():time.sleep(10)
        result=json.loads(sequence.read_text())
        if result.get('exit_code')!=0 or result.get('timed_out'):
            raise RuntimeError('existing sequence failed or timed out; no postrun load started')
        # Keep the shared real-provider lock while analyzing: a mistaken second runner
        # must fail rather than overlap with postrun load or overwrite timing evidence.
        real_lock=(OUT/'manifests/real_provider_execution.lock').open('a')
        fcntl.flock(real_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        manifest.update(status='RUNNING',existing_sequence_sha256=digest(sequence),sequence_finished_utc=result.get('finished_utc'))
        save(target/'manifest.json',manifest)
        try:gate=pressure_gate(OUT)
        except Exception as error:
            gate={'status':'ERROR','created_utc':utc(),'paid_api_calls':0,
                'issues':[{'code':'PRESSURE_GATE_INPUT_OR_EXECUTION_ERROR','error_type':type(error).__name__,
                    'error_text_sha256':hashlib.sha256(str(error).encode()).hexdigest()}],
                'scope':'no tests or downstream audits were started'}
        save(target/'pressure_gate.json',gate)
        manifest['pressure_gate_status']=gate['status']
        if gate['status']=='ERROR':raise RuntimeError('pressure gate could not execute; inspect retained pressure_gate.json')
        if gate['status']!='GATE_COMPLETE':
            manifest.update(status='GATE_INCOMPLETE',finished_utc=utc(),steps=[])
            save(target/'manifest.json',manifest)
            print(json.dumps({'status':'GATE_INCOMPLETE','output':str(target),'paid_api_calls':0}),flush=True)
            return 2
        manifest['child_subreaper']=enable_child_subreaper();save(target/'manifest.json',manifest)
        steps=[
            ('01_frozen_statistics_synthetic','formal_statistics_test.py',[],900),
            ('02_protocol_alignment_synthetic','formal_statistics_protocol_aligned_v3_test.py',[],900),
            ('03_export_contract_synthetic','formal_export_test.py',[],900),
            ('04_external_auditor_synthetic','formal_external_measurement_audit_test.py',[],900),
            ('05_complete_matrix','formal_completion_audit.py',['--output',str(target/'complete_matrix_audit.json')],1800),
            ('06_native_measurement','formal_native_measurement_audit_all.py',['--destination',str(target/'native_measurement')],7200),
            ('07_external_measurement','formal_external_measurement_audit.py',['--output',str(target/'external_measurement.json')],1800),
            ('08_preselected_30','formal_statistics_spotcheck.py',['run'],1800),
            ('09_provider_usage_authority','formal_provider_usage_authority.py',['--destination',str(target/'provider_usage_authority')],1800),
            ('10_quality_appendix','formal_quality_appendix.py',['--destination',str(target/'quality_appendix')],1800),
            ('11_lossless_exports','formal_export.py',['--destination',str(target/'exports')],3600),
            ('12_formal_report','formal_report.py',['--report-dir',str(target/'report')],7200),
        ]
        for name,filename,arguments,timeout in steps:
            source=DRIVERS/filename
            if not source.is_file():raise RuntimeError('postrun helper not yet supplied: '+filename)
            command=([sys.executable,'-m','unittest','-v',source.stem,*arguments]
                if filename.endswith('_test.py') else [sys.executable,str(source),*arguments])
            step={'name':name,'command':command,'started_utc':utc(),'source_sha256':digest(source)}
            manifest['steps'].append(step);save(target/'manifest.json',manifest)
            execute_step(command,env,target,step,timeout,lambda:save(target/'manifest.json',manifest))
        availability=json.loads((target/'report/analysis/report_availability.json').read_text())
        manifest.update(status='VERIFICATION_EXECUTED_REQUIRES_NUMERIC_AND_VISUAL_QA',finished_utc=utc(),
            report_availability_status=availability.get('status'),
            remaining=['independent schema validation and required-field audit','figure numeric/layout/PDF-text QA',
                'fresh-directory one-command reproduction replay','read-only source/raw package and checksums',
                '155-requirement completion audit and supported abstract claims'])
        save(target/'manifest.json',manifest)
        print(json.dumps({'status':manifest['status'],'output':str(target),'paid_api_calls':0}),flush=True)
    except BaseException as error:
        manifest.update(status='ERROR_RETAINED_FOR_DIAGNOSIS',finished_utc=utc(),
            error_type=type(error).__name__,error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
        save(target/'manifest.json',manifest)
        raise


if __name__=='__main__':raise SystemExit(main() or 0)
