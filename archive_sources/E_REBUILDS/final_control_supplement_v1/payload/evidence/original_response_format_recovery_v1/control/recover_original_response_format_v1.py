"""Recover frozen response-format facts, never diagnosis scores or new calls.

Source preparation only until root approves execution. Request bodies are not
JSON-decoded or emitted. Only returned response objects and safe attempt/unit
metadata are joined; original pure parser AST and installed24 data are replayed.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter, defaultdict
import copy
import csv
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

STUDY = 'protocol_repair_20261020_v1'
EXPECTED = {
 'model_io': ('91c5cb0a29c91c43616717618e7497c1a5d3bb8139cdfe71048545cc089e45a7',128720848),
 'provider_calls': ('9b4c0d60aadb040097a3f60ce7256b6c7f7b39c7c8d8d767f6d2a194bfc3e14b',20205453),
 'observations': ('1c2031eec1ad922c753199fc4cc0a7760fdeca0f110becc6d2e83db0dc12281a',45655316),
 'formal_plan': ('3944e9ae3b5de942dd6e463d7b241fb3d1b3f363415249a52b893ad1b2d3a159',5311350),
 'native_configurations': ('86547efeca03977abc74acd3b7912270e7a227e9375251a19fbf27cff08378e3',26319),
 'decision_source': ('43db3734750289d030ad2aecda75ccfc35db14c79e1e18109010e3e5f70661b7',3190),
 'contract_source': ('44e5658967db62bdfafe54accabddfdc187bd6a241fc80d59fef32fff4192749',6014),
 'bootstrap_source': ('590d691d30816a6f7aae06e0177a631b9b53dd065627dfca381737cbb5c2b269',1414),
 'real_driver_source': ('d63b08e0120513944d0f0c40a4bdd221cf910518d01c446a0acc0ab1afc6c93b',37221),
 'external_runner_source': ('45da2d4ae69ea5a9b4e66c37b677d21e2723cc83fa4b7c82b5da4b2140fd9c60',23096),
 'recorder_source': ('0357d1f36373d4bfc403dc1b9515685aa7f168586624401e3f74e98e5ea0011c',11257),
 'external_adapter_source': ('7057e71107947aecfd64784910ff706305b12e481ad10438e533b9dce7dc7ab5',13819),
 'executor_source': ('c03aa09162f9ea1dd42360b1e875348e1946c06d21a4e26a3cbb17d651027b68',18828),
 'context_source': ('f6fb506aaf17728c2c59c007212dff9d651afe2d8c1e7e1f992f3aec88c08ee3',10117),
 'gateway_source': ('0af80d4f39a434cab2c343098871be1327128770e8b544807a0d3272aef63c0e',19309),
 'provider_source': ('0d5029048539e1fc6bdefc450f042ce5f57acc4567b6869163eaeb78e46b847b',16389),
}
META = ['unit_id','run_id','phase','task_id','chain_id','domain','arm','repeat','model','model_id','topology','processes']
FLAGS = ['frozen_normalized_json_decoder_accepted','decoded_is_object',
         'required_diagnosis_action_keys_present','required_keys_exact', 'frozen_parser_accepted']


def require(condition, code):
    if not condition:
        raise ValueError(code)  # fixed code only; never interpolate model/request text


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''): h.update(part)
    return h.hexdigest()


def envelope(text):
    def pairs(items):
        r={}
        for k,v in items:
            require(k not in r,'DUPLICATE_ENVELOPE_KEY')
            r[k]=v
        return r
    return json.loads(text,object_pairs_hook=pairs)


def read(path):
    return envelope(Path(path).read_text(encoding='utf-8'))


def write(path,value):
    with path.open('x',encoding='utf-8',newline='\n') as f:
        f.write(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n')


def write_csv(path,rows,fields):
    with path.open('x',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n'); w.writeheader()
        for r in rows: w.writerow({k:canonical(r.get(k)) for k in fields})


def identity(row):
    return {k:row.get(k) for k in META}


def direction_before_body(line):
    """Read top-level prefix only. Frozen writer puts direction before any body.

    Decode metadata values until direction, never request/messages/response/headers.
    A changed field ordering fails closed instead of decoding a request body.
    """
    text=line.decode('utf-8'); dec=json.JSONDecoder(); p=0
    def white(pos):
        while pos<len(text) and text[pos].isspace(): pos+=1
        return pos
    p=white(p); require(p<len(text) and text[p]=='{','MODEL_IO_ENVELOPE_NOT_OBJECT'); p+=1
    while True:
        p=white(p); require(p<len(text),'MODEL_IO_PREFIX_TRUNCATED')
        if text[p]=='}': return None
        key,end=dec.raw_decode(text,p); require(isinstance(key,str),'MODEL_IO_PREFIX_KEY_TYPE')
        p=white(end); require(text[p]==':','MODEL_IO_PREFIX_COLON'); p=white(p+1)
        require(key not in {'request','messages','response','headers'},'BODY_PRECEDES_DIRECTION_NOT_DECODED')
        value,p=dec.raw_decode(text,p)
        if key=='direction': return value
        p=white(p); require(p<len(text),'MODEL_IO_PREFIX_TRUNCATED')
        if text[p]=='}': return None
        require(text[p]==',','MODEL_IO_PREFIX_SEPARATOR'); p+=1


def frozen_parser(paths):
    contract=ast.parse(paths['contract_source'].read_text(encoding='utf-8'))
    node=next(n for n in contract.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MECHANISMS' for t in n.targets))
    mechanisms=ast.literal_eval(node.value)
    labels={label:action for group in mechanisms.values() for label,action,_ in group}
    require(len(labels)==len(set(labels.values()))==24,'INSTALLED_CONTRACT_NOT_24')
    tree=ast.parse(paths['decision_source'].read_text(encoding='utf-8'))
    parser=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='parse_decision')
    marker=next(i for i,n in enumerate(parser.body) if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call)
        and isinstance(n.value.func,ast.Attribute) and isinstance(n.value.func.value,ast.Name)
        and n.value.func.value.id=='json' and n.value.func.attr=='loads')
    # Exact frozen normalization statements, not a stricter replacement parser.
    normalizer=copy.deepcopy(parser); normalizer.name='_frozen_normalize_only'
    normalizer.body=copy.deepcopy(parser.body[:marker])+[ast.Return(value=ast.Name(id='text',ctx=ast.Load()))]
    normalizer.returns=None
    module=ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(parser),normalizer],type_ignores=[]))
    namespace={'json':json,'re':re,'LABEL_ACTIONS':labels}
    exec(compile(module,'<hash-bound-pure-parser-AST>','exec'),namespace)
    return namespace['parse_decision'],namespace['_frozen_normalize_only'],{
        'function':'model_decision.parse_decision','function_ast_sha256':hashlib.sha256(ast.dump(parser,include_attributes=False).encode()).hexdigest(),
        'normalization_ast_sha256':hashlib.sha256(ast.dump(normalizer,include_attributes=False).encode()).hexdigest(),
        'installed_label_count':24,'installed_action_count':24,'required_keys':['diagnosis','action'],
        'value_rule':'diagnosis and action separately belong to installed24 sets; no diagnosis/action pair-correctness or oracle comparison',
        'decoder_semantics':'original default json.loads, including its duplicate-key and nonfinite-extension behavior; not RFC-strict JSON certification',
        'execution_scope':'only copied pure function AST + exact normalization prefix in isolated namespace; no SDK/runtime import or original module/global mutation'}


def recover_flags(row,parse_decision,normalize):
    response=row.get('response'); external=row.get('phase','').startswith('external_')
    record={k:None for k in FLAGS}
    record.update(schema_applicable='FROZEN_EXECUTOR_DIAGNOSIS_ACTION',parser_error_type=None,
        json_decoder_error_type=None,returned_text_utf8_bytes=None,returned_text_sha256=None,
        normalization_removed_optional_fence=None,
        recovery_status='UNKNOWN_UNSUPPORTED_SDK_RESPONSE_SHAPE',runtime_empty_from_null_or_no_choices=False)
    if not isinstance(response,dict): return record
    choices=response.get('choices')
    if not isinstance(choices,list): return record
    if not choices:
        if not external: return record
        content=''; record['runtime_empty_from_null_or_no_choices']=True
    else:
        if not isinstance(choices[0],dict) or not isinstance(choices[0].get('message'),dict): return record
        content=choices[0]['message'].get('content')
        if content is None:
            content=''; record['runtime_empty_from_null_or_no_choices']=True
        elif not isinstance(content,str): return record
    # No response string, parsed values or exception message leaves this function.
    record['returned_text_utf8_bytes']=len(content.encode('utf-8'))
    record['returned_text_sha256']=hashlib.sha256(content.encode('utf-8')).hexdigest()
    normalized=normalize(content)
    record['normalization_removed_optional_fence']=content.strip().startswith('```')
    try:
        parsed=json.loads(normalized)  # exactly frozen decoder; never envelope() here
        record['frozen_normalized_json_decoder_accepted']=True
        record['decoded_is_object']=isinstance(parsed,dict)
        if isinstance(parsed,dict):
            record['required_diagnosis_action_keys_present']={'diagnosis','action'}<=set(parsed)
            record['required_keys_exact']=set(parsed)=={'diagnosis','action'}
        else:
            record['required_diagnosis_action_keys_present']=False
            record['required_keys_exact']=False
    except Exception as exc:
        record['frozen_normalized_json_decoder_accepted']=False
        record['json_decoder_error_type']=type(exc).__name__
    try:
        parse_decision(content)
        record['frozen_parser_accepted']=True
    except Exception as exc:
        record['frozen_parser_accepted']=False
        record['parser_error_type']=type(exc).__name__
    record['recovery_status']='RECOVERED_PURE_FROZEN_FORMAT_FLAGS'
    return record


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--inputs',type=Path,required=True); ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    require(args.inputs.is_absolute() and args.out.is_absolute(),'ABSOLUTE_PATHS_REQUIRED')
    require(not args.out.exists() and args.out.parent.is_dir(),'FRESH_OUTPUT_LEAF_REQUIRED')
    manifest=read(args.inputs)
    require(manifest.get('schema_version')=='original-response-format-recovery-inputs-v1' and manifest.get('study_id')==STUDY,'INPUT_MANIFEST_SCHEMA')
    require(manifest.get('adapter_source_sha256')==sha(Path(__file__)),'ADAPTER_HASH')
    require(set(manifest.get('files',{}))==set(EXPECTED),'EXACT_INPUT_SET_REQUIRED')
    paths={}; bindings={}
    for name,(pin,size) in EXPECTED.items():
        item=manifest['files'][name]; path=Path(item['path']); parts=PurePosixPath(item['path']).parts
        require(path.is_absolute() and parts[:2]==('/','input') and '..' not in parts,'INPUT_PATH_ALLOWLIST')
        require(item['sha256']==pin and item['bytes']==size and path.is_file() and path.stat().st_size==size,'INPUT_PIN_OR_SIZE')
        # Large streams are hashed in their single processing pass, not reread.
        if name not in {'model_io','provider_calls','observations'}: require(sha(path)==pin,'INPUT_HASH')
        paths[name]=path; bindings[name]={'path':str(path),'sha256':pin,'bytes':size}
    configurations=read(paths['native_configurations'])
    require(len(configurations)==11,'CONFIGURATION_COUNT')
    config_flags={}
    for name,cfg in configurations.items():
        flags={'output_contract':cfg['llm']['output_contract'],'llm_coalesce':cfg['llm_coalesce'],
            'llm_advisory':cfg['llm_advisory'],'llm_judge':cfg['llm_judge']}
        require(flags=={'output_contract':'none','llm_coalesce':'off','llm_advisory':False,'llm_judge':False},'ACTIVE_SCHEMA_CHANGED')
        config_flags[name]=flags
    parse_decision,normalize,parser_receipt=frozen_parser(paths)
    plan_rows=read(paths['formal_plan']); plan={r['unit_id']:r for r in plan_rows}
    require(len(plan_rows)==len(plan)==6504,'PLAN_ID_COVERAGE')
    observed={}; h=hashlib.sha256()
    with paths['observations'].open('rb') as f:
        for line in f:
            h.update(line); r=envelope(line); uid=r['unit_id']; require(uid not in observed and uid in plan,'OBSERVATION_ID')
            require(all(r.get(k)==plan[uid].get(k) for k in META),'OBSERVATION_PLAN_IDENTITY')
            require(type(r.get('success')) is bool and type(r.get('provider_attempts')) is int,'OBSERVATION_METADATA_TYPES')
            observed[uid]={**identity(r),'success':r['success'],'terminal_status':r.get('status'),
                'provider_attempts_recorded':r['provider_attempts'],'raw_file':r['raw_file'],
                'raw_line':r['raw_line'],'raw_line_sha256':r['raw_line_sha256']}
    require(h.hexdigest()==EXPECTED['observations'][0] and set(observed)==set(plan),'OBSERVATION_HASH_COVERAGE')
    attempts={}; per_unit_attempts=Counter(); h=hashlib.sha256()
    with paths['provider_calls'].open('rb') as f:
        for line_number,line in enumerate(f,1):
            h.update(line); r=envelope(line); rid=r['request_id']; uid=r['unit_id']
            require(uid in observed and rid not in attempts,'ATTEMPT_IDENTITY')
            require(all(r.get(k)==observed[uid].get(k) for k in META),'ATTEMPT_UNIT_IDENTITY')
            attempts[rid]={**identity(r),'request_id':rid,'provider_status':r.get('status'),
                'provider_error_type':r.get('error_type'),'raw_file':'provider_calls.jsonl',
                'raw_line':line_number,'raw_line_sha256':hashlib.sha256(line).hexdigest()}
            per_unit_attempts[uid]+=1
    require(h.hexdigest()==EXPECTED['provider_calls'][0],'ATTEMPT_STREAM_HASH')
    require(all(per_unit_attempts[u]==r['provider_attempts_recorded'] for u,r in observed.items()),'ATTEMPT_COVERAGE_VS_AUDIT')
    args.out.mkdir(exist_ok=False)
    response_rows=[]; response_by_request=defaultdict(list); response_by_unit=defaultdict(list)
    skipped=Counter(); h=hashlib.sha256()
    with paths['model_io'].open('rb') as f:
        for line_number,line in enumerate(f,1):
            h.update(line); direction=direction_before_body(line)
            if direction!='response': skipped[str(direction)]+=1; continue
            r=envelope(line); require(r.get('direction')=='response','RESPONSE_DIRECTION')
            uid=r['unit_id']; rid=r['request_id']; require(uid in observed and rid in attempts,'RESPONSE_ATTEMPT_JOIN')
            require(attempts[rid]['unit_id']==uid and all(r.get(k)==observed[uid].get(k) for k in META),'RESPONSE_UNIT_IDENTITY')
            flags=recover_flags(r,parse_decision,normalize)
            safe={**identity(r),'request_id':rid,'logical_role':'executor','purpose':'frozen diagnosis/action decision',
                'source_role_basis':'native llm_diagnose with advisory/judge/coalescing disabled; external _Work.executor',
                'raw_file':'model_io.jsonl','raw_line':line_number,'raw_line_sha256':hashlib.sha256(line).hexdigest(),**flags}
            response_rows.append(safe); response_by_request[rid].append(safe); response_by_unit[uid].append(safe)
    require(h.hexdigest()==EXPECTED['model_io'][0],'MODEL_IO_STREAM_HASH')
    attempt_rows=[]
    for rid,a in sorted(attempts.items()):
        rs=response_by_request.get(rid,[])
        status=('NO_RETURNED_RESPONSE_RECORDED_UNKNOWN_FORMAT' if not rs else
            'UNIQUE_RETURNED_RESPONSE' if len(rs)==1 else 'MULTIPLE_RESPONSE_ROWS_UNKNOWN_CONSUMED_IDENTITY')
        attempt_rows.append({**a,'returned_response_rows':len(rs),'response_identity_status':status,
            **{key:rs[0].get(key) if len(rs)==1 else None for key in FLAGS}})
    task_rows=[]
    for uid,o in sorted(observed.items()):
        rs=response_by_unit.get(uid,[])
        status=('NO_RESPONSE_UNKNOWN_NOT_INVALID_JSON' if not rs else
            'UNIQUE_RETURNED_RESPONSE_FORMAT_RECOVERED' if len(rs)==1 else 'MULTIPLE_RESPONSES_FINAL_CONSUMED_IDENTITY_UNKNOWN')
        task_rows.append({**o,'returned_response_rows':len(rs),'response_identity_status':status,
            'unique_request_id':rs[0]['request_id'] if len(rs)==1 else None,
            **{key:rs[0].get(key) if len(rs)==1 else None for key in FLAGS}})
    response_fields=[*META,'request_id','logical_role','purpose','source_role_basis','raw_file','raw_line','raw_line_sha256',
        *FLAGS,'schema_applicable','parser_error_type','json_decoder_error_type','returned_text_utf8_bytes',
        'returned_text_sha256','normalization_removed_optional_fence','recovery_status','runtime_empty_from_null_or_no_choices']
    write_csv(args.out/'response_format_flags.csv',response_rows,response_fields)
    groups=defaultdict(lambda:{'responses':[],'attempts':[],'tasks':[]})
    def group_key(r): return tuple(r.get(k) for k in ['phase','arm','model_id','topology','processes'])
    for r in response_rows: groups[group_key(r)]['responses'].append(r)
    for r in attempt_rows: groups[group_key(r)]['attempts'].append(r)
    for r in task_rows: groups[group_key(r)]['tasks'].append(r)
    summaries=[]
    for key,items in sorted(groups.items(),key=lambda x:canonical(x[0])):
        summary=dict(zip(['phase','arm','model_id','topology','processes'],key))
        summary.update(planned_task_denominator=len(items['tasks']),actual_attempt_denominator=len(items['attempts']),
            actual_returned_response_row_denominator=len(items['responses']),
            no_response_attempts=sum(r['returned_response_rows']==0 for r in items['attempts']),
            no_response_tasks=sum(r['returned_response_rows']==0 for r in items['tasks']),
            no_response_failed_tasks=sum(r['returned_response_rows']==0 and r['success'] is False for r in items['tasks']),
            multiple_response_tasks=sum(r['returned_response_rows']>1 for r in items['tasks']))
        for field in FLAGS:
            for label,value in [('true',True),('false',False),('unknown',None)]:
                summary[field+'_response_'+label]=sum(r.get(field) is value for r in items['responses'])
                summary[field+'_unique_task_'+label]=sum(r.get(field) is value for r in items['tasks'])
        summaries.append(summary)
    write_csv(args.out/'summary.csv',summaries,list(summaries[0]))
    source_receipt={'schema_version':'original-response-format-schema-source-v1','bound_sources':{k:v for k,v in bindings.items() if k.endswith('_source')},
        'pure_parser':parser_receipt,'frozen_configuration_flags':config_flags,
        'role_schema':'Only executor diagnosis/action decision is active; no active planner/retriever/reviewer advisory or coalesced batch output schema.',
        'unused_generic_json_hint':'context._OUTPUT_CONTRACTS json decision/reason is not active: output_contract=none',
        'no_open_explanation_required_fields':'No original required open explanation/completeness schema is supplied; not invented here.',
        'labels_and_actions_are_not_correctness':'Membership acceptance is not oracle/diagnosis correctness; pair consistency and final expected-answer quality are not evaluated.'}
    products={p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(args.out.iterdir())}
    result={'schema_version':'original-response-format-recovery-v1','status':'RECOVERED_FROZEN_FORMAT_FACTS_NOT_NEW_QUALITY_SCORING',
        'study_id':STUDY,'adapter_source_sha256':sha(Path(__file__)),'input_manifest_sha256':sha(args.inputs),'bound_inputs':bindings,
        'coverage':{'planned_tasks':6504,'public_tasks':sum(r['phase']=='public_calibration' for r in task_rows),
            'formal_nonpublic_tasks':sum(r['phase']!='public_calibration' for r in task_rows),'actual_provider_attempts':len(attempt_rows),
            'actual_returned_response_rows':len(response_rows),'response_unique_request_ids':len(response_by_request),
            'skipped_nonresponse_rows_not_body_decoded':dict(skipped),'no_response_tasks':sum(r['returned_response_rows']==0 for r in task_rows),
            'no_response_failed_tasks':sum(r['returned_response_rows']==0 and r['success'] is False for r in task_rows),
            'no_response_attempts':sum(r['returned_response_rows']==0 for r in attempt_rows),
            'multiple_response_tasks':sum(r['returned_response_rows']>1 for r in task_rows)},
        'summary_by_phase_arm_model_topology':summaries,'products':products,
        'no_response_attempt_metadata':[r for r in attempt_rows if r['returned_response_rows']==0],
        'no_response_task_metadata':[r for r in task_rows if r['returned_response_rows']==0],
        'multiple_response_task_metadata':[r for r in task_rows if r['returned_response_rows']>1],
        'schema_source':source_receipt,'decoder_is_original_default_not_RFC_strict':True,
        'request_bodies_JSON_decoded':0,'response_or_request_text_or_headers_emitted':False,
        'parser_exception_messages_emitted':False,'only_exception_type_metadata_emitted':True,
        'new_model_API_system_action_or_scientific_statistics':False,'primary_or_existing_M002_projection_modified':False,
        'within_existing_B8_241_record_identity':False,'M002_open_explanation_semantic_completeness_closed':False,
        'limits':['Counts/flags describe frozen pure parsing and exact required fields, not diagnosis accuracy, explanation semantics or strict RFC JSON.',
            'No response is unknown format, never invalid JSON. Response/attempt/task denominators are separate; repeated responses do not create independent tasks.',
            'Multiple returned responses make final consumed task-response identity unknown; no first/last/favorable selection is applied.',
            'All6504 planned task rows remain, public144 is explicitly separated; no success-only analysis or new scoring.',
            'SDK model_dump content may reflect writer secret-substring redaction; no original HTTP wire-byte claim.',
            'This independent recovery appendix does not change original quality, primary effects/CI/p/Holm, B8identity, UNKNOWN1547 or RAG54 missing.']}
    write(args.out/'results.json',result)
    paragraphs=['# 原始模型响应格式分项恢复：独立补充',
        '本补充只重放冻结纯解析函数与已安装的二十四项字段词表，不调用模型、SDK或系统动作，不重新评分。',
        '原记录保存SDK响应对象；元数据投影没有响应正文，不能据此断言历史响应不可恢复。请求正文未作JSON解码，也不输出响应正文、请求、headers或密钥。',
        '本批配置关闭coalescing、advisory和judge，活跃输出契约为executor的diagnosis/action；通用decision/reason提示不适用。',
        '响应级分母为实际返回响应；attempt分母为已记录提供方尝试；任务级保留全部六千五百零四个单元并按phase、arm、模型和拓扑分列，public另列。无响应不等于无效JSON，多返回响应不任挑最终答案。',
        '原函数使用默认json.loads且允许可选代码围栏预处理；输出只称冻结解析器可解码、原必需字段存在或恰好齐全，不称RFC严格有效或解释语义完整。',
        '原函数仅检查diagnosis与action分别属于已安装集合；不新增二者成对正确性，也不按expected答案判JSON无效。',
        '精确计数见summary.csv及results.json，逐响应仅保存安全身份、原始行号／哈希和布尔／unknown标志；原质量与主统计不变。']
    (args.out/'append_summary.md').write_text('\n\n'.join(paragraphs)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'planned_tasks':6504,'actual_attempts':len(attempt_rows),
        'returned_response_rows':len(response_rows),'request_bodies_JSON_decoded':0},ensure_ascii=False))


if __name__=='__main__': main()
