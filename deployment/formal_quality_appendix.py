"""Descriptive quality appendix, rebuilt only after all 6,504 terminal units.

No model client, secret, runtime package or paid API. The frozen decision parser
is vendored as an AST-identical stdlib function; primary success is never changed.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
import argparse
import ast
import csv
import hashlib
import json
import re

PARSER_ORIGIN = '/opt/lingxi/formal-checkout/src/agent_runtime/agents/model_decision.py'
PARSER_ORIGIN_SHA256 = '43db3734750289d030ad2aecda75ccfc35db14c79e1e18109010e3e5f70661b7'
PARSER_AST_SHA256 = '459ed5b9d4f7d66c4d8cee106783cf606e3d410be12d83d27d23b58fd2662cb9'
DEFAULT_OUT = Path('/evidence/formal_20261019')
EXPECTED_UNITS = 6504
TERMINAL = {'COMPLETED','FAILED','UNSUPPORTED','NON_COMPARABLE','INCOMPLETE','INCOMPLETE_BUDGET_STOP'}
LABEL_ACTIONS = {
    'port_conflict': 'inspect_listener',
    'permission_denied': 'inspect_permissions',
    'dns_failure': 'inspect_resolver',
    'disk_full': 'inspect_disk',
    'oom_killed': 'inspect_memory',
    'selinux_denied': 'inspect_audit',
    'compatible': 'keep_binary',
    'rebuild_required': 'rebuild_binary',
}


def parse_decision(text: str) -> dict:
    text = (text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
    result = json.loads(text)
    if not isinstance(result, dict) or set(result) != {'diagnosis', 'action'}:
        raise ValueError('model decision must contain exactly diagnosis and action')
    if result.get('diagnosis') not in LABEL_ACTIONS or result.get('action') not in LABEL_ACTIONS.values():
        raise ValueError('model selected an unsupported diagnosis or action')
    return result


def parser_ast_digest(text):
    tree=ast.parse(text)
    chosen=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name=='parse_decision':chosen.append(node)
        elif isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='LABEL_ACTIONS' for t in node.targets):chosen.append(node)
    if len(chosen)!=2:raise RuntimeError('frozen parser/mapping AST not found uniquely')
    return hashlib.sha256(ast.dump(ast.Module(body=chosen,type_ignores=[]),include_attributes=False).encode()).hexdigest()


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def recorded_sha(value):
    # Exact frozen Recorder.sha representation, which uses default JSON spacing.
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def hash_file(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):value.update(chunk)
    return value.hexdigest()


def records(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line,text in enumerate(stream,1):
            if not text.strip():continue
            value=json.loads(text,parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
            if not isinstance(value,dict):raise RuntimeError('object JSONL required')
            yield line,value


def unique(path,key):
    output={}
    for line,row in records(path):
        value=row.get(key)
        if not isinstance(value,str) or value in output:raise RuntimeError('missing/duplicate '+key+' in '+Path(path).name)
        output[value]={'row':row,'line':line,'sha256':sha(row)}
    return output


def standard_json_syntax(text):
    # An appendix observation only: never changes the vendored decision parser.
    text = (text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
    def reject_constant(name):
        raise ValueError('Non-standard JSON constant: '+name)
    try:
        json.loads(text,parse_constant=reject_constant)
    except (json.JSONDecodeError,ValueError) as error:
        return False,type(error).__name__
    return True,None


def assessment(response,expected):
    result={'response_text_status':'MISSING_RESPONSE','json_syntax_valid':None,'decision_contract_valid':None,
        'decision_answer_complete':None,'diagnosis_correct':None,'action_correct':None,
        'predicted_diagnosis':None,'predicted_action':None,'response_text_sha256':None,
        'response_text_utf8_bytes':None,'refusal_marker_observed':None,'parse_error_type':None,
        'json_syntax_error_type':None}
    if not isinstance(response,dict):return result
    choices=response.get('choices')
    if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
        result['response_text_status']='MISSING_CHOICES_STRUCTURE';return result
    message=choices[0].get('message')
    if not isinstance(message,dict):
        result['response_text_status']='MISSING_MESSAGE_STRUCTURE';return result
    result['refusal_marker_observed']=bool(message.get('refusal'))
    if 'content' not in message:
        result['response_text_status']='MISSING_CONTENT_FIELD';return result
    text=message['content']
    if text is None:text='';result['response_text_status']='EXPLICIT_NULL_NORMALIZED_TO_EMPTY_BY_FROZEN_PROVIDER'
    elif isinstance(text,str):result['response_text_status']='OBSERVED_STRING'
    else:
        result.update(response_text_status='OBSERVED_NON_STRING_CONTENT',decision_contract_valid=False,
                      decision_answer_complete=False,diagnosis_correct=False,action_correct=False)
        return result
    result.update(response_text_sha256=hashlib.sha256(text.encode()).hexdigest(),response_text_utf8_bytes=len(text.encode()))
    result['json_syntax_valid'],result['json_syntax_error_type']=standard_json_syntax(text)
    try:
        parsed=parse_decision(text)
    except json.JSONDecodeError as error:
        result.update(decision_contract_valid=False,decision_answer_complete=False,
                      diagnosis_correct=False,action_correct=False,parse_error_type=type(error).__name__)
    except (ValueError,TypeError) as error:
        result.update(decision_contract_valid=False,decision_answer_complete=False,
                      diagnosis_correct=False,action_correct=False,parse_error_type=type(error).__name__)
    else:
        result.update(decision_contract_valid=True,decision_answer_complete=True,
            predicted_diagnosis=parsed['diagnosis'],predicted_action=parsed['action'],
            diagnosis_correct=parsed['diagnosis']==expected['diagnosis'],action_correct=parsed['action']==expected['action'])
    return result


def input_bin(value):
    for upper,label in [(2048,'lt_2KiB'),(8192,'2_to_8KiB'),(32768,'8_to_32KiB'),(131072,'32_to_128KiB')]:
        if value<upper:return label
    return 'ge_128KiB'


COMMON = {'run_id':'string','phase':'string','task_id':'string','chain_id':'string','domain':'string',
    'difficulty':'string','arm':'string','repeat':'integer','model':'string','model_id':'string',
    'topology':'string','processes':'boolean'}
ASSESSMENT = {'response_text_status':'string','json_syntax_valid':'boolean','decision_contract_valid':'boolean',
    'decision_answer_complete':'boolean','diagnosis_correct':'boolean','action_correct':'boolean',
    'predicted_diagnosis':'string','predicted_action':'string','response_text_sha256':'string',
    'response_text_utf8_bytes':'integer','refusal_marker_observed':'boolean','parse_error_type':'string',
    'json_syntax_error_type':'string'}
UNIT = {**COMMON,**ASSESSMENT,'independent_task_success':'boolean','terminal_answer_complete':'boolean',
    'fixture_check_matches':'boolean','expected_diagnosis':'string','expected_action':'string',
    'frozen_fact_utf8_bytes':'integer','input_length_bin':'string','state_applicable':'boolean',
    'state_size_class':'string','chain_position':'integer','runtime_state_exchange_enabled':'boolean',
    'actual_state_consumed':'boolean','selected_request_id':'string','response_selection_status':'string',
    'successful_provider_calls':'integer','provider_attempt_count':'integer','response_candidates':'integer',
    'terminal_status':'string','raw_error_json':'string','explicit_timeout_marker_observed':'boolean',
    'timeout_evidence_json':'string','automatic_error_classification':'string','request_ids_json':'string',
    'requested_models_json':'string','returned_models_json':'string','source_task_line':'integer',
    'source_task_sha256':'string','selected_response_line':'integer','selected_response_sha256':'string'}
REQUEST = {**COMMON,**ASSESSMENT,'request_id':'string','attempt':'integer','provider_status':'string',
    'requested_model':'string','returned_model':'string','provider_request_id':'string','endpoint':'string',
    'temperature':'number','max_tokens':'integer','timeout_sec':'number','finish_reason':'string',
    'actual_prompt_sha256':'string','response_sha256':'string','request_io_sha_matches':'boolean',
    'response_io_sha_matches':'boolean','request_io_count':'integer','response_io_count':'integer','source_call_line':'integer',
    'source_call_sha256':'string','response_io_line':'integer','error_type':'string','error_json':'string',
    'explicit_timeout_marker_observed':'boolean'}
SUMMARY = {'phase':'string','model_id':'string','topology':'string','arm':'string','slice_dimension':'string',
    'slice_label':'string','metric':'string','all_planned_units':'integer','unique_tasks':'integer','chains':'integer',
    'true_count':'integer','false_count':'integer','unknown_count':'integer','rate_among_known':'number',
    'all_planned_identification_lower':'number','all_planned_identification_upper':'number','inference_scope':'string'}


def common(unit,terminal):
    result={name:unit.get(name) for name in COMMON};result['model_id']=terminal.get('model_id');return result


def timeout_marker(value):
    return bool(re.search(r'timeout|timed\s*out|超时',canonical(value),re.I)) if value is not None else False


def classify(row):
    if row['independent_task_success'] is True:return 'TASK_SUCCESS_ORIGINAL_VERIFIER'
    if row['explicit_timeout_marker_observed']:return 'EXPLICIT_TIMEOUT_MARKER_OBSERVED'
    if row['response_selection_status']!='SELECTED_SINGLE_SUCCESS_RESPONSE':return row['response_selection_status']
    if row['refusal_marker_observed']:return 'EXPLICIT_REFUSAL_MARKER'
    if row['json_syntax_valid'] is False:return 'INVALID_STANDARD_JSON_SYNTAX_APPENDIX_VALIDATOR'
    if row['decision_contract_valid'] is False:return 'INVALID_DECISION_CONTRACT'
    if row['diagnosis_correct'] is False and row['action_correct'] is False:return 'DIAGNOSIS_AND_ACTION_WRONG'
    if row['diagnosis_correct'] is False:return 'DIAGNOSIS_WRONG'
    if row['action_correct'] is False:return 'ACTION_WRONG'
    return 'SYSTEM_OR_VERIFIER_FAILURE_WITHOUT_ACCEPTED_SUCCESS'


def grouped(unit_rows):
    grouped_rows=defaultdict(list)
    for row in unit_rows:
        slices={'overall':'ALL','domain':row['domain'],'difficulty':row['difficulty'],
            'input_length_bin':row['input_length_bin'],'frozen_state_size':row['state_size_class'],
            'chain_position':str(row['chain_position']),'model_identity':row['model_id'] or 'UNKNOWN'}
        for dimension,label in slices.items():
            grouped_rows[(row['phase'],row['model_id'],row['topology'],row['arm'],dimension,label)].append(row)
    metrics=['independent_task_success','diagnosis_correct','action_correct','decision_answer_complete',
             'terminal_answer_complete','json_syntax_invalid','decision_contract_invalid',
             'refusal_marker_observed','explicit_timeout_marker_observed']
    output=[]
    for key,values in sorted(grouped_rows.items(),key=lambda item:str(item[0])):
        for metric in metrics:
            raw=[(not r['json_syntax_valid'] if r['json_syntax_valid'] is not None else None) if metric=='json_syntax_invalid'
                 else (not r['decision_contract_valid'] if r['decision_contract_valid'] is not None else None) if metric=='decision_contract_invalid'
                 else r[metric] for r in values]
            yes=sum(v is True for v in raw);no=sum(v is False for v in raw);unknown=len(raw)-yes-no;n=len(raw)
            output.append(dict(zip(('phase','model_id','topology','arm','slice_dimension','slice_label'),key),
                metric=metric,all_planned_units=n,unique_tasks=len({r['task_id'] for r in values}),
                chains=len({r['chain_id'] for r in values}),true_count=yes,false_count=no,unknown_count=unknown,
                rate_among_known=yes/(yes+no) if yes+no else None,
                all_planned_identification_lower=yes/n,all_planned_identification_upper=(yes+unknown)/n,
                inference_scope='DESCRIPTIVE_IDENTIFICATION_BOUNDS_NOT_CI_NO_NEW_CONFIRMATORY_FAMILY'))
    return output


def write_table(destination,name,data,types):
    import pyarrow as pa
    import pyarrow.parquet as pq
    columns=list(types)
    data=[{key:row.get(key) for key in columns} for row in data]
    dtypes={'string':pa.string(),'integer':pa.int64(),'number':pa.float64(),'boolean':pa.bool_()}
    schema=pa.schema([pa.field(key,dtypes[typ],nullable=True) for key,typ in types.items()],
        metadata={b'csv_cell_codec':b'typed JSON; unknown is null; all derived fields explicitly present',
                  b'scope':b'exploratory quality appendix; raw independent success unchanged'})
    paths={extension:destination/(name+'.'+extension) for extension in ('jsonl','csv','parquet','schema.json')}
    with paths['jsonl'].open('x') as js,paths['csv'].open('x',newline='',encoding='utf-8-sig') as cs,pq.ParquetWriter(paths['parquet'],schema,compression='zstd') as writer:
        csv_writer=csv.DictWriter(cs,fieldnames=columns);csv_writer.writeheader();batch=[]
        for row in data:
            js.write(canonical(row)+'\n');csv_writer.writerow({key:canonical(row[key]) for key in columns});batch.append(row)
            if len(batch)>=1000:writer.write_table(pa.Table.from_pylist(batch,schema=schema));batch=[]
        if batch:writer.write_table(pa.Table.from_pylist(batch,schema=schema))
    observed={'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object',
        'properties':{key:{'type':[typ,'null']} for key,typ in types.items()},'required':columns,'additionalProperties':False}
    paths['schema.json'].write_text(json.dumps(observed,ensure_ascii=False,indent=2))
    for expected,actual in zip_longest(data,(row for _,row in records(paths['jsonl']))):
        if expected is None or actual is None or canonical(expected)!=canonical(actual):raise RuntimeError('quality JSONL full readback mismatch')
    cell_limit=max((len(canonical(value)) for row in data for value in row.values()),default=0)+1
    csv.field_size_limit(max(csv.field_size_limit(),cell_limit))
    with paths['csv'].open(newline='',encoding='utf-8-sig') as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=columns:raise RuntimeError('quality CSV column mismatch')
        for expected,actual in zip_longest(data,reader):
            if expected is None or actual is None or canonical(expected)!=canonical({key:json.loads(actual[key]) for key in columns}):
                raise RuntimeError('quality CSV full typed readback mismatch')
    def parquet_rows():
        for batch in pq.ParquetFile(paths['parquet']).iter_batches(batch_size=1000):yield from batch.to_pylist()
    for expected,actual in zip_longest(data,parquet_rows()):
        if expected is None or actual is None or canonical(expected)!=canonical(actual):raise RuntimeError('quality Parquet full readback mismatch')
    return {'table':name,'rows':len(data),'full_type_null_value_readback':'PASS',
            'sha256':{path.name:hash_file(path) for path in paths.values()}}


def build(out,destination):
    out=Path(out);destination=Path(destination)
    if destination.exists():raise RuntimeError('use a new immutable quality appendix destination')
    if parser_ast_digest(Path(__file__).read_text())!=PARSER_AST_SHA256:raise RuntimeError('vendored frozen parser AST changed')
    sources=[out/'manifests/formal_plan.json',out/'corpus/automatic_validated_v1/tasks.jsonl',
             out/'raw/task_runs.jsonl',out/'raw/provider_calls.jsonl',out/'raw/model_io.jsonl']
    freeze_path=out/'manifests/formal-freeze.json'
    sources.append(freeze_path)
    source_hashes={str(path):hash_file(path) for path in sources}
    freeze=json.loads(freeze_path.read_text())
    for path in sources[:2]:
        suffix='/manifests/formal_plan.json' if path==sources[0] else '/corpus/automatic_validated_v1/tasks.jsonl'
        expected=[value for key,value in freeze.get('files',{}).items() if key.endswith(suffix)]
        if len(expected)!=1 or source_hashes[str(path)]!=expected[0]:raise RuntimeError('frozen plan/corpus identity changed')
    plan=json.loads(sources[0].read_text());units={u['run_id']:u for u in plan}
    if len(plan)!=EXPECTED_UNITS or len(units)!=EXPECTED_UNITS:raise RuntimeError('exact registered 6504-unit plan required')
    corpus={r['task_id']:r for _,r in records(sources[1])}
    tasks=unique(sources[2],'run_id');calls=unique(sources[3],'request_id')
    if set(tasks)!=set(units) or any(t['row'].get('status') not in TERMINAL for t in tasks.values()):
        raise RuntimeError('wait for all 6504 unique terminal units')
    if any(t['row'].get('model_evidence')!='real_provider' for t in tasks.values()):raise RuntimeError('real-provider task evidence required')
    issues=[];responses=defaultdict(list);requests=defaultdict(list);by_run=defaultdict(list)
    for request_id,record in calls.items():
        run_id=record['row'].get('run_id')
        if run_id not in units:raise RuntimeError('orphan provider call')
        by_run[run_id].append(request_id)
    for line,item in records(sources[4]):
        request_id=item.get('request_id')
        if item.get('direction')=='request':requests[request_id].append(recorded_sha(item.get('messages')))
        elif item.get('direction')=='response':
            if request_id not in calls:
                issues.append({'code':'ORPHAN_RESPONSE_IO','request_id':request_id,'line':line});continue
            call=calls[request_id]['row'];case=corpus[units[call['run_id']]['task_id']]
            response=item.get('response')
            responses[request_id].append({'line':line,'sha256':recorded_sha(response),
                'assessment':assessment(response,case['expected'])})
    request_rows=[];request_by_id={}
    for request_id,record in calls.items():
        call=record['row'];run_id=call['run_id'];unit=units[run_id];terminal=tasks[run_id]['row']
        matched=responses[request_id];selected=matched[0] if len(matched)==1 else None
        assessed=selected['assessment'] if selected else assessment(None,{})
        request_hashes=requests[request_id]
        req_matches=request_hashes[0]==call.get('actual_prompt_sha256') if len(request_hashes)==1 and call.get('actual_prompt_sha256') else None
        res_matches=selected['sha256']==call.get('response_sha256') if selected and call.get('response_sha256') else None
        if req_matches is False or res_matches is False or len(matched)>1 or len(request_hashes)!=1:
            issues.append({'code':'REQUEST_RESPONSE_IDENTITY_MISMATCH_OR_DUPLICATE','request_id':request_id})
        item={**common(unit,terminal),**assessed,'request_id':request_id,'attempt':call.get('attempt'),
            'provider_status':call.get('status'),'requested_model':call.get('requested_model'),'returned_model':call.get('returned_model'),
            'provider_request_id':call.get('provider_request_id'),'endpoint':call.get('endpoint'),
            'temperature':float(call['temperature']) if call.get('temperature') is not None else None,
            'max_tokens':call.get('max_tokens'),'timeout_sec':float(call['timeout_sec']) if call.get('timeout_sec') is not None else None,
            'finish_reason':call.get('finish_reason'),'actual_prompt_sha256':call.get('actual_prompt_sha256'),
            'response_sha256':call.get('response_sha256'),'request_io_sha_matches':req_matches,'response_io_sha_matches':res_matches,
            'request_io_count':len(request_hashes),'response_io_count':len(matched),'source_call_line':record['line'],'source_call_sha256':record['sha256'],
            'response_io_line':selected['line'] if selected else None,'error_type':call.get('error_type'),
            'error_json':canonical(call.get('error')),'explicit_timeout_marker_observed':timeout_marker([call.get('error_type'),call.get('error')])}
        request_rows.append(item);request_by_id[request_id]=item
    unit_rows=[]
    for unit in plan:
        run_id=unit['run_id'];record=tasks[run_id];raw=record['row'];case=corpus[unit['task_id']]
        ids=sorted(by_run[run_id],key=lambda request_id:(calls[request_id]['row'].get('attempt') or 0,request_id))
        successful=[request_id for request_id in ids if calls[request_id]['row'].get('status')=='OK']
        selected=successful[0] if len(successful)==1 and len(responses[successful[0]])==1 else None
        if selected and (request_by_id[selected]['response_io_sha_matches'] is False or request_by_id[selected]['request_io_sha_matches'] is False):selected=None
        assessed={key:request_by_id[selected][key] for key in ASSESSMENT} if selected else assessment(None,case['expected'])
        status='SELECTED_SINGLE_SUCCESS_RESPONSE' if selected else 'AMBIGUOUS_MULTIPLE_SUCCESSFUL_RESPONSES' if len(successful)>1 else 'NO_UNIQUE_MATCHED_SUCCESSFUL_RESPONSE'
        answer=(raw.get('outputs') or {}).get('answer')
        complete=isinstance(answer,dict) and all(isinstance(answer.get(key),str) and bool(answer[key]) for key in ('diagnosis','action','probe'))
        facts={'observations':case['observations'],'procedure':case['procedure']};length=len(canonical(facts).encode('utf-8'))
        timeouts=[{'request_id':request_id,'error_type':calls[request_id]['row'].get('error_type')} for request_id in ids if request_by_id[request_id]['explicit_timeout_marker_observed']]
        terminal_timeout=timeout_marker(raw.get('error'))
        if terminal_timeout:timeouts.append({'terminal_error_marker':True})
        item={**common(unit,raw),**assessed,'independent_task_success':raw.get('success'),
            'terminal_answer_complete':complete,'fixture_check_matches':(raw.get('observed_fixture_check') or {}).get('current_record_matches_check'),
            'expected_diagnosis':case['expected']['diagnosis'],'expected_action':case['expected']['action'],
            'frozen_fact_utf8_bytes':length,'input_length_bin':input_bin(length),'state_applicable':case['state_spec']['applicable'],
            'state_size_class':case['state_spec']['size_class'] if case['state_spec']['applicable'] else 'NOT_APPLICABLE',
            'chain_position':case['observations']['checkpoint'],'runtime_state_exchange_enabled':raw.get('state_exchange_enabled'),
            'actual_state_consumed':raw.get('state_consumed'),'selected_request_id':selected,'response_selection_status':status,
            'successful_provider_calls':len(successful),'provider_attempt_count':len(ids),
            'response_candidates':sum(len(responses[request_id]) for request_id in ids),'terminal_status':raw.get('status'),
            'raw_error_json':canonical(raw.get('error')),'explicit_timeout_marker_observed':bool(timeouts),
            'timeout_evidence_json':canonical(timeouts),'request_ids_json':canonical(ids),
            'requested_models_json':canonical(sorted({calls[r]['row']['requested_model'] for r in ids if calls[r]['row'].get('requested_model')})),
            'returned_models_json':canonical(sorted({calls[r]['row']['returned_model'] for r in ids if calls[r]['row'].get('returned_model')})),
            'source_task_line':record['line'],'source_task_sha256':record['sha256'],
            'selected_response_line':responses[selected][0]['line'] if selected else None,
            'selected_response_sha256':responses[selected][0]['sha256'] if selected else None}
        item['automatic_error_classification']=classify(item)
        if raw.get('provider_attempts')!=len(ids):issues.append({'code':'TASK_ATTEMPT_COUNT_MISMATCH','run_id':run_id})
        unit_rows.append(item)
    summaries=grouped(unit_rows)
    failures=[row for row in unit_rows if row['independent_task_success'] is not True or row['explicit_timeout_marker_observed'] or row['decision_contract_valid'] is False]
    destination.mkdir(parents=True)
    tables=[write_table(destination,'quality_per_unit',unit_rows,UNIT),
        write_table(destination,'quality_group_summary',summaries,SUMMARY),
        write_table(destination,'quality_failures_timeouts',failures,UNIT),
        write_table(destination,'quality_request_identity',request_rows,REQUEST)]
    if {str(path):hash_file(path) for path in sources}!=source_hashes:raise RuntimeError('raw source changed during quality rebuild')
    report={'status':'FAIL_SOURCE_IDENTITY_ISSUES' if issues else 'PASS_DESCRIPTIVE_APPENDIX',
        'created_utc':datetime.now(timezone.utc).isoformat(),'planned_units':6504,'per_unit_rows':len(unit_rows),
        'provider_attempt_rows':len(request_rows),'source_sha256':source_hashes,'module_sha256':hash_file(__file__),
        'parser_origin':PARSER_ORIGIN,'parser_origin_sha256':PARSER_ORIGIN_SHA256,'vendored_parser_ast_sha256':PARSER_AST_SHA256,
        'tables':tables,'issues':issues,'raw_modified':False,'primary_success_or_analysis_changed':False,
        'unknown_selected_response_units':sum(row['decision_contract_valid'] is None for row in unit_rows),
        'human_raters':0,'blind_ratings':'NOT_PERFORMED_USER_AUTHORIZED_AUTOMATIC_VALIDATION',
        'inter_rater_agreement':'NOT_APPLICABLE','paid_api_calls':0,
        'inference':'exploratory descriptive slices only; identification bounds are not CI; no new confirmatory family',
        'response_missing_policy':'unknown/null, never silently valid or known-invalid; observed empty text is explicitly invalid under frozen parser',
        'json_syntax_policy':'Separate standard-JSON appendix validator uses the same fence stripping and rejects NaN/Infinity constants; frozen decision contract and original task success remain unchanged; no answer repair.',
        'input_length_unit':'canonical frozen source facts UTF-8 bytes, 1KiB=1024; bins predeclared in quality_appendix_contract.json'}
    (destination/'quality_appendix_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',default=str(DEFAULT_OUT));parser.add_argument('--destination')
    args=parser.parse_args();out=Path(args.out)
    report=build(out,args.destination or out/'exports/quality_appendix_v1')
    print(json.dumps({key:report.get(key) for key in ('status','per_unit_rows','provider_attempt_rows','unknown_selected_response_units')}))
    raise SystemExit(report['status'].startswith('FAIL'))
