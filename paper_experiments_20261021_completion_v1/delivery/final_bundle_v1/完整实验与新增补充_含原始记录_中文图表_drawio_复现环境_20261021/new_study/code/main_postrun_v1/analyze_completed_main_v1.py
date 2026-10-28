"""Postrun-only acceptance and unchanged-v3 analysis of the separate new main.

Prepared while collection runs. Never invoke against a running batch. This
module reads only the new batch, never edits raw, never imports a collection
driver, and never calls a provider or estimates an unobserved token value.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

FROZEN_SHA = '7e201b28e8a85e712cdace33951e900ad47e6c8dcd8f4554763cccd69870e373'
V3_SHA = '43fffd1665f8e0266f1de04b25e22ba336d3c97919796c29ab07beb4d57811bc'
ARMS = ('T0', 'S0', 'SS0', 'T1', 'S1', 'SS1')
CORE = ('prompt_tokens', 'completion_tokens', 'total_tokens')
COSTS = ('provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens')
IDENTITY = ('study_id', 'run_id', 'task_id', 'arm', 'repeat', 'model', 'topology', 'phase')
RAW = ('task_runs.jsonl', 'task_unit_starts.jsonl', 'provider_attempt_starts.jsonl',
       'provider_calls.jsonl', 'provider_accounting.jsonl', 'model_io.jsonl',
       'http_request_preflight.jsonl')
FIGURE04 = ('phase,model,model_id,topology,dimension,level,population_facet,arm,'
            'planned_units,terminal_units,missing_units,failed_terminal_units,unique_tasks,'
            'chain_count,metric,value,ci95_low,ci95_high,p50,p95,p99,known_units,unknown_units,'
            'applicable_planned_units,status,source_pointer,inference_scope,kind,category,'
            'value_sec,zero_scope').split(',')


class Rejected(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Rejected(code)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def load_identity_bound_v3(directory, identity):
    """Reuse frozen d87 loading rule; only registered population identities vary.

    The two source files and every numerical function remain unchanged. This
    imports a private wrapper namespace, never edits frozen module files, and
    restores any previous public formal_statistics module after importing v3.
    """
    def module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value
    frozen = module('_completion_frozen_statistics', directory / 'formal_statistics.py')
    previous = sys.modules.get('formal_statistics')
    sys.modules['formal_statistics'] = frozen
    try:
        wrapper = module('_completion_v3', directory / 'formal_statistics_protocol_aligned_v3.py')
    finally:
        if previous is None:
            sys.modules.pop('formal_statistics', None)
        else:
            sys.modules['formal_statistics'] = previous
    wrapper.FORMAL_TASK_IDS_SHA256 = identity['formal_task_ids_sha256']
    wrapper.APPLICABLE_TASK_IDS_SHA256 = identity['applicable_task_ids_sha256']
    return wrapper


def save(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')


def jsonl(path, rows):
    with path.open('x', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')


def csv_rows(path, rows, columns=None):
    columns = columns or sorted({key for row in rows for key in row})
    with path.open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns); writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def observed_int(value):
    return type(value) is int and value >= 0


def observed_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def exact_identity(row, plan):
    need(all(key in row and row[key] == plan[key] for key in IDENTITY), 'RAW_PLAN_IDENTITY_MISMATCH')
    if 'unit_id' in row:
        need(row['unit_id'] == plan['unit_id'], 'UNIT_ID_MISMATCH')
    for key in ('chain_id', 'domain'):
        if key in plan:
            need(row.get(key) == plan[key], 'RAW_PLAN_CHAIN_OR_DOMAIN_MISMATCH')


def keyed(rows, field, code):
    index = {}
    for row in rows:
        need(isinstance(row.get(field), str) and row[field] not in index, code)
        index[row[field]] = row
    return index


def read_input(path, bindings, *, lines=False, optional=False):
    item = bindings[str(path)]
    if item['exists'] is False:
        need(optional and not path.exists(), 'REQUIRED_INPUT_ABSENT_OR_CHANGED')
        return []
    need(path.is_file() and path.stat().st_size == item['bytes'], 'INPUT_SIZE_CHANGED')
    h = hashlib.sha256(); rows = []
    with path.open('rb') as f:
        if lines:
            for line_no, line in enumerate(f, 1):
                h.update(line)
                row = json.loads(line)
                need(isinstance(row, dict), 'JSONL_ROW_NOT_OBJECT')
                rows.append((row, line_no, hashlib.sha256(line).hexdigest()))
        else:
            body = f.read(); h.update(body); rows = json.loads(body)
    need(h.hexdigest() == item['sha256'], 'INPUT_SHA_CHANGED_DURING_SINGLE_READ')
    return rows


def main_only(records, main_plan, all_plan):
    result = []
    for row, line, line_sha in records:
        if row.get('phase') != 'main':
            continue
        need(row.get('run_id') in main_plan, 'UNPLANNED_MAIN_RAW_ROW')
        exact_identity(row, main_plan[row['run_id']])
        # The cloned real driver sets this literal before writing any main row.
        if 'model_evidence' in row:
            need(row['model_evidence'] == 'real_provider', 'MOCK_OR_FIXTURE_EXCLUDED_FROM_SCIENCE')
        result.append((row, line, line_sha))
    return result


def safe_native_scalars(row):
    fields = IDENTITY + ('unit_id', 'chain_id', 'domain', 'difficulty', 'model_id',
        'source_sha256', 'task_sha256', 'effective_contract_sha256', 'seed', 'status',
        'success', 'quality', 'model_evidence', 'state_applicable', 'state_size_class',
        'state_exchange_enabled', 'state_consumed', 'provider_attempts', 'provider_wait_sec',
        'wall_sec', 'observable_ipc_bytes', 'local_state_materialized_bytes', 'logical_state_bytes',
        'communication_tokens_proxy', 'runtime_cpu_sec', 'sampled_peak_process_group_rss',
        'sampled_peak_process_group_fds', 'span_partition_error_sec', 'provider_cached_input_tokens')
    result = {key: deepcopy(row[key]) for key in fields if key in row}
    if isinstance(row.get('exclusive_spans_sec'), dict):
        result['exclusive_spans_sec'] = deepcopy(row['exclusive_spans_sec'])
    if isinstance(row.get('error'), dict):
        # Keep type provenance only, never exception messages or model answers.
        result['error_type'] = row['error'].get('type')
    return result


def validate_attempts(streams, plan, terminal, usage_valid, safe_id):
    starts = keyed([r for r, _, _ in streams['provider_attempt_starts.jsonl']], 'request_id', 'DUPLICATE_ATTEMPT_START')
    calls = keyed([r for r, _, _ in streams['provider_calls.jsonl']], 'request_id', 'DUPLICATE_PROVIDER_CALL')
    accounting = keyed([r for r, _, _ in streams['provider_accounting.jsonl']], 'client_request_id', 'DUPLICATE_ACCOUNTING_ROW')
    need(set(starts) == set(calls) == set(accounting), 'ATTEMPT_START_CALL_ACCOUNTING_JOIN_NOT_BIJECTIVE')
    io = defaultdict(lambda: defaultdict(list)); preflight = defaultdict(list)
    for row, line, line_sha in streams['model_io.jsonl']:
        need(row.get('request_id') in calls, 'MODEL_IO_WITHOUT_ATTEMPT')
        direction = row.get('direction')
        need(direction in ('request', 'response'), 'UNEXPECTED_NATIVE_MODEL_IO_DIRECTION')
        # Request/answer content is neither copied nor printed.
        io[row['request_id']][direction].append((row, line, line_sha))
    for row, line, line_sha in streams['http_request_preflight.jsonl']:
        need(row.get('request_id') in calls, 'PREFLIGHT_WITHOUT_ATTEMPT')
        preflight[row['request_id']].append((row, line, line_sha))
    by_run = defaultdict(list); checks = []; oneapi_seen = {}; optional_ids = Counter()
    for request_id, call in calls.items():
        start, account = starts[request_id], accounting[request_id]
        rid = call['run_id']; need(rid in plan, 'ATTEMPT_FOR_NONMAIN_TASK')
        need(start['run_id'] == account['run_id'] == rid, 'ATTEMPT_RUN_ID_JOIN_MISMATCH')
        need(observed_int(call.get('attempt')) and call['attempt'] >= 1 and start.get('attempt') == call['attempt'], 'ATTEMPT_ORDINAL_INVALID')
        need(request_id == rid + '-' + str(call['attempt']), 'CLIENT_ATTEMPT_ID_NOT_SOURCE_FORMAT')
        safe_id(request_id)
        need(account.get('requested_model') == start.get('requested_model') == call.get('requested_model'), 'REQUESTED_MODEL_MISMATCH')
        need(call.get('requested_model') == terminal[rid]['model_id'], 'TASK_MODEL_ID_MISMATCH')
        need(len(io[request_id]['request']) == 1 and len(io[request_id]['response']) <= 1 and len(preflight[request_id]) <= 1,
             'MODEL_IO_OR_PREFLIGHT_CARDINALITY')
        dispatch, responses = account.get('http_dispatch_count'), account.get('http_response_count')
        need(observed_int(dispatch) and observed_int(responses) and dispatch <= 1 and responses <= 1, 'NO_RETRY_HTTP_ACCOUNTING_COUNTS')
        headers = account.get('response_ID_headers')
        need(isinstance(headers, dict), 'SAFE_HEADER_MAP_INVALID')
        for name, values in headers.items():
            compact = re.sub('[^a-z]', '', name.lower())
            need(('requestid' in compact or 'traceid' in compact)
                 and not any(s in compact for s in ('authorization','cookie','key','token','secret','credential','session'))
                 and isinstance(values, list), 'NON_ID_HEADER_REJECTED')
            for value in values:
                safe_id(value)
        oneapi_values = {v for v in headers.get('x-oneapi-request-id', []) if v is not None}
        optional_ids['PRESENT' if oneapi_values else 'NOT_PRESENT'] += 1
        for value in oneapi_values:
            need(value not in oneapi_seen or oneapi_seen[value] == request_id, 'REUSED_OBSERVED_X_ONEAPI_ID')
            oneapi_seen[value] = request_id
        if dispatch:
            need(account.get('actual_X_Client_Request_Id') == request_id, 'ACTUAL_CLIENT_HEADER_MISMATCH')
        raw = account.get('usage_raw'); sdk = account.get('SDK_usage_model_dump')
        body_rows = io[request_id]['response']; body = body_rows[0][0].get('response') if body_rows else None
        if body is not None:
            need(isinstance(body, dict), 'SDK_RESPONSE_NOT_OBJECT')
            need(account.get('ChatCompletion_id') == body.get('id'), 'BODY_COMPLETION_ID_MISMATCH')
            if 'response_sha256' in call:
                need(call['response_sha256'] == canonical(body), 'SDK_RESPONSE_HASH_MISMATCH')
        raw_call_usage = call.get('usage_raw')
        known = False; core = None; origin = 'UNKNOWN_NO_ZERO_FILL'
        zero_dispatch = dispatch == responses == 0 and not body_rows and not preflight[request_id]
        if zero_dispatch:
            need(raw is None and sdk is None and raw_call_usage is None, 'ZERO_DISPATCH_CONTRADICTS_USAGE_OBJECT')
            core = [0, 0, 0]; known = True; origin = 'PROVEN_NO_HTTP_DISPATCH_NOT_PROVIDER_REPORTED_ZERO'
        elif usage_valid(raw):
            need(account.get('usage_raw_present') is True, 'HTTP_USAGE_PRESENCE_FLAG_CONTRADICTION')
            if account.get('SDK_usage_present') is True or body_rows or call.get('status') == 'OK':
                need(account.get('SDK_usage_present') is True, 'SDK_USAGE_PRESENCE_FLAG_CONTRADICTION')
                need(usage_valid(sdk) and isinstance(body, dict) and usage_valid(body.get('usage')), 'MISSING_INVALID_SDK_USAGE')
                need(all(raw[k] == sdk[k] == body['usage'][k] for k in CORE), 'HTTP_SDK_MODEL_IO_USAGE_MISMATCH')
                need(dispatch == responses == 1 and account.get('http_status') == 200, 'SUCCESS_HTTP_CARDINALITY_OR_STATUS')
                need(len(preflight[request_id]) == 1, 'SUCCESS_PREFLIGHT_MISSING')
                if account.get('SDK_request_id') is not None:
                    safe_id(account['SDK_request_id'])
                if 'provider_request_id' in call:
                    need(call['provider_request_id'] == account.get('SDK_request_id'), 'SDK_REQUEST_ATTRIBUTE_MISMATCH')
                origin = 'EXACT_HTTP_SDK_MODEL_IO_ACCOUNTING'
            else:
                # An actual HTTP error may supply valid usage without any SDK
                # ChatCompletion object. Preserve that source rather than invent one.
                need(responses == 1 and account.get('transport_outcome') == 'HTTP_RESPONSE_OBSERVED', 'ERROR_USAGE_WITHOUT_HTTP_RESPONSE')
                origin = 'OBSERVED_HTTP_ERROR_USAGE_NO_CHATCOMPLETION'
            if raw_call_usage is not None:
                need(usage_valid(raw_call_usage) and all(raw_call_usage[k] == raw[k] for k in CORE), 'RAW_CALL_USAGE_MISMATCH')
            elif call.get('status') == 'OK':
                raise Rejected('OK_CALL_MISSING_ITS_USAGE_OBJECT')
            need(account.get('usage_complete') is True, 'ACCOUNTING_COMPLETENESS_FLAG_CONTRADICTION')
            core = [raw[k] for k in CORE]; known = True
        elif raw is not None:
            raise Rejected('INVALID_HTTP_USAGE_CORE_NO_COERCION')
        if call.get('status') == 'OK':
            need(known and origin == 'EXACT_HTTP_SDK_MODEL_IO_ACCOUNTING', 'SUCCESS_USAGE_NOT_COMPLETELY_VERIFIED')
        audit_row = {'run_id': rid, 'request_id': request_id, 'attempt': call['attempt'],
                     'raw_call_status': call.get('status'), 'http_dispatch_count': dispatch,
                     'http_response_count': responses, 'cost_known': known, 'cost_origin': origin,
                     'provider_input_tokens': core[0] if known else None,
                     'provider_output_tokens': core[1] if known else None,
                     'provider_total_tokens': core[2] if known else None,
                     'SDK_response_rows': len(body_rows), 'observed_x_oneapi_present': bool(oneapi_values)}
        checks.append(audit_row); by_run[rid].append(audit_row)
    for rid, attempts in by_run.items():
        need(sorted(r['attempt'] for r in attempts) == list(range(1, len(attempts) + 1)), 'ATTEMPT_SEQUENCE_GAP')
    return by_run, checks, {'observed_x_oneapi_unique_values': len(oneapi_seen),
        'x_oneapi_attempt_presence': dict(optional_ids), 'missing_header_is_not_a_usage_gate': True}


def timing_rows(runs, np):
    rows = []
    # A recorded category has one row per arm, including arms to which no
    # mass of that category was assigned by the complete partition ledger.
    categories = sorted({key for row in runs if isinstance(row.get('exclusive_spans_sec'),dict)
                         for key in row['exclusive_spans_sec']})
    for arm in ARMS:
        values = [r for r in runs if r['arm'] == arm]; n = len(values)
        base = {'phase':'main','model':values[0]['model'],'model_id':values[0]['model_id'],
                'topology':values[0]['topology'],'dimension':'overall','level':'all',
                'population_facet':'all_formal','arm':arm,'planned_units':360,'terminal_units':n,
                'missing_units':360-n,'failed_terminal_units':sum(not r['success'] for r in values),
                'unique_tasks':120,'chain_count':20,'applicable_planned_units':360,
                'ci95_low':None,'ci95_high':None,'status':'DESCRIPTIVE_NATIVE_FIELDS_NO_NEW_CI',
                'source_pointer':'normalized_task_runs.jsonl; new main only',
                'inference_scope':'descriptive all-planned measurements; no second Holm family'}
        metric_values = {
            'wall_sec':[r.get('wall_sec') for r in values],
            'provider_wait_fraction':[(r['provider_wait_sec']/r['wall_sec']) if observed_number(r.get('provider_wait_sec'))
                and observed_number(r.get('wall_sec')) and r['wall_sec']>0 else None for r in values],
            'recorded_exclusive_partition_mass':[sum(r['exclusive_spans_sec'].values()) if isinstance(r.get('exclusive_spans_sec'),dict) else None for r in values]}
        for metric, raw_values in metric_values.items():
            known = [v for v in raw_values if observed_number(v)]
            rows.append({**base,'metric':metric,'value':float(np.mean(known)) if len(known)==n else None,
                         'p50':float(np.quantile(known,.50)) if len(known)==n else None,
                         'p95':float(np.quantile(known,.95)) if len(known)==n else None,
                         'p99':float(np.quantile(known,.99)) if len(known)==n else None,
                         'known_units':len(known),'unknown_units':n-len(known),
                         'zero_scope':'undefined fraction/missing partition remains null, never zero'})
        partitions = [r.get('exclusive_spans_sec') for r in values]
        if all(isinstance(p,dict) for p in partitions):
            for category in categories:
                assigned = sum(p.get(category,0.0) for p in partitions)/n
                rows.append({**base,'metric':'recorded_exclusive_partition_mass','kind':'recorded_exclusive_partition_mass',
                             'category':category,'value':assigned,'value_sec':assigned,'known_units':n,'unknown_units':0,
                             'zero_scope':'absent key assigns zero RECORDED partition mass only; not zero operation latency or absence outside instrumentation'})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--manifest',required=True,type=Path)
    ap.add_argument('--out',required=True,type=Path); args=ap.parse_args()
    need(not args.out.exists() and args.out.parent.is_dir(),'FRESH_ANALYSIS_DIRECTORY_REQUIRED')
    manifest=json.loads(args.manifest.read_text(encoding='utf-8'))
    need(manifest['postrun_execution_explicitly_confirmed'] is True,'COLLECTION_STILL_RUNNING_NO_ANALYSIS')
    bindings=manifest['files']; study=Path('/study'); native=Path('/native')
    need(digest(Path(__file__))==manifest['analysis_source_sha256'],'ANALYSIS_SOURCE_PIN')
    need(digest(Path('/run-control/completion_receipt.json'))==manifest['completion_receipt_sha256'],
         'TERMINAL_RECEIPT_IDENTITY_CHANGED')
    identity_path=Path('/control/identity_binding.json')
    need(digest(identity_path)==manifest['identity_binding_sha256'],'PREOUTCOME_IDENTITY_BINDING_CHANGED')
    identity=json.loads(identity_path.read_text(encoding='utf-8'))
    parsed_paths={str(study/'raw'/name) for name in RAW}|{
        str(study/'manifests/formal_plan.json'),str(study/'corpus/tasks.jsonl')}
    for name,item in bindings.items():
        if name not in parsed_paths:
            path=Path(name)
            need(item['exists'] and path.is_file() and path.stat().st_size==item['bytes']
                 and digest(path)==item['sha256'],'SMALL_METADATA_OR_METHOD_INPUT_CHANGED')
    need(digest(native/'formal_statistics.py')==FROZEN_SHA and digest(native/'formal_statistics_protocol_aligned_v3.py')==V3_SHA,'FROZEN_ANALYZER_SHA')
    need(digest(native/'provider_accounting.py')==manifest['accounting_source_sha256'],'ACCOUNTING_HELPER_SHA')
    plan=read_input(study/'manifests/formal_plan.json',bindings)
    all_plan=keyed(plan,'run_id','DUPLICATE_PLANNED_RUN_ID')
    main_list=[p for p in plan if p['phase']=='main']; planned=keyed(main_list,'run_id','DUPLICATE_MAIN_RUN_ID')
    ids={p['task_id'] for p in main_list}; chains={p['chain_id'] for p in main_list}
    expected={(t,a,r) for t in ids for a in ARMS for r in (0,1,2)}
    need(len(main_list)==2160 and len(ids)==120 and len(chains)==20 and
         Counter((p['task_id'],p['arm'],p['repeat']) for p in main_list)==Counter({k:1 for k in expected}), 'MAIN_PLAN_NOT_120x6x3_20CHAINS')
    corpus=read_input(study/'corpus/tasks.jsonl',bindings,lines=True)
    tasks=keyed([r for r,_,_ in corpus],'task_id','DUPLICATE_CORPUS_TASK')
    applicable={t for t in ids if tasks[t]['state_spec']['applicable'] is True};need(len(applicable)==108,'FROZEN_APPLICABILITY_NOT_108')
    need(canonical(sorted(ids))==identity['formal_task_ids_sha256'] and
         canonical(sorted(applicable))==identity['applicable_task_ids_sha256'] and
         identity['seed']==20261020,'REGISTERED_POPULATION_OR_SEED_CHANGED')
    need(bindings[str(study/'manifests/formal_plan.json')]['sha256']==identity['formal_plan_sha256'] and
         bindings[str(study/'corpus/tasks.jsonl')]['sha256']==identity['corpus_sha256'],
         'PREOUTCOME_PLAN_OR_CORPUS_CHANGED')
    streams={}; global_accounting=[]
    for name in RAW:
        records=read_input(study/'raw'/name,bindings,lines=True,optional=name not in ('task_runs.jsonl','task_unit_starts.jsonl'))
        if name=='provider_accounting.jsonl':global_accounting=records
        streams[name]=main_only(records,planned,all_plan)
    raw_runs=keyed([r for r,_,_ in streams['task_runs.jsonl']],'run_id','DUPLICATE_MAIN_TERMINAL')
    starts=keyed([r for r,_,_ in streams['task_unit_starts.jsonl']],'run_id','DUPLICATE_TASK_UNIT_START')
    need(len(raw_runs)==2160 and set(raw_runs)==set(starts)==set(planned),'2160_TERMINALS_AND_TASK_STARTS_NOT_BIJECTIVE')
    for rid,row in raw_runs.items():
        need(type(row.get('success')) is bool and isinstance(row.get('status'),str) and row['status'], 'QUALITY_OR_TERMINAL_TYPE_INVALID')
        need(row.get('model_evidence')=='real_provider','FIXTURE_TASK_NOT_REAL_MAIN')
    sys.path.insert(0,str(native))
    from provider_accounting import usage_valid,safe_id
    by_run,attempt_checks,id_checks=validate_attempts(streams,planned,raw_runs,usage_valid,safe_id)
    # Presence remains optional. Check every already-observed safe oneapi value
    # in this raw capture, including the six separately excluded public units.
    global_oneapi={};global_presence=Counter()
    for row,_,_ in global_accounting:
        headers=row.get('response_ID_headers',{});need(isinstance(headers,dict),'GLOBAL_SAFE_ID_MAP_INVALID')
        values=headers.get('x-oneapi-request-id',[]);need(isinstance(values,list),'GLOBAL_ONEAPI_LIST_INVALID')
        values={v for v in values if v is not None};global_presence['PRESENT' if values else 'NOT_PRESENT']+=1
        for value in values:
            safe_id(value);client=row.get('client_request_id');safe_id(client)
            need(value not in global_oneapi or global_oneapi[value]==client,'GLOBAL_REUSED_OBSERVED_X_ONEAPI_ID')
            global_oneapi[value]=client
    id_checks.update(global_observed_x_oneapi_unique_values=len(global_oneapi),
                     global_capture_header_presence=dict(global_presence))
    normalized=[]; changes=[]; unknown=[]
    locators={r['run_id']:{'raw_line':line,'raw_line_sha256':s} for r,line,s in streams['task_runs.jsonl']}
    for rid,raw in raw_runs.items():
        calls=by_run.get(rid,[]);need(observed_int(raw.get('provider_attempts')) and raw['provider_attempts']==len(calls),'TASK_ATTEMPT_COUNT_MISMATCH')
        # No-call zero is supported only by terminal source count plus absence
        # from every joined dispatch/model-I/O/preflight stream in this harness.
        complete=all(c['cost_known'] for c in calls)
        costs=[sum(c[k] for c in calls) for k in COSTS] if complete else [None,None,None]
        norm=safe_native_scalars(raw);norm.update(locators[rid])
        proof='EXACT_JOINED_ATTEMPT_SUM' if calls else 'SOURCE_PROVIDER_ATTEMPTS_ZERO_AND_ALL_DISPATCH_STREAMS_EMPTY'
        for key,value in zip(COSTS,costs):
            prior=raw.get(key)
            if prior is not None:
                need(observed_int(prior),'RAW_TASK_TOKEN_SCALAR_INVALID')
                if value is not None:need(prior==value,'RAW_TASK_TOKEN_SCALAR_DISAGREES_WITH_ACTUAL_CALLS')
            norm[key]=value
            if value is not None and prior is None:
                changes.append({'run_id':rid,'field':key,'change_type':'EXACT_AGGREGATION_RECOVERED',
                    'raw_field_present':key in raw,'raw_value':None,'derived_value':value,'evidence':proof})
            elif value is None and prior is not None:
                changes.append({'run_id':rid,'field':key,'change_type':'UNCONFIRMED_SCALAR_NOT_USED',
                    'raw_field_present':True,'raw_value':prior,'derived_value':None,
                    'evidence':'At least one actual attempt cost is unknown; stop before analysis.'})
        if raw.get('provider_usage_complete') is True:need(complete,'RAW_TASK_COMPLETE_FLAG_CONTRADICTS_USAGE')
        if complete and raw.get('reported_token_lower_bound') is not None:
            need(observed_int(raw['reported_token_lower_bound']) and raw['reported_token_lower_bound']==costs[2],
                 'COMPLETE_TASK_REPORTED_TOKEN_SUM_MISMATCH')
        norm['provider_usage_complete']=complete;norm['derived_usage_evidence']=proof if complete else 'UNKNOWN_RETAINED_NO_ZERO_SUBSTITUTION'
        norm['derived_cost_aggregation_does_not_modify_raw']=True
        if not complete:unknown.append(rid)
        parts=norm.get('exclusive_spans_sec')
        if parts is not None:
            need(isinstance(parts,dict)and all(isinstance(k,str)and observed_number(v) for k,v in parts.items()),'RECORDED_PARTITION_INVALID')
        normalized.append(norm)
    args.out.mkdir()
    jsonl(args.out/'normalized_task_runs.jsonl',normalized);jsonl(args.out/'attempt_acceptance.jsonl',attempt_checks)
    save(args.out/'derived_aggregation_changes.json',{'scope':'Independent derived aggregation only; no raw mutation/zero fill','changes':changes})
    acceptance={'status':'MAIN2160_EXACT_JOIN_USAGE_COMPLETE' if not unknown else 'INCOMPLETE_USAGE_STOP_BEFORE_ANALYZE',
        'planned_main_units':2160,'terminal_main_units':len(raw_runs),'formal_unique_tasks':120,'chains':20,'state_applicable':108,
        'public_and_other_phases_excluded':True,'mock_fixture_excluded':True,'all_business_failures_retained':True,
        'quality_successes':sum(r['success'] for r in normalized),'quality_failures':sum(not r['success'] for r in normalized),
        'provider_attempt_rows':len(attempt_checks),'unknown_task_usage':len(unknown),
        'derived_scalar_fields_restored_from_exact_evidence':sum(c['change_type']=='EXACT_AGGREGATION_RECOVERED' for c in changes),
        'provider_zero_only_from_proved_no_dispatch':True,**id_checks,'frozen_statistics_sha256':FROZEN_SHA,'v3_sha256':V3_SHA,
        'input_manifest_sha256':digest(args.manifest),'analysis_source_sha256':digest(Path(__file__)),
        'identity_binding_sha256':digest(identity_path),
        'scope':'This separate new main only; no old results/usage repair, new endpoint, quality rule or second Holm family.'}
    save(args.out/'acceptance.json',acceptance)
    if unknown:return 2
    # Exactly the existing API and seed. Do not rewrite/import the real driver.
    v3=load_identity_bound_v3(native,identity)
    import numpy as np
    primary=v3.analyze(normalized,main_list,applicable,seed=20261020)
    save(args.out/'formal_primary_v3.json',primary)
    timing=timing_rows(normalized,np)
    # Figure CSV has ordinary scalar cells, matching the existing figure04 schema.
    with (args.out/'figure_04.csv').open('x',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIGURE04);writer.writeheader();writer.writerows(timing)
    summaries=[]
    for arm in ARMS:
        group=[r for r in normalized if r['arm']==arm];row={'phase':'main','arm':arm,'planned_units':360,'observed_units':len(group),
            'unique_tasks':120,'chain_count':20,'successes':sum(r['success'] for r in group),'failures':sum(not r['success'] for r in group)}
        for field in COSTS+('observable_ipc_bytes','wall_sec','provider_wait_sec','local_state_materialized_bytes','logical_state_bytes','communication_tokens_proxy'):
            vals=[r.get(field) for r in group];known=[v for v in vals if observed_number(v)]
            row[field+'_known_units']=len(known);row[field+'_unknown_units']=len(group)-len(known)
            row[field+'_mean']=float(np.mean(known)) if len(known)==len(group) else None
            row[field+'_total']=sum(known) if len(known)==len(group) else None
        summaries.append(row)
    csv_rows(args.out/'observed_by_arm.csv',summaries)
    comparisons=primary['strata'][0]['comparisons']
    need(len(comparisons)==5 and primary['primary_family_test_count']==10 and primary['active_confirmatory_families']==1,'FROZEN_PRIMARY_FAMILY_BOUNDARY')
    need([c['planned_unique_tasks'] for c in comparisons]==[120,108,120,120,120],'FROZEN_PAIR_POPULATIONS')
    need(primary['quality_noninferiority_wording']=='WITHHELD_BY_PROSPECTIVE_GATE','NI_WORDING_CHANGED')
    with (args.out/'normalized_task_runs.jsonl').open(encoding='utf-8') as f:
        need([json.loads(line) for line in f]==normalized,'NORMALIZED_JSONL_READBACK')
    outputs=['normalized_task_runs.jsonl','attempt_acceptance.jsonl','derived_aggregation_changes.json','acceptance.json',
             'formal_primary_v3.json','figure_04.csv','observed_by_arm.csv']
    save(args.out/'analysis_receipt.json',{'status':'ACTUAL_NEW_MAIN_V3_ANALYSIS_COMPLETE','raw_unchanged':True,
        'exact_API':'analyze(normalized_main2160,main_plan2160,preoutcome_applicable108,seed=20261020)',
        'source_sha256':digest(Path(__file__)),'input_manifest_sha256':digest(args.manifest),
        'identity_binding_sha256':digest(identity_path),
        'identity_adapter_scope':'Existing d87 isolated loader; only registered formal/applicable ID digests rebound, unchanged 7e201/43fffd source and functions.',
        'output_sha256':{name:digest(args.out/name) for name in outputs},'new_model_API_benchmark_calls':0,
        'one_postrun_boundary_check_passed':True,'unfavorable_results_not_suppressed':True,
        'figure04_scope':'Six arms, all planned tasks; native wall mean/p50/p95/p99, per-unit wait/wall fraction, original exclusive category labels and assigned recorded mass. No extra CI/bootstrap.'})
    print(json.dumps({'status':'ACTUAL_NEW_MAIN_V3_ANALYSIS_COMPLETE','main_units':2160,'primary_family_size':10}),flush=True)
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Rejected as error:
        # Codes only; never print raw rows, request/answer/error text or headers.
        print(json.dumps({'status':'ACCEPTANCE_REJECTED_RETAINED','reason_code':str(error)}),flush=True)
        raise SystemExit(2)
