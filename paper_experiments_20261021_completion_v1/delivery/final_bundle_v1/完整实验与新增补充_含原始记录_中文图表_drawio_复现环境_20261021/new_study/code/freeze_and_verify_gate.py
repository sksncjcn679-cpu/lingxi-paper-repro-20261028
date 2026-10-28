"""One pre-main freeze and evidence-bound gate; no provider requests."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def rows(p):return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()]
def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2)
def main():
    b=ROOT/'main_batch'
    runs=rows(b/'raw/task_runs.jsonl');calls=rows(b/'raw/provider_calls.jsonl');accounts=rows(b/'raw/provider_accounting.jsonl')
    starts=rows(b/'raw/provider_attempt_starts.jsonl')
    assert len(runs)==len(calls)==len(accounts)==len(starts)==6
    assert {r['phase'] for r in runs}=={'public_calibration'}
    assert {r['arm'] for r in runs}=={'T0','S0','SS0','T1','S1','SS1'}
    assert {r['request_id'] for r in calls}=={r['client_request_id'] for r in accounts}=={r['request_id'] for r in starts}
    assert all(r.get('provider_usage_complete') is True and r['span_partition_error_sec']<1e-6 for r in runs)
    assert all(r['status']=='OK' and r.get('business_facts_equal') is True for r in calls)
    assert all(r['status']=='SUCCESS_COMPLETE_USAGE' and r['usage_complete'] is True
        and len(r['response_ID_headers'].get('x-oneapi-request-id',[]))==1 for r in accounts)
    assert len({r['response_ID_headers']['x-oneapi-request-id'][0] for r in accounts})==6
    assert read(ROOT/'usage_preflight/billing_bridge_receipt.json')['status']=='EXACT_HTTP_HEADER_TO_ORIGINAL_BILLING_ID_AND_USAGE_VERIFIED'
    assert read(ROOT/'sdk_fixture_smoke_v3/metric_audit.json')['status']=='PASS_LOCAL_SDK_USAGE_CONTRACT_AND_PAIRED_RECORDING_OVERHEAD'
    assert read(ROOT/'usage_missing_response_fixture/verification.json')['next_attempt_blocked'] is True
    assert read(ROOT/'usage_accounting_fixture_receipt.json')['network_or_model_calls']==0
    for attempt in ['public_v1','usage_failure_v1','sdk_fixture_v3']:
        assert read(ROOT/'logs'/attempt/'execution_receipt.json')['actual_exit_code']==0
    seed=Path('E:/codex_rebuilds/completion_20261021_v1/seed_write/run_v3/boundary_verification_v1.json')
    assert seed.exists(), 'offline actual-write boundary verification must finish before real main'
    stress=Path('E:/codex_rebuilds/completion_20261021_v1/stress_ci/decoding_validation_receipt.json')
    assert stress.exists()
    plan=read(b/'manifests/formal_plan.json');mainplan=[u for u in plan if u['phase']=='main']
    assert len(mainplan)==2160 and len({u['task_id'] for u in mainplan})==120
    assert len({u['chain_id'] for u in mainplan})==20
    assert Counter(u['arm'] for u in mainplan)==dict.fromkeys(['T0','S0','SS0','T1','S1','SS1'],360)
    files={}
    for p in sorted((ROOT/'code/native').glob('*.py')):files['/code/native/'+p.name]=sha(p)
    for p in sorted((ROOT/'tiktoken_cache').iterdir()):files['/tiktoken_cache/'+p.name]=sha(p)
    for relative in ['corpus/tasks.jsonl','manifests/formal_plan.json','manifests/budget_limits.json','manifests/replication_design.json']:
        files['/output/main_batch/'+relative]=sha(b/relative)
    freeze={'created_utc':datetime.now(timezone.utc).isoformat(),
        'source_sha256':'a040080a8c1011bc1281439b6398d177b7517a2b6a65c7bcfda9e90a1937b562',
        'files':files,'study_id':'completion_main_20261021_v1','main_units':2160,
        'prior_formal_outcomes':'Known before replication; no input, quality rubric, model, topology or method tuning. Replication recorded independently.',
        'same_original_tasks':'120 tasks/20chains,108 state-applicable,6arms,3 repeats; no increased independent sample count'}
    save(b/'manifests/formal-freeze.json',freeze)
    gate={'status':'PASS_FOR_BOUNDED_MAIN_WITH_UNKNOWN_USAGE_STOP','verified_utc':datetime.now(timezone.utc).isoformat(),
        'exact_real_billing_bridge_requests':1,'real_six_arm_public_units':6,
        'local_http_smoke_units':4,'local_missing_usage_fixture_dispatches':1,
        'no_automatic_provider_retries_after_errors':True,
        'failure_billing_not_assumed_zero':True,'preflight_is_not_guarantee_of_future_provider_availability':True,
        'main_plan_units':2160,'main_freeze_sha256':sha(b/'manifests/formal-freeze.json'),
        'input_sha256':{str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'usage_preflight/result.json',ROOT/'usage_preflight/billing_bridge_receipt.json',ROOT/'usage_accounting_fixture_receipt.json',ROOT/'usage_missing_response_fixture/verification.json',ROOT/'sdk_fixture_smoke_v3/metric_audit.json']},
        'offline_complete_before_main':{'seed_receipt_sha256':sha(seed),'stress_receipt_sha256':sha(stress)},
        'historical_results_changed':False}
    save(ROOT/'usage_preflight/final_gate.json',gate)
    print(json.dumps({'status':gate['status'],'main_plan_units':2160,'frozen_files':len(files)},ensure_ascii=False))
if __name__=='__main__':main()
