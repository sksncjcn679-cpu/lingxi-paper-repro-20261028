"""Prospectively register and execute the corrected PUBLIC cohort exactly once.

Preparation is unpaid. A sealed driver copy, rather than mutable development
files, is imported for execution. No old-study outcomes are read or replayed.
"""
from __future__ import annotations
import argparse
from collections import Counter
import fcntl
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT=Path('/evidence/protocol_repair_20261020_v1')
ROOT=Path('/opt/lingxi/formal-checkout')
HERE=Path(__file__).resolve().parent
PILOT=OUT/'public_pilot_v1'
EXPECTED_SOURCE='a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562'
def now():return datetime.now(timezone.utc).isoformat()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write_new(path,value):
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def load(path):return json.loads(path.read_text())
def source_sha():
    sys.path.insert(0,str(ROOT/'src'))
    from agent_runtime.evaluation.runner import _source_tree_sha256
    return _source_tree_sha256(ROOT)
def lines(name):
    path=OUT/'raw'/name
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []

def prepare():
    if PILOT.exists():raise RuntimeError('pilot registration exists; refusing replacement')
    assert not lines('provider_attempt_starts.jsonl') and not lines('task_unit_starts.jsonl')
    assert source_sha()==EXPECTED_SOURCE
    metric=load(OUT/'calibration/metric_full_v3/metric_audit.json')
    sdk=load(OUT/'calibration/sdk_overhead_full_v2/metric_audit.json')
    contract=load(OUT/'calibration/offline_v2/audit.json')
    assert metric['status']=='PASS_URI_INDEX_CORRECTNESS_NOT_PROVIDER_OR_OVERHEAD_CALIBRATION'
    assert sdk['status']=='PASS_LOCAL_SDK_USAGE_CONTRACT_AND_PAIRED_RECORDING_OVERHEAD'
    assert metric['units']==90 and sdk['task_units']==420 and sdk['paid_api_calls']==0
    assert contract['status'].startswith('PASS')
    for receipt,field in [(metric,'helper_sha256'),(sdk,'source_sha256')]:
        for name,expected in receipt[field].items():
            assert digest(HERE/name)==expected,f'calibrated source changed: {name}'
        assert receipt['business_source_before_after_sha256']==EXPECTED_SOURCE
    plan=load(OUT/'manifests/formal_plan.json')
    public=[u for u in plan if u['phase']=='public_calibration']
    corpus=[json.loads(s) for s in (OUT/'corpus/candidate_v2/tasks.jsonl').read_text().splitlines()]
    cases={r['task_id']:r for r in corpus}
    assert len(public)==144 and len({u['run_id'] for u in public})==144
    assert len({u['task_id'] for u in public})==24
    assert set(Counter(u['arm'] for u in public).values())=={24}
    assert all(cases[u['task_id']]['split']=='pilot' and u['model']=='main' for u in public)
    assert all(u['study_id']=='protocol_repair_20261020_v1' for u in plan)
    # The exact native namespace hash uses json.dumps(..., sort_keys=True).
    # Existing databases must never silently seed an allegedly empty public chain.
    for u in public:
        if not u['arm'].endswith('1'):continue
        key=('protocol_repair_20261020_v1',u['phase'],u['model'],u['arm'],u['repeat'],cases[u['task_id']]['chain_id'],'real')
        keyhash=hashlib.sha256(json.dumps(key,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
        assert not (Path('/opt/lingxi/protocol-repair-memory')/(keyhash+'.sqlite')).exists(),'public memory namespace already exists'
    assert digest(OUT/'corpus/candidate_v2/tasks.jsonl')=='4cfd1c01b6fbdd639a969b69edef6fcd36fc30ad0aa77ed909c0483987834d4e'
    protocol={
        'prepared_utc':now(),'authority':'manifests/original_protocol.md',
        'authority_sha256':digest(OUT/'manifests/original_protocol.md'),
        'study_id':'protocol_repair_20261020_v1','source_sha256':EXPECTED_SOURCE,
        'research_exception':{'authorization':'采用自动验证，取消人工评分并记录协议变更',
                              'human_ratings_or_agreement':'NOT_COLLECTED'},
        'input_correction':{'reason':'old corpus repeated final diagnosis/action across chain ancestors; original section3.1 not met',
                            'old_study':'preserved; no pooling, replacement, or usage backfill',
                            'tasks':144,'public_tasks':24,'formal_tasks':120,'formal_chains':20,
                            'full_ancestor_pairs':360,'repeated_expected_pairs':0,
                            'effective_output_contract':'fixed 24 mechanism/action pairs, identical across arms; old effective contract had8',
                            'source_sha_is_not_effective_contract_equivalence':True},
        'models':{'main':'qwen3.7-plus','cross':'doubao-seed-2-0-mini-260215'},
        'endpoint':'https://www.dmxapi.cn/v1/chat/completions',
        'request':{'temperature':0,'max_tokens':1500,'timeout_sec':60,'max_attempts_including_first':3,
                   'main_extra_body':{'enable_thinking':False},'cross_extra_body':{'thinking':{'type':'disabled'}},
                   'provider_seed':'NOT_SENT; local seed20261020 does not control provider sampling'},
        'public':{'units':144,'tasks':24,'repeats':1,'arms':['T0','S0','SS0','T1','S1','SS1'],
                  'maximum_attempts':432,'purpose':'validate facts, recorder, real usage and resources; no formal effect estimates',
                  'exclude_from_all_formal_estimates':True},
        'formal_plan_status':'CANDIDATE; full freeze only after public calibration, power and budget gates',
        'formal_units_candidate':6360,'retrieval_units_candidate':14400,
        'paired_primary':{'pairs':[['T0','S0'],['S0','SS0'],['T0','T1'],['S0','S1'],['SS0','SS1']],
                          'endpoints':['provider_input_tokens','observable_ipc_bytes'],
                          'sole_holm_family':10,'bootstrap_draws':10000,'bootstrap':'domain-stratified whole-chain resampling; repeats averaged within task',
                          'p_value':'paired sign flip on chain means, exact2^20 or >=100000 random flips',
                          'state_pair_population':108,'other_pair_population':120},
        'quality':{'unacceptable_loss_percentage_points':5,'safety_bound':'conservative paired exact/chain bound; no degenerate zero-width safety interval',
                   'power_data':'only new public cohort, never formal outcomes',
                   'if_underpowered':'retain120 and withhold noninferiority wording; original section6 explicitly permits this',
                   'no_automatic_expansion':'expansion requires separate preformal input/version/budget registration within original allowed branch'},
        'missing_failures':{'quality':'all planned units incl timeouts/crashes; no post-outcome dropping',
                            'usage':'unknown=null; incomplete paired primary costs have no CI/p; no local estimate/zero/success-only substitution',
                            'both_success':'exploratory only, report selection bias',
                            'unsupported':'retain explicit UNSUPPORTED/NOT_APPLICABLE; never0ms/0B'},
        'workload_equivalence':'mark WORK_NOT_EQUIVALENT when tools/steps/quality change; never call early failure efficiency',
        'memory':'independent empty phase/model/arm/repeat/chain stores; prior same-chain only; seeded conditions have matched empty control',
        'measurement':{'per_uri':'reference metadata/hash/depth, actual materialization, read/compute, RPC handoff, release',
                        'mmap':'measure native mmap; existing arena reuse is NO_NEW_MAP, not invented0 latency',
                        'fused_read_compute':'native fused matmul/cast is not an independently timed pure read',
                        'index':'native index-only operation plus separate enclosing memory operation; no extra embedding recomputation',
                        'mpi':'Runtime reads SHM, ndarray crosses RPC, child computes ranking; not child direct SHM map',
                        'ipc':'Runtime endpoint once incl framed control/ACK/RPC, excludes HTTP and local/logical state bytes',
                        'latency':'exclusive spans only additive; SDK wait not provider queue or pure RTT; no overhead subtraction',
                        'resource_sampling_ms':200,'rss':'process group RSS, shared pages counted repeatedly; not PSS'},
        'budget':'manifests/budget_limits.json; public cap144units/432attempts; prices and currency cap unspecified, failed request billing unknown',
        'resume':'single flock; terminal units skipped; unmatched starts block automatic replay; idempotency provider support not assumed',
        'environment':'openEuler userspace in Docker/WSL2,2CPU/2GiB, no native openEuler kernel claim',
        'timing':'real timing and offline benchmark workloads run in separate windows; no heavy preparation during real run',
        'delivery':'raw append-only, versioned analysis,9fig/6tables where supported, claims-to-evidence, failures and independent rebuild retained'}
    assert not (OUT/'protocol.yaml').exists()
    # JSON is valid YAML; serialization avoids undocumented YAML type coercions.
    write_new(OUT/'protocol.yaml',protocol)
    PILOT.mkdir(); drivers=PILOT/'drivers';drivers.mkdir()
    paths=list(HERE.glob('*.py'))
    paths += [Path('/drivers')/n for n in ['formal_fact_calibration.py','formal_recording.py',
              'formal_prompt_adapter.py','formal_fixture_checks.py']]
    for src in paths:
        shutil.copyfile(src,drivers/src.name)
    env={
        'created_utc':now(),'python':sys.version,'platform':platform.platform(),'uname':list(platform.uname()),
        'distribution':Path('/etc/os-release').read_text(),'source_sha256':source_sha(),
        'caps':{name:(Path('/sys/fs/cgroup')/name).read_text().strip() for name in ['cpu.max','memory.max','pids.max']},
        'dev_shm_bytes':os.statvfs('/dev/shm').f_frsize*os.statvfs('/dev/shm').f_blocks,
        'package_versions':{k:importlib.metadata.version(k) for k in ['numpy','openai','httpx','psutil','autogen-agentchat','langgraph']},
        'host_background_visibility':'uncontrolled Windows background load/CPU frequency/VM preemption; do not claim isolation'}
    checks={}
    for name,args in [('pip_check',[sys.executable,'-m','pip','check']),('dependencies',[sys.executable,'-m','pip','freeze'])]:
        result=subprocess.run(args,capture_output=True,text=True,timeout=60)
        (PILOT/(name+'.stdout.log')).write_text(result.stdout)
        (PILOT/(name+'.stderr.log')).write_text(result.stderr)
        checks[name]={'exit_code':result.returncode,'stdout_sha256':digest(PILOT/(name+'.stdout.log'))}
        assert result.returncode==0,f'{name} failed; registration retained, no model call'
    write_new(PILOT/'environment.json',env)
    write_new(PILOT/'preflight.json',{'status':'PASS_SOURCE_DEPENDENCIES_AND_EXISTING_CALIBRATIONS','checks':checks,
        'source_test_scope':'existing unchanged-source v4 actual653:651pass1WSLplatformfailure1namespaceSkip; not rerun or represented allgreen',
        'new_effective_contract_coverage':'118 offline paths,90 metric units,420 local SDK fixture units; no claim of real provider calibration'})
    inputs=[OUT/'protocol.yaml',OUT/'manifests/original_protocol.md',OUT/'manifests/formal_plan.json',
            OUT/'manifests/budget_limits.json',OUT/'manifests/native_configurations.json',
            OUT/'calibration/offline_v2/audit.json',OUT/'calibration/metric_full_v3/metric_audit.json',
            OUT/'calibration/sdk_overhead_full_v2/metric_audit.json']
    inputs+=sorted((OUT/'corpus/candidate_v2').glob('*'))
    inputs+=list(drivers.glob('*.py'))+[PILOT/'environment.json',PILOT/'preflight.json',HERE/'public_pilot.py']
    freeze={'status':'REGISTERED_PUBLIC_ONLY_BEFORE_NEW_OUTCOMES','created_utc':now(),'source_sha256':EXPECTED_SOURCE,
            'public_unit_ids':[u['run_id'] for u in public],'public_task_ids':sorted({u['task_id'] for u in public}),
            'max_public_attempts':432,'files':{str(p):digest(p) for p in inputs if p.is_file()},
            'driver_snapshot':str(drivers),'not_a_formal_freeze':True}
    write_new(PILOT/'registration.json',freeze)
    print(json.dumps({'status':freeze['status'],'public_units':144,'snapshot_files':len(freeze['files']),'source_sha256':EXPECTED_SOURCE}))

def validate():
    freeze=load(PILOT/'registration.json')
    assert source_sha()==freeze['source_sha256']
    for name,expected in freeze['files'].items():assert digest(Path(name))==expected,f'registered input changed: {Path(name).name}'
    expected=set(freeze['public_unit_ids'])
    for name in ['task_unit_starts.jsonl','provider_attempt_starts.jsonl','provider_calls.jsonl','task_runs.jsonl']:
        records=lines(name)
        assert all(r['run_id'] in expected for r in records),'formal/foreign outcome before public gate'
    assert len(lines('provider_attempt_starts.jsonl'))<=432
    return freeze

def run():
    freeze=validate()
    execution=PILOT/'execution.json'
    if execution.exists():raise RuntimeError('pilot launch already recorded; inspect actual process/outcomes, do not replay')
    # Lock is independent of a status file; never trust a stale PID/lock as liveness.
    with (OUT/'manifests/real_provider_execution.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        sys.path.insert(0,freeze['driver_snapshot'])
        import real_driver as driver
        assert Path(driver.__file__).parent==Path(freeze['driver_snapshot'])
        for module_name in ['bootstrap','contract','oracle','prompt_adapter','instrumentation','formal_recording','formal_fact_calibration']:
            assert Path(sys.modules[module_name].__file__).parent==Path(freeze['driver_snapshot']),f'unregistered module origin: {module_name}'
        record={'status':'RUNNING','started_utc':now(),'pid':os.getpid(),'source_sha256':source_sha(),
                'registration_sha256':digest(PILOT/'registration.json'),'max_public_units':144,'max_public_attempts':432,
                'driver_origin':driver.__file__}
        write_new(execution,record)
        began=time.perf_counter()
        failure=None
        try:
            driver.run(['public_calibration'])
            validate()
            tasks=lines('task_runs.jsonl');starts=lines('provider_attempt_starts.jsonl');calls=lines('provider_calls.jsonl')
            assert len(tasks)==len({r['run_id'] for r in tasks})==144
            assert {r['request_id'] for r in starts}=={r['request_id'] for r in calls}
            assert {r['run_id'] for r in tasks}==set(freeze['public_unit_ids'])
            record.update(status='PUBLIC_TERMINAL_AWAIT_REAL_METRIC_POWER_BUDGET_GATES',terminal_units=144,
                          successful_units=sum(bool(r['success']) for r in tasks),provider_attempts=len(starts),
                          attempt_errors=sum(r.get('status')!='OK' for r in calls),
                          formal_units_executed=0,full_formal_freeze=False)
        except BaseException as exc:
            failure=exc;record.update(status='ERROR_RETAINED_NO_AUTOMATIC_REPLAY',error_type=type(exc).__name__,
                error=str(exc).replace(driver.SECRET,'[REDACTED]') if driver.SECRET else str(exc),
                terminal_units=len(lines('task_runs.jsonl')),provider_attempts=len(lines('provider_attempt_starts.jsonl')))
        finally:
            record.update(finished_utc=now(),wall_sec=time.perf_counter()-began)
            # This lifecycle receipt is mutable; terminal raw and registered inputs are not.
            execution.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(record,ensure_ascii=False),flush=True)
        if failure:raise failure

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','validate','run']);args=parser.parse_args()
    if args.command=='prepare':prepare()
    elif args.command=='validate':print(json.dumps({'status':'PUBLIC_REGISTRATION_VALID','registered_units':len(validate()['public_unit_ids'])}))
    else:run()
