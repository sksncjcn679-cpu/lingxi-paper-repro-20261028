"""Rebuild explicit provider-usage authority after all 6,504 units are terminal.

Read-only with respect to raw evidence. No model client, API, or secret access.
Missing cache/reasoning fields never become zero and do not invalidate complete
input/output/total counters. CSV cells are typed JSON; Parquet is fixed-schema.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math

DEFAULT_OUT = Path('/evidence/formal_20261019')
EXPECTED_UNITS = 6504
INT64_MAX = 2**63 - 1
TOKEN_PATHS = {
    'provider_input_tokens': ('prompt_tokens',),
    'provider_output_tokens': ('completion_tokens',),
    'provider_total_tokens': ('total_tokens',),
    'provider_cached_input_tokens': ('prompt_tokens_details', 'cached_tokens'),
    'provider_reasoning_output_tokens': ('completion_tokens_details', 'reasoning_tokens'),
}
PRIMARY = ('provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens')
IDENTITY = {'run_id':'string','unit_id':'string','phase':'string','task_id':'string','chain_id':'string',
    'domain':'string','difficulty':'string','arm':'string','repeat':'integer','model':'string',
    'model_id':'string','topology':'string','processes':'boolean'}
FIELDS_TO_RETAIN = set(IDENTITY) | {'request_id','attempt','start_utc','end_utc','status',
    'requested_model','returned_model','endpoint','usage_raw','usage_source','wall_sec',
    'actual_prompt_sha256','response_sha256','provider_attempts','provider_usage_complete',
    'provider_wait_sec','success','model_evidence',*TOKEN_PATHS}
TERMINAL_STATUSES = {'COMPLETED','FAILED','UNSUPPORTED','NON_COMPARABLE','INCOMPLETE','INCOMPLETE_BUDGET_STOP'}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256((value if isinstance(value,bytes) else canonical(value).encode())).hexdigest()


def hash_file(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):result.update(chunk)
    return result.hexdigest()


def rows(path):
    with Path(path).open(encoding='utf-8') as stream:
        for index,line in enumerate(stream,1):
            if not line.strip():continue
            try:
                row=json.loads(line,parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
                if not isinstance(row,dict):raise ValueError('object row required')
            except (ValueError,TypeError) as error:
                raise RuntimeError(f'invalid standard JSON object: {Path(path).name}:{index}') from error
            yield index,row


def load_unique(path,key):
    result={}
    for line,row in rows(path):
        identity=row.get(key)
        if not isinstance(identity,str) or not identity or identity in result:
            raise RuntimeError(f'missing/duplicate {key}: {Path(path).name}:{line}')
        kept={name:value for name,value in row.items() if name in FIELDS_TO_RETAIN}
        result[identity]={'row':kept,'line':line,'sha256':digest(row)}
    return result


def explicit_counter(usage,path):
    current=usage
    for index,key in enumerate(path):
        if current is None:return None,'UNKNOWN','NULL_USAGE_OR_PARENT'
        if not isinstance(current,dict):return None,'INVALID','USAGE_OR_PARENT_NOT_OBJECT'
        if key not in current:return None,'UNKNOWN','MISSING_FIELD:'+'.'.join(path[:index+1])
        current=current[key]
    if current is None:return None,'UNKNOWN','EXPLICIT_NULL_FIELD'
    if isinstance(current,bool) or not isinstance(current,int) or not 0<=current<=INT64_MAX:
        return None,'INVALID','NONNEGATIVE_INT64_COUNTER_REQUIRED'
    return current,'REPORTED','EXPLICIT_NUMERIC_FIELD_IN_USAGE_RAW'


def finite_time(value):
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None


def load_sdk_spans(path):
    result=defaultdict(list)
    if not path.exists():return result
    for line,row in rows(path):
        if row.get('category')!='provider_wait' or not row.get('request_id'):continue
        begin,end=row.get('start_ns'),row.get('end_ns')
        valid_ns=all(isinstance(v,int) and not isinstance(v,bool) for v in (begin,end)) and end>=begin
        value=(end-begin)/1e9 if valid_ns else finite_time(row.get('inclusive_sec'))
        result[row['request_id']].append({'value':value,'line':line,'sha256':digest(row),
            'run_id':row.get('run_id'),
            'monotonic_bounds_available':valid_ns,'reported_inclusive_sec':row.get('inclusive_sec')})
    return result


def identity(plan,terminal):
    result={name:plan.get(name) for name in IDENTITY}
    result['model_id']=terminal.get('model_id')
    return result


def summarize(values):
    known=[value for value in values if value is not None]
    lower=sum(known)
    if lower>INT64_MAX:raise RuntimeError('aggregate token counter exceeds supported exact int64 storage')
    complete=bool(values) and len(known)==len(values)
    return (lower if complete else None,
        'COMPLETE_REPORTED' if complete else 'PARTIALLY_REPORTED' if known else 'UNKNOWN',
        lower,len(known),len(values)-len(known))


def derive_attempt(request_id,start,end,unit,terminal,sdk_spans,issues):
    source=(end or start)['row']
    usage=end['row'].get('usage_raw') if end else None
    row={**identity(unit,terminal),'request_id':request_id,'attempt':source.get('attempt'),
        'attempt_status':end['row'].get('status') if end else 'UNRESOLVED_STARTED_WITHOUT_END',
        'requested_model':source.get('requested_model'),'returned_model':source.get('returned_model'),
        'endpoint':source.get('endpoint'),'start_utc':source.get('start_utc'),'end_utc':source.get('end_utc'),
        'usage_source_raw':source.get('usage_source'),
        'usage_raw_presence':'MISSING' if not end or 'usage_raw' not in end['row'] else 'PRESENT_NULL' if usage is None else 'PRESENT_OBJECT' if isinstance(usage,dict) else 'PRESENT_OTHER',
        'usage_raw_json':canonical(usage) if end and 'usage_raw' in end['row'] else None,
        'source_call_line':end['line'] if end else None,'source_start_line':start['line'] if start else None,
        'source_call_sha256':end['sha256'] if end else None,'source_start_sha256':start['sha256'] if start else None,
        'recorded_sdk_envelope_wall_sec':finite_time(source.get('wall_sec')),
        'recorded_sdk_envelope_scope':'provider_calls.wall_sec includes its recorded SDK/response-processing/logging envelope; not queue or pure RTT',
        'sdk_wall_sec':None,'sdk_wall_status':'UNKNOWN','sdk_span_source_line':None,'sdk_span_source_sha256':None,
        'queue_latency_sec':None,'queue_latency_status':'UNKNOWN_NOT_OBSERVED',
        'pure_http_rtt_sec':None,'pure_http_rtt_status':'UNKNOWN_NOT_OBSERVED',
        'reasoning_accounting':'SUBSET_OF_COMPLETION_WHEN_EXPLICIT_NEVER_ADDED_TO_COMPLETION_OR_TOTAL'}
    if not start or not end:
        issues.append({'severity':'ERROR','code':'ATTEMPT_LEDGER_UNMATCHED','request_id':request_id,
                       'has_start':bool(start),'has_end':bool(end)})
    for field,path in TOKEN_PATHS.items():
        value,status,reason=explicit_counter(usage,path)
        row[field]=value;row[field+'_status']=status;row[field+'_reason']=reason
        if status=='INVALID':issues.append({'severity':'ERROR','code':'INVALID_EXPLICIT_COUNTER',
                                           'request_id':request_id,'field':field,'reason':reason})
    row['primary_usage_complete']=all(row[field] is not None for field in PRIMARY)
    row['primary_usage_conserved']=None
    if row['primary_usage_complete']:
        row['primary_usage_conserved']=row['provider_total_tokens']==row['provider_input_tokens']+row['provider_output_tokens']
        if not row['primary_usage_conserved']:
            issues.append({'severity':'ERROR','code':'EXPLICIT_PRIMARY_COUNTERS_NOT_CONSERVED','request_id':request_id})
    for subset,total in [('provider_cached_input_tokens','provider_input_tokens'),('provider_reasoning_output_tokens','provider_output_tokens')]:
        if row[subset] is not None and row[total] is not None and row[subset]>row[total]:
            issues.append({'severity':'ERROR','code':'EXPLICIT_SUBSET_COUNTER_EXCEEDS_PARENT','request_id':request_id,'field':subset})
    cache,prompt=row['provider_cached_input_tokens'],row['provider_input_tokens']
    row['provider_uncached_input_tokens']=prompt-cache if cache is not None and prompt is not None and cache<=prompt else None
    row['provider_uncached_input_tokens_status']='DERIVED_FROM_EXPLICIT_COUNTERS' if row['provider_uncached_input_tokens'] is not None else 'UNKNOWN'
    matches=sdk_spans.get(request_id,[])
    if len(matches)==1 and matches[0].get('run_id')!=unit['run_id']:
        issues.append({'severity':'ERROR','code':'SDK_SPAN_TASK_ID_MISMATCH','request_id':request_id})
    elif len(matches)==1 and matches[0]['value'] is not None:
        span=matches[0];row.update(sdk_wall_sec=span['value'],sdk_wall_status='RECORDED_PROVIDER_WAIT_SPAN',
            sdk_span_source_line=span['line'],sdk_span_source_sha256=span['sha256'])
        inclusive=finite_time(span['reported_inclusive_sec'])
        if inclusive is not None and abs(inclusive-span['value'])>1e-6:
            issues.append({'severity':'ERROR','code':'SDK_MONOTONIC_SPAN_DISAGREES','request_id':request_id})
    elif len(matches)>1:issues.append({'severity':'ERROR','code':'DUPLICATE_SDK_PROVIDER_WAIT_SPAN','request_id':request_id})
    return row


def derive_task(unit,terminal_record,attempts,issues):
    raw=terminal_record['row']
    row={**identity(unit,raw),'terminal_status':raw.get('status'),'task_success':raw.get('success'),
        'attempt_count':len(attempts),'failed_or_unresolved_attempt_count':sum(a['attempt_status']!='OK' for a in attempts),
        'terminal_provider_attempts_raw':raw.get('provider_attempts'),
        'source_task_line':terminal_record['line'],'source_task_sha256':terminal_record['sha256'],
        'request_ids_json':canonical([a['request_id'] for a in attempts]),
        'queue_latency_sec':None,'queue_latency_status':'UNKNOWN_NOT_OBSERVED',
        'pure_http_rtt_sec':None,'pure_http_rtt_status':'UNKNOWN_NOT_OBSERVED',
        'reasoning_accounting':'REPORTED_SUBSET_ONLY_NEVER_ADDED_TO_COMPLETION_OR_TOTAL'}
    if raw.get('provider_attempts')!=len(attempts):
        issues.append({'severity':'ERROR','code':'TERMINAL_ATTEMPT_COUNT_DISAGREES','run_id':unit['run_id']})
    for field in TOKEN_PATHS:
        value,status,lower,n_known,n_unknown=summarize([a[field] for a in attempts])
        row.update({field:value,field+'_status':status,field+'_reported_lower_bound':lower,
                    field+'_reported_attempts':n_known,field+'_unknown_attempts':n_unknown})
    row['primary_usage_complete']=all(row[field] is not None for field in PRIMARY)
    row['cache_usage_complete']=row['provider_cached_input_tokens'] is not None
    row['reasoning_usage_complete']=row['provider_reasoning_output_tokens'] is not None
    row['no_provider_attempt_observed']=not attempts
    row['primary_usage_conserved']=all(a['primary_usage_conserved'] is True for a in attempts) if row['primary_usage_complete'] else None
    uncached,status,lower,n_known,n_unknown=summarize([a['provider_uncached_input_tokens'] for a in attempts])
    row.update(provider_uncached_input_tokens=uncached,provider_uncached_input_tokens_status=status,
        provider_uncached_input_tokens_reported_lower_bound=lower)
    for source,target in [('sdk_wall_sec','sdk_wall_sec'),('recorded_sdk_envelope_wall_sec','recorded_sdk_envelope_wall_sec')]:
        values=[a[source] for a in attempts];known=[v for v in values if v is not None]
        complete=bool(values) and len(known)==len(values)
        row[target]=float(sum(known)) if complete else None
        row[target+'_status']='COMPLETE_RECORDED' if complete else 'PARTIALLY_RECORDED' if known else 'UNKNOWN'
        row[target+'_recorded_lower_bound_sec']=float(sum(known))
    original={field:{'present':field in raw,'value':raw.get(field)} for field in [*TOKEN_PATHS,'provider_usage_complete','provider_wait_sec']}
    differences=[]
    for field in TOKEN_PATHS:
        if field not in raw or canonical(raw.get(field))!=canonical(row[field]):
            reason=('RAW_CACHE_DEFAULT_ZERO_WITHOUT_EXPLICIT_ATTEMPT_CACHE_EVIDENCE'
                if field=='provider_cached_input_tokens' and type(raw.get(field)) is int and raw.get(field)==0 and row[field] is None
                else 'RAW_TASK_AGGREGATE_DIFFERS_FROM_FIELDWISE_EXPLICIT_ATTEMPT_AUTHORITY')
            differences.append({'field':field,'raw_present':field in raw,'raw_value':raw.get(field),
                                'authority_value':row[field],'authority_status':row[field+'_status'],'reason':reason})
    row['raw_task_token_values_json']=canonical(original)
    row['token_provenance_differences_json']=canonical(differences)
    row['raw_task_token_difference_count']=len(differences)
    row['raw_task_cached_zero_not_authoritative']=any(d['reason']=='RAW_CACHE_DEFAULT_ZERO_WITHOUT_EXPLICIT_ATTEMPT_CACHE_EVIDENCE' for d in differences)
    return row


def specs(kind):
    fields={name:{'type':typ,'nullable':name=='model_id'} for name,typ in IDENTITY.items()}
    def add(names,typ,nullable=False):
        for name in names.split():fields[name]={'type':typ,'nullable':nullable}
    add('primary_usage_complete','boolean')
    add('primary_usage_conserved','boolean',True)
    add('reasoning_accounting queue_latency_status pure_http_rtt_status','string')
    add('queue_latency_sec pure_http_rtt_sec','number',True)
    if kind=='attempt':
        add('request_id attempt_status usage_raw_presence sdk_wall_status recorded_sdk_envelope_scope','string')
        add('attempt source_call_line source_start_line sdk_span_source_line','integer',True)
        add('requested_model returned_model endpoint start_utc end_utc usage_source_raw usage_raw_json source_call_sha256 source_start_sha256 sdk_span_source_sha256','string',True)
        add('sdk_wall_sec recorded_sdk_envelope_wall_sec','number',True)
        for field in TOKEN_PATHS:
            add(field,'integer',True);add(field+'_status '+field+'_reason','string')
        add('provider_uncached_input_tokens','integer',True)
        add('provider_uncached_input_tokens_status','string')
    else:
        add('terminal_status request_ids_json raw_task_token_values_json token_provenance_differences_json sdk_wall_sec_status recorded_sdk_envelope_wall_sec_status provider_uncached_input_tokens_status','string')
        add('task_success cache_usage_complete reasoning_usage_complete no_provider_attempt_observed raw_task_cached_zero_not_authoritative','boolean')
        add('attempt_count failed_or_unresolved_attempt_count source_task_line raw_task_token_difference_count','integer')
        add('terminal_provider_attempts_raw','integer',True)
        add('source_task_sha256','string')
        for field in TOKEN_PATHS:
            add(field,'integer',True);add(field+'_status','string')
            add(field+'_reported_lower_bound '+field+'_reported_attempts '+field+'_unknown_attempts','integer')
        add('provider_uncached_input_tokens','integer',True)
        add('provider_uncached_input_tokens_reported_lower_bound','integer')
        add('sdk_wall_sec recorded_sdk_envelope_wall_sec','number',True)
        add('sdk_wall_sec_recorded_lower_bound_sec recorded_sdk_envelope_wall_sec_recorded_lower_bound_sec','number')
    return fields


def json_schema(kind,fields):
    properties={}
    for name,spec in fields.items():
        value={'type':[spec['type'],'null'] if spec['nullable'] else spec['type']}
        if spec['type']=='integer':value.update(minimum=0,maximum=INT64_MAX)
        if spec['type']=='number':value['minimum']=0
        if name.endswith('_json'):value.update(contentMediaType='application/json',description='Canonical JSON stored in this string; null means no source field where specified.')
        properties[name]=value
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','title':'provider_usage_authority_'+kind,
        'type':'object','properties':properties,'required':list(fields),'additionalProperties':False}


def validate_row(row,fields):
    if set(row)!=set(fields):raise RuntimeError('derived row field set differs from declared schema')
    for name,spec in fields.items():
        value=row[name]
        if value is None:
            if not spec['nullable']:raise RuntimeError('nonnullable derived field missing: '+name)
            continue
        typ=spec['type']
        valid=(isinstance(value,str) if typ=='string' else isinstance(value,bool) if typ=='boolean' else
            isinstance(value,int) and not isinstance(value,bool) and 0<=value<=INT64_MAX if typ=='integer' else
            isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0)
        if not valid:raise RuntimeError('derived scalar does not match schema: '+name)


def write_table(destination,name,table_rows,kind):
    import pyarrow as pa
    import pyarrow.parquet as pq
    fields=specs(kind);columns=list(fields)
    dtypes={'string':pa.string(),'boolean':pa.bool_(),'integer':pa.int64(),'number':pa.float64()}
    schema=pa.schema([pa.field(name,dtypes[spec['type']],nullable=spec['nullable']) for name,spec in fields.items()],
        metadata={b'null_policy':b'unknown is null; field status is explicit; missing cache never becomes zero',
                  b'csv_cell_codec':b'typed JSON; json.loads each CSV cell; all derived fields are present'})
    jsonl=destination/(name+'.jsonl');csv_path=destination/(name+'.csv');parquet=destination/(name+'.parquet')
    with jsonl.open('x') as js,csv_path.open('x',newline='',encoding='utf-8-sig') as cs,pq.ParquetWriter(parquet,schema,compression='zstd') as writer:
        csv_writer=csv.DictWriter(cs,fieldnames=columns);csv_writer.writeheader();batch=[]
        for row in table_rows:
            validate_row(row,fields);js.write(canonical(row)+'\n')
            csv_writer.writerow({key:canonical(row[key]) for key in columns});batch.append(row)
            if len(batch)>=1000:writer.write_table(pa.Table.from_pylist(batch,schema=schema));batch=[]
        if batch:writer.write_table(pa.Table.from_pylist(batch,schema=schema))
    schema_path=destination/(name+'.schema.json')
    schema_path.write_text(json.dumps(json_schema(kind,fields),ensure_ascii=False,indent=2))
    # Full ordered readback, including null/type/value equality, not only row count.
    for expected,record in zip_longest(table_rows,rows(jsonl)):
        if expected is None or record is None or canonical(expected)!=canonical(record[1]):
            raise RuntimeError('derived JSONL full readback mismatch')
    with csv_path.open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=columns:raise RuntimeError('CSV columns differ')
        for expected,actual in zip_longest(table_rows,reader):
            if expected is None or actual is None or canonical(expected)!=canonical({key:json.loads(actual[key]) for key in columns}):
                raise RuntimeError('CSV typed-cell full readback mismatch')
    def parquet_rows():
        for batch in pq.ParquetFile(parquet).iter_batches(batch_size=1000):yield from batch.to_pylist()
    for expected,actual in zip_longest(table_rows,parquet_rows()):
        if expected is None or actual is None or canonical(expected)!=canonical(actual):
            raise RuntimeError('Parquet full readback mismatch')
    actual_count=pq.ParquetFile(parquet).metadata.num_rows
    if actual_count!=len(table_rows):raise RuntimeError('Parquet row count differs')
    return {'table':name,'rows':len(table_rows),'readback':'FULL_ORDERED_TYPE_NULL_VALUE_COMPARISON_PASS',
        'sha256':{path.name:hash_file(path) for path in (jsonl,csv_path,parquet,schema_path)}}


def build(out,destination):
    out=Path(out);destination=Path(destination)
    if destination.exists():raise RuntimeError('use a new destination; derived exports are immutable')
    plan_path=out/'manifests/formal_plan.json'
    task_path=out/'raw/task_runs.jsonl'
    span_path=out/'raw/spans.jsonl'
    freeze_path=out/'manifests/formal-freeze.json'
    sources=[plan_path,task_path,out/'raw/provider_calls.jsonl',out/'raw/provider_attempt_starts.jsonl']
    if span_path.exists():sources.append(span_path)
    if freeze_path.exists():sources.append(freeze_path)
    # Snapshot before any parsing: a mutation between the preflight parse and
    # the first hash must not pass later unchanged-source certification.
    initial={str(path):hash_file(path) for path in sources}
    if freeze_path.exists():
        freeze=json.loads(freeze_path.read_text())
        expected=[value for key,value in freeze.get('files',{}).items() if key.endswith('/manifests/formal_plan.json')]
        if len(expected)!=1 or initial[str(plan_path)]!=expected[0]:raise RuntimeError('formal plan differs from the frozen identity')
    plan=json.loads(plan_path.read_text())
    units={u['run_id']:u for u in plan}
    if len(plan)!=EXPECTED_UNITS or len(units)!=EXPECTED_UNITS:raise RuntimeError('registered 6504-unit complete plan required')
    tasks=load_unique(task_path,'run_id')
    if set(tasks)!=set(units):raise RuntimeError('wait until all 6504 planned units have unique terminal records')
    if any(record['row'].get('status') not in TERMINAL_STATUSES for record in tasks.values()):
        raise RuntimeError('all task rows must have explicit terminal statuses')
    if any(record['row'].get('model_evidence')!='real_provider' for record in tasks.values()):
        raise RuntimeError('non-real task evidence is not eligible for provider authority export')
    destination.mkdir(parents=True)
    issues=[]
    try:
        ends=load_unique(out/'raw/provider_calls.jsonl','request_id')
        starts=load_unique(out/'raw/provider_attempt_starts.jsonl','request_id')
        spans=load_sdk_spans(span_path)
        attempts=[];by_run=defaultdict(list)
        ordered=sorted(set(starts)|set(ends),key=lambda key:(units.get((ends.get(key) or starts[key])['row'].get('run_id'),{}).get('phase',''),
            (ends.get(key) or starts[key])['row'].get('run_id',''),(ends.get(key) or starts[key])['row'].get('attempt') or 0,key))
        for request_id in ordered:
            end,start=ends.get(request_id),starts.get(request_id)
            source=(end or start)['row'];run_id=source.get('run_id')
            if run_id not in units:raise RuntimeError('unplanned provider attempt; refuse orphan aggregation')
            if start and end and start['row'].get('run_id')!=run_id:raise RuntimeError('provider start/end task identity mismatch')
            attempt=derive_attempt(request_id,start,end,units[run_id],tasks[run_id]['row'],spans,issues)
            attempts.append(attempt);by_run[run_id].append(attempt)
        task_rows=[derive_task(unit,tasks[unit['run_id']],by_run[unit['run_id']],issues) for unit in plan]
        tables=[write_table(destination,'provider_usage_per_attempt',attempts,'attempt'),
                write_table(destination,'provider_usage_per_task',task_rows,'task')]
        current={str(path):hash_file(path) for path in sources}
        if current!=initial:raise RuntimeError('raw source changed during authority rebuild; derived data not certified')
        primary_unknown=sum(not row['primary_usage_complete'] for row in task_rows)
        report={'schema_version':1,'created_utc':datetime.now(timezone.utc).isoformat(),
            'status':'FAIL_DATA_ISSUES' if any(i['severity']=='ERROR' for i in issues) else 'COMPLETE_WITH_USAGE_UNKNOWNS' if primary_unknown else 'PASS_PRIMARY_USAGE_COMPLETE',
            'planned_units':EXPECTED_UNITS,'derived_task_rows':len(task_rows),'derived_attempt_rows':len(attempts),
            'primary_usage_unknown_tasks':primary_unknown,
            'cache_unknown_tasks':sum(not row['cache_usage_complete'] for row in task_rows),
            'reasoning_unknown_tasks':sum(not row['reasoning_usage_complete'] for row in task_rows),
            'sdk_wall_unknown_tasks':sum(row['sdk_wall_sec'] is None for row in task_rows),
            'raw_task_token_difference_tasks':sum(row['raw_task_token_difference_count']>0 for row in task_rows),
            'raw_cached_zero_without_explicit_evidence_tasks':sum(row['raw_task_cached_zero_not_authoritative'] for row in task_rows),
            'per_field_attempt_status_counts':{field:dict(Counter(row[field+'_status'] for row in attempts)) for field in TOKEN_PATHS},
            'per_field_task_status_counts':{field:dict(Counter(row[field+'_status'] for row in task_rows)) for field in TOKEN_PATHS},
            'queue_latency':'UNKNOWN_NULL_NOT_OBSERVED','pure_http_rtt':'UNKNOWN_NULL_NOT_OBSERVED',
            'sources':initial,'source_module_sha256':hash_file(__file__),'tables':tables,'issues':issues,
            'raw_modified':False,'paid_api_calls':0,
            'authority':'only explicit recorded provider_calls.usage_raw counters, fieldwise across all attempts; task_run counters are provenance only',
            'cache_unknown_does_not_invalidate_complete_primary_counters':True,
            'sdk_wall_scope':'provider_wait monotonic SDK-create span when recorded; per-call wall envelope separately named; neither identifies provider queue nor pure RTT',
            'account_cost':'UNKNOWN_NOT_INFERRED_FROM_TOKEN_USAGE'}
    except Exception as error:
        report={'status':'FAIL_REBUILD','error_type':type(error).__name__,'error_text_sha256':digest(str(error)),
                'sources':initial,'issues':issues,'raw_modified':False,'paid_api_calls':0}
        (destination/'provider_usage_authority_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        raise
    (destination/'provider_usage_authority_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default=str(DEFAULT_OUT))
    parser.add_argument('--destination')
    args=parser.parse_args();base=Path(args.out)
    report=build(base,args.destination or base/'exports/provider_usage_authority_v1')
    print(json.dumps({key:report.get(key) for key in ('status','derived_task_rows','derived_attempt_rows','primary_usage_unknown_tasks','cache_unknown_tasks')}))
    raise SystemExit(report['status'].startswith('FAIL'))
