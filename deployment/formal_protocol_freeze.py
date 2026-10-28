"""Prepare protocol metadata and freeze only after independent public calibration gates."""
from pathlib import Path
import argparse
from collections import Counter
import json
import shutil
import sys
import uuid
import yaml
sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
from agent_runtime.evaluation.runner import _source_tree_sha256
from formal_recording import now,sha
from formal_real_driver import OUT,ROOT,ARMS,MODELS


def write_new(path,value):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)


def prepare_protocol():
    plan_path=OUT/'manifests/formal_plan.json'
    plan=json.loads(plan_path.read_text())
    # Framework handoffs are in-process. UDS is a property of the native system only.
    if any(u['phase'].startswith('external_') and u['topology']!='framework_inprocess' for u in plan):
        old=OUT/'manifests/formal_plan_draft_v1.json'
        if old.exists():raise RuntimeError('prior plan draft already archived; refusing ambiguous change')
        shutil.copyfile(plan_path,old)
        for unit in plan:
            if unit['phase'].startswith('external_'):
                unit['topology']='framework_inprocess'
                identity={key:value for key,value in unit.items() if key not in ('run_id','unit_id')}
                unit['run_id']=unit['unit_id']=uuid.uuid5(uuid.NAMESPACE_URL,'formal_20261019/'+json.dumps(identity,sort_keys=True)).hex
        plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    counts=dict(Counter(u['phase'] for u in plan))
    protocol={'authority':'manifests/original_protocol.md','protocol_sha256':sha((OUT/'manifests/original_protocol.md').read_bytes()),
        'prepared_utc':now(),'source_sha256':_source_tree_sha256(ROOT),
        'research_changes':['only user-authorized automatic task/query verification instead of human review and blind ratings'],
        'user_authorization':'采用自动验证，取消人工评分并记录协议变更',
        'models':MODELS,'endpoint':'https://www.dmxapi.cn/v1/chat/completions',
        'request_parameters':{'temperature':0,'max_tokens':1500,'timeout_sec':60,'max_attempts_including_first':3},
        'groups':ARMS,'formal_tasks':120,'formal_chains':20,'public_pilot_tasks':24,
        'repeat_definition':'independent initially empty memory stores per phase/model/arm/repeat/chain; no new independent case from repetition',
        'formal_units':{k:v for k,v in counts.items() if k!='public_calibration'},'public_units':counts['public_calibration'],
        'unit_ids':[u['run_id'] for u in plan],
        'formal_subset_rule':'checkpoints 1,3,5 of all 20 frozen formal chains: 15/domain, five per difficulty; selected before formal outcomes',
        'arm_order':'balanced cyclic rotation by task ordinal plus repeat; same causal order in each chain',
        'memory_conditions':{'tasks':60,'repeats':3,'conditions':['related','unrelated','stale','wrong'],
            'additional_empty_control':True,'initialization':'one predeclared procedural fixture per task/condition; none control empty; independent stores',
            'causal_note':'condition effects and adopted-success are distinct; extra seeded conditions do not alter six-arm empty-start main'},
        'ablation_reference':'same subset, three repeats; full SS1 baseline in separate matched phase to keep memory history equal',
        'exclusion_rules':{'task_quality':'all planned units including failures/timeouts; no post-outcome exclusions',
            'state_increment':'report all cases and predeclared 108 applicable subset separately',
            'cost_failure_bias':'both-success subset is exploratory; early failures cannot be efficiency gains',
            'unknown_usage':'null, never substitute local tokenizer or prior outputs',
            'unsupported':'retain planned denominator and label UNSUPPORTED/INCOMPLETE, no zero imputation',
            'history':'all historical pilots and recording-development fixtures excluded from formal estimates'},
        'prompt_contract':'native lean text / structured text view preserve different representation and handoff; complete source observations exactly once; no identical-prompt substitution',
        'tools':'one model-selected deterministic read-only authored fixture check; never modifies host services',
        'ipc_scope':'Runtime endpoint actual successful framed socket send+recv once; includes control negotiation/ACK and AgentHost RPC; excludes provider HTTP, kernel bookkeeping, ancillary representation and local/logical bytes',
        'process_state_scope':'Runtime reads native state and returns ndarray via RPC; child computes stable operand ranking, not direct child SHM mapping',
        'latency_scope':'exclusive monotonic interval partition; nested inclusive traces retained but not summed twice; terminal buffered archive flush separately timed',
        'resources':'200ms process group RSS/CPU/FD and cgroup memory/load; RSS sum repeats shared pages; no full Windows-host load visibility',
        'calibration_scope':'144 PUBLIC real units for provider/facts validation; overlaps separately logged offline jobs and is excluded from formal latency effects; quiescent offline overhead measured separately',
        'environment':'openEuler24.03SP3 userspace in Docker on Windows WSL2 kernel; 2CPU quota,2GiB container; not a native openEuler kernel',
        'quality_wording':'noninferiority claim prospectively withheld; report paired differences and conservative uncertainty',
        'budget':'manifests/budget_limits.json; finite unit/attempt/request-byte/token ceilings; actual currency account billing unknown',
        'analysis_plan':'analysis/stats/statistical_plan_v2.json plus native public and unequal-chain preformal addenda; original v1 retained',
        'resumption':'single-provider-driver flock; terminal tasks skipped; any attempt/task start without terminal task blocks replay; server idempotency support not assumed'}
    path=OUT/'protocol.yaml'
    with path.open('x',encoding='utf-8') as stream:yaml.safe_dump(protocol,stream,allow_unicode=True,sort_keys=False)
    alignment_path=OUT/'manifests/protocol_alignment.json'
    alignment=json.loads(alignment_path.read_text())
    write_new(OUT/'manifests/protocol_alignment_before_user_exception.json',alignment)
    alignment['human_requirements']='user-authorized automatic verification; no human blind ratings or interrater agreement; see protocol_changes.md'
    alignment['human_exception_registered_before_formal_calls']=True
    alignment_path.write_text(json.dumps(alignment,ensure_ascii=False,indent=2))
    print(json.dumps({'protocol_prepared':True,'all_plan_units':len(plan),'formal_units':len(plan)-144,'formal_main_units':counts['main']}))


def estimate():
    rows=[json.loads(s) for s in (OUT/'raw/task_runs.jsonl').read_text().splitlines()]
    public=[r for r in rows if r['phase']=='public_calibration']
    if len(public)!=144 or any(r['phase']=='main' for r in rows):
        raise RuntimeError('estimate requires complete public-only real pilot before formal main')
    calls=[json.loads(s) for s in (OUT/'raw/provider_calls.jsonl').read_text().splitlines()]
    uses=[c['usage_raw'] for c in calls if c.get('usage_raw')]
    planned=json.loads((OUT/'manifests/formal_plan.json').read_text())
    remaining=[u for u in planned if u['phase']!='public_calibration']
    avg_input=sum(u.get('prompt_tokens',0) for u in uses)/144
    avg_output=sum(u.get('completion_tokens',0) for u in uses)/144
    # Vendor-original Qwen price is only a contextual reference; account/DMX ratio is absent.
    count_by_model=dict(Counter(u['model'] for u in remaining))
    model_estimates={}
    for model,count in count_by_model.items():
        input_price,output_price=(2,8) if model=='main' else (.2,2)
        model_estimates[model]={'task_units':count,'input_tokens_using_main_public_mean':count*avg_input,
            'output_tokens_using_main_public_mean':count*avg_output,
            'price_reference_rmb_per_million_input':input_price,'price_reference_rmb_per_million_output':output_price,
            'reference_uncached_formula_rmb':count*(avg_input*input_price+avg_output*output_price)/1e6,
            'actual_account_cost_rmb':None,'cache_accounting':'not forecast; actual cached usage retained per call',
            'price_status':'vendor-original custom tier; DMX model ratio/account discount unknown' if model=='main' else 'public DMX ratio snapshot; account billing still unverified',
            'token_prediction_status':'main public native prompt pilot; cross model/ablation prompts can differ'}
    result={'created_utc':now(),'basis':'144 public native real-provider units; no formal outputs used',
        'observed_api_attempts':len(calls),'observed_attempts_per_task':len(calls)/144,
        'observed_mean_prompt_tokens':avg_input,'observed_mean_completion_tokens':avg_output,
        'observed_mean_wall_sec':sum(r['wall_sec'] for r in public)/144,
        'time_forecast_sec':len(remaining)*sum(r['wall_sec'] for r in public)/144,
        'time_forecast_scope':'planning only; pilot overlaps offline load; runtime/ablation/provider behavior may differ',
        'model_estimates':model_estimates,'retry_allowance':'ceilings permit up to three attempts/task, approximately 3x reference request cost if all retried',
        'actual_total_currency_cost':'UNKNOWN; no invoice or exact account ratio',
        'budget_upper_bounds':json.loads((OUT/'manifests/budget_limits.json').read_text())}
    write_new(OUT/'manifests/preformal_budget_estimate.json',result)
    print(json.dumps({'public_units':144,'remaining_planned_units':len(remaining),'estimated_seconds':result['time_forecast_sec'],'actual_currency_cost':'UNKNOWN'}))


def freeze():
    from formal_measurement_audit import calibration_gate
    gate=calibration_gate(OUT,OUT/'calibration/recorder_dry_v4')
    write_new(OUT/'analysis/measurement_freeze_gate.json',gate)
    # The independent gate defines its own exact status; keep all diagnostic evidence.
    status=gate.get('status','')
    if status not in ('PASS','PASS_FREEZE','PASS_FOR_FREEZE','READY_TO_FREEZE'):
        raise RuntimeError('independent measurement gate did not permit freeze: '+str(status))
    for name in ['analysis/stats/statistical_plan_addendum_native_public_v1.json',
                 'manifests/preformal_budget_estimate.json','analysis/overhead_calibration.json',
                 'protocol.yaml']:
        if not (OUT/name).exists():raise RuntimeError('missing preformal gate evidence: '+name)
    if any(json.loads(s)['phase']!='public_calibration' for s in (OUT/'raw/task_runs.jsonl').read_text().splitlines()):
        raise RuntimeError('formal outcomes present before freeze')
    files=[CORPUS for CORPUS in (OUT/'corpus/automatic_validated_v1').glob('*') if CORPUS.is_file()]
    files += [OUT/'manifests/formal_plan.json',OUT/'manifests/native_configurations.json',
              OUT/'manifests/budget_limits.json',OUT/'protocol.yaml',OUT/'analysis/stats/statistical_plan.json',
              OUT/'analysis/stats/statistical_plan_v2.json',
              OUT/'analysis/stats/statistical_plan_addendum_unequal_chain_v2.json',
              OUT/'analysis/stats/statistical_plan_addendum_native_public_v1.json']
    files += [path for path in Path('/drivers').glob('formal*.py')
              if path.name not in ('formal_report.py','formal_status.py')]
    record={'frozen_at_utc':now(),'source_sha256':_source_tree_sha256(ROOT),
        'files':{str(path):sha(path.read_bytes()) for path in files},
        'formal_model_outcomes_before_freeze':0,'human_review':'user authorized automatic verification',
        'presentation_helpers_excluded':['formal_report.py','formal_status.py; final hashes retained in reproduction archive'],
        'quality_noninferiority_claim':'WITHHELD_PROSPECTIVELY'}
    write_new(OUT/'manifests/formal-freeze.json',record)
    for path in files:
        if str(path).startswith('/drivers/'):
            target=OUT/'drivers/frozen'/path.name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,target)
    print(json.dumps({'status':'FROZEN','files':len(files),'source_sha256':record['source_sha256']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare-protocol','estimate','freeze'])
    args=parser.parse_args()
    {'prepare-protocol':prepare_protocol,'estimate':estimate,'freeze':freeze}[args.command]()
