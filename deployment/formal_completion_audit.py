"""Read-only reconciliation of the complete frozen provider execution matrix.

This is a data-completeness gate, not an effect analysis or a replacement for
native/external measurement audits. It never imports a model client or a secret.
Run only after provider timing has ended; incomplete plans are reported honestly.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

DEFAULT_OUT=Path('/evidence/formal_20261019')
MODELS={'main':'qwen3.7-plus','cross':'doubao-seed-2-0-mini-260215'}
COMMON=('unit_id','phase','task_id','chain_id','domain','difficulty','arm','repeat',
        'model','processes','topology','intervention')
JOIN_TABLES=('model_io','messages','states','memory_events','execution_events',
             'external_execution_events','external_framework_runs','ipc_socket_events',
             'ipc_accounting','spans','resource_samples','http_request_preflight','provider_retry_waits')


def rows(path):
    if not path.exists():return
    with path.open(encoding='utf-8') as stream:
        for number,line in enumerate(stream,1):
            if line.strip():
                value=json.loads(line)
                if not isinstance(value,dict):raise ValueError(f'{path.name}:{number}: object required')
                yield value


def file_sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def value_sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def closed_timestamp(value):
    if not isinstance(value,str):return False
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is not None
    except ValueError:return False


def source_sha(root):
    candidates=[root/'pyproject.toml']
    for name in ('src','scenarios','scripts'):
        folder=root/name
        if folder.is_dir():
            candidates.extend(p for p in folder.rglob('*') if p.is_file() and p.suffix in {'.py','.sh','.json','.npz'})
    digest=hashlib.sha256()
    for path in sorted(candidates,key=lambda p:p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode());digest.update(b'\0')
        digest.update(path.read_bytes());digest.update(b'\0')
    return digest.hexdigest()


def audit(out=DEFAULT_OUT,driver_root=Path('/drivers'),source_root=Path('/opt/lingxi/formal-checkout')):
    out=Path(out);raw=out/'raw';errors=[];warnings=[]
    def issue(code,**detail):errors.append({'code':code,**detail})
    plan=json.loads((out/'manifests/formal_plan.json').read_text())
    planned={u['run_id']:u for u in plan}
    if len(plan)!=6504 or len(planned)!=6504:issue('FROZEN_PLAN_NOT_6504_UNIQUE_UNITS')
    terminals=list(rows(raw/'task_runs.jsonl'))
    counts=Counter(r.get('run_id') for r in terminals)
    missing=sorted(set(planned)-set(counts));extra=sorted(set(counts)-set(planned))
    duplicates={key:n for key,n in counts.items() if n!=1}
    if missing or extra or duplicates:
        return {'status':'INCOMPLETE' if missing and not extra and not duplicates else 'FAIL',
            'created_utc':datetime.now(timezone.utc).isoformat(),'planned_units':len(plan),
            'terminal_units':len(terminals),'missing_unit_ids':missing,'extra_unit_ids':extra,
            'duplicate_terminal_ids':duplicates,'measurement_errors':errors,
            'scope':'coverage only; no statistics or provider replay performed'}
    raw_inputs={name:raw/f'{name}.jsonl' for name in (*JOIN_TABLES,'task_unit_starts','task_runs','provider_attempt_starts','provider_calls')
        if (raw/f'{name}.jsonl').is_file()}
    raw_initial_sha256={name:file_sha(path) for name,path in raw_inputs.items()}
    by_terminal={r['run_id']:r for r in terminals}
    starts=list(rows(raw/'task_unit_starts.jsonl'))
    start_counts=Counter(r.get('run_id') for r in starts)
    for run_id in set(planned)|set(start_counts):
        if run_id not in planned:issue('UNPLANNED_TASK_START',run_id=run_id)
        if start_counts[run_id]!=1:issue('TASK_START_NOT_ONE_TO_ONE',run_id=run_id,starts=start_counts[run_id])
    for item in starts:
        if not closed_timestamp(item.get('start_utc')):issue('TASK_START_TIMESTAMP_MISSING_OR_INVALID',run_id=item.get('run_id'))
    freeze=json.loads((out/'manifests/formal-freeze.json').read_text())
    freeze_manifest_initial_sha256=file_sha(out/'manifests/formal-freeze.json')
    frozen_observed={}
    for original,expected in freeze['files'].items():
        if original.startswith('/evidence/formal_20261019/'):
            path=out/original.removeprefix('/evidence/formal_20261019/')
        elif original.startswith('/drivers/'):
            path=Path(driver_root)/original.removeprefix('/drivers/')
        else:path=Path(original)
        observed=file_sha(path) if path.is_file() else None
        frozen_observed[str(path)]=observed
        if observed!=expected:issue('FROZEN_FILE_CHANGED_OR_MISSING',frozen_path=original)
    observed_source=source_sha(Path(source_root)) if Path(source_root).exists() else None
    if observed_source!=freeze['source_sha256']:issue('RUNTIME_SOURCE_HASH_MISMATCH',observed=observed_source)
    corpus={r['task_id']:r for r in rows(out/'corpus/automatic_validated_v1/tasks.jsonl')}
    for run_id,row in by_terminal.items():
        unit=planned[run_id]
        for field in COMMON:
            if row.get(field)!=unit.get(field):issue('TERMINAL_PLANNED_IDENTITY_DIFFERS',run_id=run_id,field=field)
        for field,expected in [('source_sha256',freeze['source_sha256']),('model_id',MODELS[unit['model']]),
                               ('model_evidence','real_provider'),('task_sha256',corpus[unit['task_id']]['content_sha256'])]:
            if row.get(field)!=expected:issue('TERMINAL_PROVENANCE_MISMATCH',run_id=run_id,field=field)
        if type(row.get('success')) is not bool:issue('TERMINAL_SUCCESS_NOT_BOOLEAN',run_id=run_id)
        if row.get('status') not in {'COMPLETED','FAILED','INCOMPLETE_BUDGET_STOP','UNSUPPORTED','NON_COMPARABLE'}:
            issue('TASK_RECORD_NOT_TERMINAL',run_id=run_id,status=row.get('status'))
        if not closed_timestamp(row.get('start_utc')) or not closed_timestamp(row.get('end_utc')):
            issue('TERMINAL_TIMESTAMPS_MISSING_OR_INVALID',run_id=run_id)
        elif datetime.fromisoformat(row['end_utc'])<datetime.fromisoformat(row['start_utc']):
            issue('TERMINAL_UTC_ORDER_INVALID',run_id=run_id)
        if row.get('success') is True and row.get('status')!='COMPLETED':issue('SUCCESS_WITH_NONCOMPLETED_STATUS',run_id=run_id)
        if row.get('public_state_calibration_override') and unit['phase']!='public_calibration':
            issue('PUBLIC_STATE_OVERRIDE_OUTSIDE_PUBLIC_PHASE',run_id=run_id)
        if unit['phase']!='public_calibration' and corpus[unit['task_id']]['split']!='formal':
            issue('PUBLIC_TASK_CONTAMINATES_FORMAL_PHASE',run_id=run_id)
    attempt_starts=list(rows(raw/'provider_attempt_starts.jsonl'))
    calls=list(rows(raw/'provider_calls.jsonl'))
    started=Counter(r.get('request_id') for r in attempt_starts)
    ended=Counter(r.get('request_id') for r in calls)
    for request_id in set(started)|set(ended):
        if started[request_id]!=1 or ended[request_id]!=1:
            issue('ATTEMPT_START_END_NOT_ONE_TO_ONE',request_id=request_id,starts=started[request_id],ends=ended[request_id])
    calls_by=defaultdict(list);call_ids={r.get('request_id'):r for r in calls}
    for table,items in [('provider_attempt_starts',attempt_starts),('provider_calls',calls)]:
        for item in items:
            run_id=item.get('run_id');unit=planned.get(run_id)
            if unit is None:issue('UNPLANNED_PROVIDER_RECORD',table=table,run_id=run_id);continue
            for field in COMMON:
                if item.get(field)!=unit.get(field):issue('PROVIDER_RECORD_PLANNED_IDENTITY_DIFFERS',table=table,run_id=run_id,field=field)
            for field,expected in [('source_sha256',freeze['source_sha256']),('model_id',MODELS[unit['model']]),
                                   ('model_evidence','real_provider'),('task_sha256',corpus[unit['task_id']]['content_sha256'])]:
                if item.get(field)!=expected:issue('PROVIDER_RECORD_PROVENANCE_MISMATCH',table=table,run_id=run_id,field=field)
            if table=='provider_attempt_starts':
                end=call_ids.get(item.get('request_id'))
                if end and any(item.get(key)!=end.get(key) for key in ('run_id','attempt','actual_prompt_sha256','start_utc')):
                    issue('PROVIDER_START_END_IDENTITY_DIFFERS',request_id=item.get('request_id'))
    for call in calls:
        run_id=call.get('run_id');calls_by[run_id].append(call)
        unit=planned.get(run_id)
        if unit is None:issue('ORPHAN_PROVIDER_ATTEMPT',request_id=call.get('request_id'));continue
        if call.get('request_id')!=f'{run_id}-{call.get("attempt")}':issue('REQUEST_ID_NOT_DETERMINISTIC',run_id=run_id)
        if call.get('endpoint')!='https://www.dmxapi.cn/v1/chat/completions':issue('ENDPOINT_MISMATCH',run_id=run_id)
        if call.get('requested_model')!=MODELS[unit['model']]:issue('REQUEST_MODEL_MISMATCH',run_id=run_id)
        expected_extra={'thinking':{'type':'disabled'}} if unit['model']=='cross' else {'enable_thinking':False}
        for field,value in [('temperature',0),('max_tokens',1500),('timeout_sec',60),('extra_body',expected_extra)]:
            if call.get(field)!=value:issue('REQUEST_PARAMETER_MISMATCH',run_id=run_id,field=field)
    for run_id,row in by_terminal.items():
        attempts=calls_by[run_id]
        numbers=sorted(c.get('attempt',-1) for c in attempts)
        if len(attempts)>3 or numbers!=list(range(1,len(attempts)+1)):
            issue('ATTEMPT_SEQUENCE_OR_LIMIT',run_id=run_id,attempts=numbers)
        if row.get('provider_attempts')!=len(attempts):issue('TASK_ATTEMPT_COUNT_MISMATCH',run_id=run_id)
        if not attempts:warnings.append({'code':'TERMINAL_WITHOUT_PROVIDER_ATTEMPT','run_id':run_id,
            'meaning':'retained pre-provider execution failure; usage unknown, never zero billed'})
    io_counts=Counter();preflights=defaultdict(list);file_counts={};runtime_pids=defaultdict(set)
    for name in JOIN_TABLES:
        path=raw/f'{name}.jsonl';number=0
        for item in rows(path):
            number+=1;run_id=item.get('run_id')
            if run_id not in planned:issue('ORPHAN_RECORDER_ROW',table=name,run_id=run_id);continue
            unit=planned[run_id]
            for field in COMMON:
                # The frozen sampler uses `processes` for its per-PID sample list,
                # shadowing the common topology boolean; terminal/plan is authoritative.
                if name=='resource_samples' and field=='processes':continue
                if field in item and item[field]!=unit.get(field):issue('RECORDER_PLANNED_IDENTITY_DIFFERS',table=name,run_id=run_id,field=field)
            if name=='resource_samples':
                samples=item.get('processes')
                if not isinstance(samples,list):issue('RESOURCE_PROCESS_SAMPLE_LIST_INVALID',run_id=run_id)
                else:
                    runtime_pids[run_id].update(p.get('pid') for p in samples if p.get('role')=='runtime' and type(p.get('pid')) is int)
            if name=='http_request_preflight':
                request_id=item.get('request_id');preflights[request_id].append(item)
                call=call_ids.get(request_id)
                if call is None:issue('HTTP_PREFLIGHT_WITHOUT_PROVIDER_ATTEMPT',run_id=run_id,request_id=request_id)
                elif call.get('run_id')!=run_id:issue('HTTP_PREFLIGHT_ATTEMPT_TASK_JOIN_DIFFERS',run_id=run_id,request_id=request_id)
            if name=='model_io':
                request_id=item.get('request_id');direction=item.get('direction')
                io_counts[(request_id,direction)]+=1
                call=call_ids.get(request_id)
                if call is None:issue('MODEL_IO_WITHOUT_PROVIDER_ATTEMPT',run_id=run_id);continue
                if call.get('run_id')!=run_id:issue('MODEL_IO_ATTEMPT_TASK_JOIN_DIFFERS',run_id=run_id,request_id=request_id)
                if direction=='request' and value_sha(item.get('messages'))!=call.get('actual_prompt_sha256'):
                    issue('MODEL_REQUEST_MESSAGES_HASH_MISMATCH',run_id=run_id)
                if direction=='response' and value_sha(item.get('response'))!=call.get('response_sha256'):
                    issue('MODEL_RESPONSE_HASH_MISMATCH',run_id=run_id)
                if direction=='http_request_body':
                    body=item.get('request')
                    if not isinstance(body,dict):issue('EXTERNAL_HTTP_BODY_NOT_OBJECT',request_id=request_id)
                    else:
                        if value_sha(body.get('messages'))!=call.get('actual_prompt_sha256'):
                            issue('EXTERNAL_HTTP_MESSAGES_HASH_MISMATCH',request_id=request_id)
                        expected_extra={'thinking':{'type':'disabled'}} if unit['model']=='cross' else {'enable_thinking':False}
                        for key,value in {'model':MODELS[unit['model']],'temperature':0,'max_tokens':1500,**expected_extra}.items():
                            if body.get(key)!=value:issue('EXTERNAL_HTTP_ACTUAL_PARAMETER_MISMATCH',request_id=request_id,field=key)
                    if item.get('actual_utf8_bytes')!=call.get('actual_request_utf8_bytes'):
                        issue('EXTERNAL_HTTP_SIZE_LINK_MISMATCH',request_id=request_id)
        file_counts[name]=number
    for call in calls:
        request_id=call['request_id'];run_id=call['run_id']
        if run_id not in planned:continue
        external=planned[run_id]['phase'].startswith('external_')
        if io_counts[(request_id,'request')]!=1:issue('SAVED_MODEL_REQUEST_NOT_UNIQUE',request_id=request_id)
        expected_responses=1 if call.get('status')=='OK' else 0
        if io_counts[(request_id,'response')]!=expected_responses:issue('SAVED_MODEL_RESPONSE_COUNT',request_id=request_id)
        dispatches=io_counts[(request_id,'http_request_body')] if external else len(preflights[request_id])
        if dispatches>1 or (call.get('status')=='OK' and dispatches!=1):issue('HTTP_DISPATCH_EVIDENCE_COUNT',request_id=request_id,count=dispatches)
        size=call.get('actual_request_utf8_bytes');digest=call.get('actual_http_body_sha256')
        if dispatches or call.get('status')=='OK':
            if type(size) is not int or size<=0 or not isinstance(digest,str) or re.fullmatch(r'[0-9a-f]{64}',digest) is None:
                issue('ACTUAL_HTTP_SIZE_HASH_MISSING_OR_INVALID',request_id=request_id)
        if not external and dispatches==1:
            observed=preflights[request_id][0]
            if observed.get('actual_http_body_bytes')!=call.get('actual_request_utf8_bytes') or observed.get('actual_http_body_sha256')!=call.get('actual_http_body_sha256'):
                issue('ACTUAL_HTTP_BODY_SIZE_HASH_LINK_MISMATCH',request_id=request_id)
    limits=json.loads((out/'manifests/budget_limits.json').read_text())
    if len(attempt_starts)>limits['max_provider_attempts']:issue('GLOBAL_ATTEMPT_CEILING_EXCEEDED')
    for call in calls:
        size=call.get('actual_request_utf8_bytes')
        if size is not None and (type(size) is not int or size>limits['max_actual_utf8_request_bytes'] or size<0):
            issue('ACTUAL_HTTP_REQUEST_SIZE_BOUND',request_id=call.get('request_id'))
    known_tokens=sum((r.get('usage_raw') or {}).get('total_tokens') or 0 for r in calls)
    if known_tokens>=limits['observed_reported_total_tokens_stop']:
        warnings.append({'code':'REPORTED_TOKEN_STOP_REACHED_REQUIRES_START_TIME_RECONCILIATION',
            'meaning':'a final response crossing the threshold does not itself prove a new attempt began after the stop'})
    helper_pipes=defaultdict(set)
    for run_id,row in by_terminal.items():
        if row['phase'].startswith('external_'):continue
        pids=runtime_pids[run_id]
        if len(pids)!=1:
            issue('NATIVE_RUNTIME_PID_NOT_UNIQUE_FROM_RESOURCE_SAMPLES',run_id=run_id,count=len(pids));continue
        before=set((row.get('fd_targets_before') or {}).values());after=set((row.get('fd_targets_after') or {}).values())
        helper_pipes[next(iter(pids))].update(target for target in after-before if isinstance(target,str) and target.startswith('pipe:['))
    for pid,pipes in helper_pipes.items():
        if len(pipes)>5:issue('GLOBAL_NATIVE_RUNTIME_HELPER_PIPE_GROWTH',runtime_pid=pid,distinct_new_helper_pipes=len(pipes))
    raw_final_sha256={name:file_sha(path) for name,path in raw_inputs.items()}
    if raw_final_sha256!=raw_initial_sha256:issue('RAW_INPUTS_CHANGED_DURING_COVERAGE_AUDIT')
    if file_sha(out/'manifests/formal-freeze.json')!=freeze_manifest_initial_sha256:
        issue('FREEZE_MANIFEST_CHANGED_DURING_COVERAGE_AUDIT')
    for name,observed in frozen_observed.items():
        path=Path(name)
        if (file_sha(path) if path.is_file() else None)!=observed:issue('FROZEN_INPUT_CHANGED_DURING_COVERAGE_AUDIT',path=name)
    if source_sha(Path(source_root))!=observed_source:issue('RUNTIME_SOURCE_CHANGED_DURING_COVERAGE_AUDIT')
    phases=Counter(u['phase'] for u in plan)
    return {'schema_version':1,'created_utc':datetime.now(timezone.utc).isoformat(),
        'status':'FAIL' if errors else 'PASS','planned_units':len(plan),'terminal_units':len(terminals),
        'formal_units':sum(n for phase,n in phases.items() if phase!='public_calibration'),
        'public_units':phases['public_calibration'],'phase_units':dict(phases),
        'real_provider_attempts':len(calls),'known_reported_total_tokens_lower_bound':known_tokens,
        'attempts_without_reported_total_tokens':sum((r.get('usage_raw') or {}).get('total_tokens') is None for r in calls),
        'native_runtime_helper_pipe_initializations':{str(pid):len(pipes) for pid,pipes in helper_pipes.items()},
        'native_fd_global_scope':'union of new pipe targets across all native phases/repeats per actual runtime PID; same fixed bound5 as frozen auditor; not proof of absolute zero FDs or complete leak freedom',
        'raw_input_sha256_before':raw_initial_sha256,'raw_input_sha256_after':raw_final_sha256,
        'freeze_manifest_sha256':freeze_manifest_initial_sha256,
        'measurement_errors':errors,'warnings':warnings,'joined_table_rows':file_counts,
        'frozen_file_count':len(freeze['files']),'source_sha256':observed_source,
        'source_files':{name:{'rows':sum(1 for _ in rows(raw/f'{name}.jsonl')),'sha256':file_sha(raw/f'{name}.jsonl')}
            for name in ('task_unit_starts','task_runs','provider_attempt_starts','provider_calls')},
        'limits':['coverage/provenance/request-record reconciliation only; separate native/external semantic measurement audits remain mandatory',
            'native actual HTTP body is recorded by encoded byte length/hash; saved message content and declared parameters are separate records, not retained native encoded body bytes',
            'failed request usage and currency billing may remain unknown; known usage lower bounds never imply zero unknown cost',
            'provider queuing and pure network RTT are not independently observable; client SDK elapsed is a combined duration',
            'complete terminal coverage includes model and execution failures; it is not a quality pass or a completed-paper certificate']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--driver-root',type=Path,default=Path('/drivers'))
    parser.add_argument('--source-root',type=Path,default=Path('/opt/lingxi/formal-checkout'))
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:report=audit(args.out,args.driver_root,args.source_root)
    except Exception as error:
        report={'status':'FAIL','created_utc':datetime.now(timezone.utc).isoformat(),
            'measurement_errors':[{'code':'AUDIT_INPUT_SCHEMA_OR_EXECUTION_ERROR','error_type':type(error).__name__,
                'error_text_sha256':hashlib.sha256(str(error).encode()).hexdigest()}],
            'scope':'audit failed closed; raw evidence was not modified and no model calls were made'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({key:report.get(key) for key in ('status','planned_units','terminal_units','real_provider_attempts')}))
    raise SystemExit(report['status']!='PASS')


if __name__=='__main__':main()
