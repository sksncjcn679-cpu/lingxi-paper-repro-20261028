"""Freeze and run the corrected study; no historical paid replay or raw overwrite.

The public registration and quality score are retained verbatim. This controller
adds only the independent formal gate and immutable source snapshots.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse, json, hashlib, sys, os, shutil, subprocess, time, fcntl, signal

OUT=Path('/evidence/protocol_repair_20261020_v1')
HERE=Path(__file__).resolve().parent
ROOT=Path('/opt/lingxi/formal-checkout')
SNAP=OUT/'formal_snapshot_v1'
SOURCE='a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def prefix_sha(path,length):
    h=hashlib.sha256();remaining=length
    with path.open('rb') as stream:
        while remaining:
            part=stream.read(min(1024*1024,remaining))
            if not part:raise RuntimeError('frozen public raw prefix truncated: '+path.name)
            h.update(part);remaining-=len(part)
    return h.hexdigest()
def load(path):return json.loads(path.read_text())
def now():return datetime.now(timezone.utc).isoformat()
def write_new(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def rows(name):
    p=OUT/'raw'/name
    return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []
def source_sha():
    sys.path.insert(0,str(ROOT/'src'))
    from agent_runtime.evaluation.runner import _source_tree_sha256
    return _source_tree_sha256(ROOT)

def freeze():
    path=OUT/'manifests/formal-freeze.json'
    if path.exists() or SNAP.exists():raise RuntimeError('formal freeze/snapshot exists; refusing replacement')
    assert source_sha()==SOURCE
    pilot=load(OUT/'public_pilot_v1/execution.json')
    metric=load(OUT/'analysis/public_metric_audit_v2/metric_audit.json')
    gate=load(OUT/'analysis/stats/preformal_gate_v1/gate.json')
    validation=load(OUT/'analysis/stats/preformal_validation_v1/audit.json')
    external=load(OUT/'calibration/external_sdk_v2/external_sdk_audit.json')
    semantic=load(OUT/'analysis/public_quality_semantic_review_v1.json')
    assert pilot['status']=='PUBLIC_TERMINAL_AWAIT_REAL_METRIC_POWER_BUDGET_GATES'
    assert pilot['terminal_units']==144 and pilot['provider_attempts']<=432 and pilot['finished_utc']
    assert metric['status']=='PASS_OBSERVED_PUBLIC_METRICS' and metric['real_usage_complete_for_all_public_units']
    assert gate['statistics']=='PASS_FIXED_120_WITH_WITHHELD_WORDING'
    assert validation['status']=='PASS' and validation['tests_run']==9
    assert external['status'].startswith('PASS') and external['paid_api_calls']==0
    for field in ['helper_sha256','reused_dependency_sha256','raw_sha256','dry_raw_sha256']:
        for name,digest in external[field].items():assert sha(Path(name))==digest,f'external calibrated evidence changed: {Path(name).name}'
    assert semantic['registered_inputs_or_raw_files_modified']==0 if 'registered_inputs_or_raw_files_modified' in semantic else semantic['review_scope']['registered_inputs_or_raw_files_modified']==0
    assert semantic['original_public_result_preserved']['successful_units']==109
    assert all(r['phase']=='public_calibration' for name in ['task_unit_starts.jsonl','provider_attempt_starts.jsonl','task_runs.jsonl'] for r in rows(name))
    assert len(rows('task_runs.jsonl'))==144
    for relative,expected in metric['evidence'].items():
        p=OUT/relative
        assert p.stat().st_size==expected['bytes'] and sha(p)==expected['sha256'],f'public metric evidence changed: {relative}'
    for name in ['task_runs.jsonl','provider_calls.jsonl','provider_attempt_starts.jsonl','task_unit_starts.jsonl']:
        assert sha(OUT/'raw'/name)==sha(OUT/'analysis/stats/preformal_gate_v1'/('public_'+name)),f'prospective public evidence changed: {name}'
    starts=rows('provider_attempt_starts.jsonl');calls=rows('provider_calls.jsonl')
    assert {r['request_id'] for r in starts}=={r['request_id'] for r in calls}
    # Verify that the genuinely measured native implementation remains unchanged.
    calibrated=load(OUT/'calibration/sdk_overhead_full_v2/metric_audit.json')['source_sha256']
    for name in ['real_driver.py','instrumentation.py','prompt_adapter.py','oracle.py','bootstrap.py','contract.py']:
        assert sha(HERE/name)==calibrated[name],f'calibrated native source changed: {name}'
    # Public inputs remain immutable, including the originally broad technical cap.
    registration=load(OUT/'public_pilot_v1/registration.json')
    for name,digest in registration['files'].items():assert sha(Path(name))==digest,f'public registration changed: {name}'
    SNAP.mkdir();drivers=SNAP/'drivers';drivers.mkdir()
    for p in HERE.glob('*.py'):shutil.copyfile(p,drivers/p.name)
    for name in ['formal_fact_calibration.py','formal_recording.py','formal_prompt_adapter.py','formal_fixture_checks.py',
                 'formal_external_adapters.py','formal_statistics.py','formal_statistics_protocol_aligned_v3.py',
                 'formal_statistics_test.py','formal_statistics_protocol_aligned_v3_test.py']:
        shutil.copyfile(Path('/drivers')/name,drivers/name)
    limitations={
        'created_utc':now(),'before_first_formal_outcome':True,
        'quality':'fixed domain-specific canonical diagnosis and read-only authored fixture action contract; not unconstrained physical Linux diagnosis accuracy',
        'public_scores':'109/144 success retained;35 failures incl26 unsupported physical choices,7 physically plausible cross-domain contract mismatches,2 over-specific OOM causes; no rescoring or acceptance-set expansion',
        'network_mac':'network-facing service client.material file-read AVC; not evidence of a socket/network-class SELinux permission denial',
        'fixtures':'oracle classifies supplied measurements then checks canonical domain action; not24 independent live Linux tools or production remediation',
        'noninferiority':'WITHHELD_PROSPECTIVELY; original section6 permits retaining120 when insufficient power',
        'budget':'user explicitly requested complete supplemental experiment; finite6504units/19512attempt cap; actual bill, unit prices and currency ceiling unknown; technical limits do not certify affordability',
        'measurement':'native fused read/compute and arena reuse remain explicit; provider queue/RTT/billing unavailable, no invented values',
        'retained_failures':['public metric v1 resource schema assumption failure','external calibrationv1 blocked IPv6 local capability probe; v2 exact retained adjudication','all35 actual public quality failures'],
        'old_study':'never pooled, replayed or overwritten; old C004/unknown usage stay limited forever'}
    write_new(SNAP/'preformal_limitations.json',limitations)
    execution_plan={
        'created_utc':now(),'study_id':'protocol_repair_20261020_v1',
        'stages':[{'name':'main','script':'real_driver.py','args':['run','--phases','main']},
                  {'name':'native_remaining','script':'real_driver.py','args':['run','--phases','cross_model','multiprocess',
                   'ablation_baseline','ablation_reference_first_off','ablation_session_dictionary_off','ablation_state_dedup_off',
                   'ablation_quantization_off','ablation_delta_off','ablation_bm25_off','ablation_feedback_isolation_off',
                   'ablation_adoption_gate_off','serialization_pure_json','serialization_pure_msgpack','memory_condition_none',
                   'memory_condition_related','memory_condition_unrelated','memory_condition_stale','memory_condition_wrong']},
                  {'name':'external','script':'external_runner.py','args':['run']}],
        'timing':'stages sequential; no simultaneous retrieval, stress, tests, analysis or packaging',
        'formal_task_units':6360,'public_units_already_completed':144,
        'retrieval':'separate post-real timing window;14400planned, exactpreparedinputs and corrected measurement driver frozen here',
        'resumption':'terminal task units skipped by native runners; unmatched start blocks; same sequence handle never automatically restarted'}
    write_new(SNAP/'execution_plan.json',execution_plan)
    files=list(drivers.glob('*.py'))+[SNAP/'preformal_limitations.json',SNAP/'execution_plan.json',HERE/'formal_sequence.py']
    files += [HERE/n for n in ['statistical_gates.py','external_runner.py','calibrate_external.py','retrieval_driver.py']]
    files += [Path(name) for field in ['helper_sha256','reused_dependency_sha256'] for name in external[field]]
    files += [OUT/'protocol.yaml',OUT/'manifests/original_protocol.md',OUT/'manifests/formal_plan.json',
              OUT/'manifests/budget_limits.json',OUT/'manifests/native_configurations.json',OUT/'public_pilot_v1/registration.json',
              OUT/'public_pilot_v1/execution.json',OUT/'public_pilot_v1/environment.json',
              OUT/'analysis/public_quality_semantic_review_v1.json',OUT/'analysis/public_quality_attribution_v1/findings.json',
              OUT/'analysis/retrieval_static_review_v1.json']
    for directory in ['corpus/candidate_v2','corpus/retrieval_pool_inputs_v1',
                      'analysis/public_metric_audit_v2','analysis/stats/preformal_gate_v1','analysis/stats/preformal_validation_v1',
                      'analysis/retrieval_driver_preoutcome_correction_v1','calibration/external_sdk_v2']:
        files += [p for p in (OUT/directory).rglob('*') if p.is_file()]
    freeze={'schema_version':1,'study_id':'protocol_repair_20261020_v1','frozen_at_utc':now(),
            'source_sha256':SOURCE,'files':{str(p):sha(p) for p in files},
            'driver_snapshot':str(drivers),'public_registration_sha256':sha(OUT/'public_pilot_v1/registration.json'),
            'public_raw_prefixes':{relative:expected for relative,expected in metric['evidence'].items() if relative.startswith('raw/')},
            'metric_gate_sha256':sha(OUT/'analysis/public_metric_audit_v2/metric_audit.json'),
            'quality_noninferiority_wording':'WITHHELD_PROSPECTIVELY','currency_budget_status':gate['currency_budget'],
            'authorization':'human request: 继续跑补充实验吧; prior full original-protocol matrix authorization; no unspecified monetary ceiling inferred',
            'excludes':'secret mounts, env, Authorization, live raw append streams and mutable execution status',
            'status':'FORMAL_FROZEN_WITH_EXPLICIT_ENVIRONMENT_QUALITY_AND_CURRENCY_LIMITS'}
    assert source_sha()==SOURCE
    write_new(path,freeze)
    print(json.dumps({'status':freeze['status'],'files':len(freeze['files']),'formal_units':6360,'full_freeze_sha256':sha(path)}))

def validate():
    freeze=load(OUT/'manifests/formal-freeze.json')
    assert source_sha()==freeze['source_sha256']
    for name,digest in freeze['files'].items():assert sha(Path(name))==digest,f'formal frozen input changed: {Path(name).name}'
    for relative,expected in freeze['public_raw_prefixes'].items():
        assert prefix_sha(OUT/relative,expected['bytes'])==expected['sha256'],f'audited public raw prefix changed: {relative}'
    return freeze

def run():
    freeze=validate()
    destination=OUT/'formal_execution_v1'
    if destination.exists():raise RuntimeError('formal launch already exists; inspect actual handle and stage receipts before any recovery')
    destination.mkdir()
    with (OUT/'manifests/formal_sequence_execution.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        record={'status':'RUNNING','started_utc':now(),'pid':os.getpid(),
                'formal_freeze_sha256':sha(OUT/'manifests/formal-freeze.json'),'stages':[]}
        status=destination/'execution.json';write_new(status,record)
        plan=load(SNAP/'execution_plan.json')
        child=None
        def interrupted(signum,frame):
            raise RuntimeError('sequence controller received signal '+str(signum)+'; inspect supervised child separately')
        previous_term=signal.signal(signal.SIGTERM,interrupted)
        try:
            for stage in plan['stages']:
                child=None
                validate()
                name=stage['name'];item={'name':name,'started_utc':now(),'status':'RUNNING'}
                record['stages'].append(item);status.write_text(json.dumps(record,indent=2)+'\n')
                command=[sys.executable,'-B',str(Path(freeze['driver_snapshot'])/stage['script']),*stage['args']]
                item['command']=command
                with (destination/(name+'.stdout.log')).open('x') as out,(destination/(name+'.stderr.log')).open('x') as err:
                    child=subprocess.Popen(command,stdout=out,stderr=err,cwd=freeze['driver_snapshot'],start_new_session=True)
                    item.update(pid=child.pid,process_group_id=child.pid)
                    status.write_text(json.dumps(record,indent=2)+'\n')
                    code=child.wait()
                item.update(status='TERMINAL',exit_code=code,finished_utc=now())
                status.write_text(json.dumps(record,indent=2)+'\n')
                if code:raise RuntimeError('formal stage failed; retained:'+name)
            validate()
            plan_ids={u['run_id'] for u in load(OUT/'manifests/formal_plan.json')}
            actual=rows('task_runs.jsonl')
            assert len(actual)==6504 and len({r['run_id'] for r in actual})==6504 and {r['run_id'] for r in actual}==plan_ids
            record.update(status='REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT',formal_units=6360,
                          public_units=144,terminal_units=6504,provider_attempts=len(rows('provider_attempt_starts.jsonl')),
                          successful_units=sum(bool(r['success']) for r in actual))
        except BaseException as exc:
            live=child is not None and child.poll() is None
            record.update(status='CONTROLLER_ERROR_CHILD_STILL_RUNNING_DO_NOT_RELAUNCH' if live else 'ERROR_RETAINED_NO_AUTOMATIC_REPLAY',
                          error_type=type(exc).__name__,error=str(exc),
                          supervised_child_pid=child.pid if child is not None else None,
                          supervised_child_observed_running=live,child_state_checked_utc=now(),
                          recovery='inspect actual child PID/process group and real-provider lock; do not replay any unmatched start or infer child termination from controller error')
            raise
        finally:
            signal.signal(signal.SIGTERM,previous_term)
            live=child is not None and child.poll() is None
            record['controller_finished_utc']=now()
            if live:
                record.pop('finished_utc',None)
                record.update(status='CONTROLLER_ERROR_CHILD_STILL_RUNNING_DO_NOT_RELAUNCH',
                              supervised_child_pid=child.pid,supervised_child_observed_running=True,
                              child_state_checked_utc=now())
            else:record['finished_utc']=now()
            status.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(record,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['freeze','validate','run']);a=p.parse_args()
    if a.command=='freeze':freeze()
    elif a.command=='validate':print(json.dumps({'status':'FORMAL_FREEZE_VALID','files':len(validate()['files'])}))
    else:run()
