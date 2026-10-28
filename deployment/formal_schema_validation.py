"""Independent postrun Draft2020-12 / lossless-readback audit. Never calls models.

Authoring this module is not execution. The 115-item authority dictionary is a
separate semantic contract: observed schemas cannot certify its data coverage.
Use an isolated analysis environment; never install into the frozen environment.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
from importlib.metadata import version
from itertools import zip_longest
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys

DEFAULT_OUT=Path('/evidence/formal_20261019')
DRAFT='https://json-schema.org/draft/2020-12/schema'
CORE=('task_runs','provider_calls','messages','states','memory_events','resource_samples')
DERIVED_USAGE=('provider_usage_per_attempt','provider_usage_per_task')
DERIVED_QUALITY=('quality_per_unit','quality_group_summary','quality_failures_timeouts','quality_request_identity')
PRESENT='__source_present_fields'
TERMINAL={'COMPLETED','FAILED','UNSUPPORTED','NON_COMPARABLE','INCOMPLETE','INCOMPLETE_BUDGET_STOP'}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data:=stream.read(1024*1024):h.update(data)
    return h.hexdigest()


def strict_loads(text):
    def reject(name):raise ValueError('non-standard JSON constant')
    value=json.loads(text,parse_constant=reject)
    # Also reject floating-point overflow such as 1e999, without printing values.
    canonical(value)
    return value


def safe_pointer(parts):
    def segment(value):
        if isinstance(value,int):return str(value)
        value=str(value)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.-]{0,100}',value):
            return 'key_sha256_'+hashlib.sha256(value.encode()).hexdigest()
        return value.replace('~','~0').replace('/','~1')
    return '/'+('/'.join(segment(p) for p in parts)) if parts else ''


def structural_schema(table):
    # Independently declared from frozen source emission, not inferred from data.
    strings=('run_id','unit_id','phase','task_id','chain_id','domain','difficulty','arm','model',
             'model_id','topology','source_sha256','task_sha256','model_evidence')
    props={name:{'type':'string','minLength':1} for name in strings}
    props.update(repeat={'type':'integer','minimum':0},seed={'type':'integer'},processes={'type':'boolean'})
    required=[*strings,'repeat','seed','processes']
    if table=='task_runs':
        props.update(status={'enum':sorted(TERMINAL)},success={'type':'boolean'},
            wall_sec={'type':'number','minimum':0},start_utc={'type':'string','format':'date-time'},
            end_utc={'type':'string','format':'date-time'},provider_attempts={'type':'integer','minimum':0,'maximum':3})
        required+=['status','success','wall_sec','start_utc','end_utc','provider_attempts']
    elif table=='provider_calls':
        props.update(request_id={'type':'string','minLength':1},attempt={'type':'integer','minimum':1,'maximum':3},
            status={'enum':['OK','ERROR']},requested_model={'type':'string','minLength':1},
            usage_raw={'type':['object','null']},usage_source={'type':'string'},
            wall_sec={'type':'number','minimum':0},start_utc={'type':'string','format':'date-time'},
            end_utc={'type':'string','format':'date-time'})
        required+=['request_id','attempt','status','requested_model','usage_raw','usage_source','wall_sec','start_utc','end_utc']
    elif table=='messages':
        for name in ('message_id','source','target','action','codec'):props[name]={'type':'string'}
        props['logical_message']={'type':'object'}
        required+=['timestamp','message_id','source','target','action','codec','logical_message']
    elif table=='states':
        props.update(event={'type':'string'},timestamp={'type':'string','format':'date-time'},pid={'type':'integer'},
                     uri={'type':['string','null']},transport={'type':'string'})
        required+=['event','timestamp','pid','uri','transport']
    elif table=='memory_events':
        props['event']={'type':'string'};required+=['event']
        # The frozen feedback-suppressed event intentionally has no timestamp.
    elif table=='resource_samples':
        props.update(processes={'type':'array','items':{'type':'object','required':['pid','role','rss','cpu_sec','fds','io'],
            'properties':{'pid':{'type':'integer'},'role':{'type':'string'},'rss':{'type':'integer','minimum':0},
                          'cpu_sec':{'type':'number','minimum':0},'fds':{'type':'integer','minimum':0}}}},
            timestamp={'type':'string','format':'date-time'},monotonic_ns={'type':'integer','minimum':0},
            sample_interval_sec={'type':'number','exclusiveMinimum':0})
        required+=['timestamp','monotonic_ns','sample_interval_sec']
    return {'$schema':DRAFT,'type':'object','properties':props,'required':required,
            'additionalProperties':True,'$comment':'Independent minimum structural contract; not all115 semantic/join coverage.'}


def merge(schema,value):
    kind='null' if value is None else 'boolean' if isinstance(value,bool) else 'integer' if isinstance(value,int) else \
         'number' if isinstance(value,float) else 'string' if isinstance(value,str) else 'array' if isinstance(value,list) else 'object'
    schema.setdefault('kinds',set()).add(kind)
    if isinstance(value,dict):
        schema['objects']=schema.get('objects',0)+1
        schema.setdefault('presence',Counter()).update(value.keys())
        for key,item in value.items():merge(schema.setdefault('properties',{}).setdefault(key,{}),item)
    elif isinstance(value,list):
        for item in value:merge(schema.setdefault('items',{}),item)


def finalize(schema):
    result={'type':sorted(schema['kinds'])} if schema.get('kinds') else {}
    if 'properties' in schema:
        result.update(properties={key:finalize(value) for key,value in sorted(schema['properties'].items())},
            required=sorted(key for key,count in schema['presence'].items() if count==schema['objects']),additionalProperties=False)
    if 'items' in schema:result['items']=finalize(schema['items'])
    return result


class Audit:
    def __init__(self,destination):
        self.destination=destination;self.sources={};self.error_count=0;self.invalid_lines=0
        self.failures=(destination/'validation_failures.jsonl').open('x',encoding='utf-8')

    def track(self,path):
        path=Path(path)
        if path not in self.sources:self.sources[path]=digest(path)
        return path

    def failure(self,code,path=None,line=None,row_sha256=None,**safe):
        self.error_count+=1
        self.failures.write(canonical({'code':code,'file':str(path) if path else None,'physical_line':line,
                                      'raw_line_sha256':row_sha256,**safe})+'\n')

    def rows(self,path):
        path=self.track(path)
        with path.open('rb') as stream:
            for line,data in enumerate(stream,1):
                if not data.strip():continue
                h=hashlib.sha256(data).hexdigest()
                try:value=strict_loads(data.decode('utf-8'))
                except (ValueError,UnicodeError,OverflowError,RecursionError) as error:
                    self.invalid_lines+=1;self.failure('INVALID_STANDARD_JSONL',path,line,h,error_type=type(error).__name__);continue
                yield line,h,value

    def load(self,path):
        return strict_loads(self.track(path).read_text(encoding='utf-8'))

    def validator(self,schema,path):
        from jsonschema import Draft202012Validator,FormatChecker
        from referencing import Registry
        try:Draft202012Validator.check_schema(schema)
        except Exception as error:
            self.failure('INVALID_DRAFT202012_SCHEMA',path,keyword=str(getattr(error,'validator','unknown')),
                schema_path=safe_pointer(getattr(error,'absolute_schema_path',[])),
                schema_sha256=hashlib.sha256(canonical(schema).encode()).hexdigest(),error_type=type(error).__name__)
            raise
        if schema.get('$schema')!=DRAFT:raise ValueError('explicit Draft2020-12 schema required')
        formats=set()
        def inspect(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key in ('$ref','$dynamicRef') and isinstance(item,str) and not item.startswith('#'):
                        raise ValueError('external schema reference forbidden; no network resolver')
                    if key=='format' and isinstance(item,str):formats.add(item)
                    inspect(item)
            elif isinstance(value,list):
                for item in value:inspect(item)
        inspect(schema)
        checker=FormatChecker()
        if formats-set(checker.checkers):raise ValueError('schema requests unavailable format checker')
        def no_retrieve(uri):raise ValueError('external schema retrieval disabled')
        return Draft202012Validator(schema,format_checker=checker,registry=Registry(retrieve=no_retrieve))

    def validate_rows(self,path,schema,schema_path,independent=None):
        validators=[('export_or_observed',self.validator(schema,schema_path))]
        if independent:validators.append(('independent_minimum',self.validator(independent,'independent_contract')))
        count=failed=0
        for line,h,row in self.rows(path):
            count+=1;bad=False
            for scope,validator in validators:
                for error in validator.iter_errors(row):
                    bad=True;self.failure('SCHEMA_ROW_VALIDATION_FAILED',path,line,h,scope=scope,
                        keyword=str(error.validator),instance_path=safe_pointer(error.absolute_path),
                        schema_path=safe_pointer(error.absolute_schema_path),schema_file=str(schema_path),
                        schema_sha256=hashlib.sha256(canonical(validator.schema).encode()).hexdigest())
            failed+=bad
        return {'jsonl':str(path),'schema':str(schema_path),'valid_json_rows':count,'failed_schema_rows':failed,
                'independent_minimum_contract':bool(independent),'scope':'row format/type validation; not semantic115 coverage'}

    def readback(self,source,csv_path,parquet_path,storage=None):
        import pyarrow.parquet as pq
        for path in (csv_path,parquet_path):self.track(path)
        encoded=set(storage.get('canonical_json_parquet_columns',[])) if storage else set()
        def decoded_csv():
            with csv_path.open(newline='',encoding='utf-8-sig') as stream:
                reader=csv.DictReader(stream)
                for row in reader:
                    keys=strict_loads(row[PRESENT]) if storage else list(row)
                    yield {key:strict_loads(row[key]) for key in keys}
        def decoded_parquet():
            for batch in pq.ParquetFile(parquet_path).iter_batches(batch_size=1000):
                for row in batch.to_pylist():
                    if storage:
                        keys=strict_loads(row.pop(PRESENT))
                        yield {key:strict_loads(row[key]) if key in encoded and row[key] is not None else row[key] for key in keys}
                    else:yield row
        # Select exact capacity from existing CSV bytes; no arbitrary field truncation.
        csv.field_size_limit(max(csv.field_size_limit(),csv_path.stat().st_size+1))
        hashes=[hashlib.sha256() for _ in range(3)];missing=object();count=0;bad=0
        source_rows=((line,h,row) for line,h,row in self.rows(source))
        for ordinal,(original,cs,pqrow) in enumerate(zip_longest(source_rows,decoded_csv(),decoded_parquet(),fillvalue=missing),1):
            count+=1
            if original is missing or cs is missing or pqrow is missing:
                bad+=1;self.failure('READBACK_ROW_COUNT_MISMATCH',source,None,None,export_ordinal=ordinal);continue
            line,h,row=original
            values=[canonical(value).encode() for value in (row,cs,pqrow)]
            if values[0]!=values[1] or values[0]!=values[2]:
                bad+=1;self.failure('READBACK_VALUE_TYPE_PRESENCE_MISMATCH',source,line,h,export_ordinal=ordinal)
            for target,value in zip(hashes,values):target.update(value+b'\n')
        return {'rows':count,'mismatch_rows':bad,'canonical_sha256':dict(zip(('jsonl','csv','parquet'),(h.hexdigest() for h in hashes))),
                'status':'PASS' if bad==0 else 'FAIL'}


def required_ledger(authority):
    fields=authority.get('fields',[])
    if len(fields)!=115 or len({f['id'] for f in fields})!=115:raise ValueError('exact115 unique required field groups required')
    rows=[]
    for field in fields:
        for system in ('native','external'):
            side=field[system];unknown=side['coverage_status']=='UNKNOWN_UNMEASURED'
            rows.append({'field_id':field['id'],'system':system,'protocol_ref':field['protocol_ref'],
                'table_group':field['table_group'],'nullable':field['nullable'],
                'declared_source_status':side['coverage_status'],'declared_sources':side['sources'],
                'join_contract':side['join_contract'],'applicability':side['applicability'],
                'semantic_data_coverage_status':'EXPLICIT_UNKNOWN_RETAINED' if unknown else 'NOT_CERTIFIED_REQUIRES_FIELD_OR_JOIN_AUDIT',
                'formal_coverage_verified':False,'schema_pass_implies_field_coverage':False})
    return rows


def contained_path(out,relative,directory):
    if not isinstance(relative,str) or not relative or '\\' in relative:
        raise ValueError('selection path must be a nonempty canonical relative POSIX path')
    posix=PurePosixPath(relative);windows=PureWindowsPath(relative)
    if posix.is_absolute() or windows.drive or any(part in ('.','..') for part in relative.split('/')) or posix.as_posix()!=relative:
        raise ValueError('selection path must stay strictly below OUT')
    path=(out/relative).resolve(strict=True)
    if path==out or not path.is_relative_to(out) or (not path.is_dir() if directory else not path.is_file()):
        raise ValueError('selected path is missing, outside OUT or has the wrong kind')
    return path


def selected_raw_roots(out,rules,record):
    binding=rules['raw_scope_selection']
    selection_path=contained_path(out,binding['file'],False)
    if digest(selection_path)!=binding['sha256']:
        raise ValueError('final artifact selection identity changed')
    selection=record.load(selection_path)
    relative=selection.get('authoritative_retrieval_root')
    if relative!=binding['authoritative_retrieval_root']:
        raise ValueError('authoritative retrieval root differs from versioned selection contract')
    selected=contained_path(out,relative,True)
    roots=[contained_path(out,'raw',True),contained_path(out,relative+'/raw',True)]
    if not roots[1].is_relative_to(selected) or roots[1]==selected or \
       roots[0]==roots[1] or roots[0].is_relative_to(roots[1]) or roots[1].is_relative_to(roots[0]):
        raise ValueError('raw roots must be distinct nonoverlapping descendants of their selected locations')
    expected=['raw',relative+'/raw']
    if binding.get('raw_roots')!=expected:
        raise ValueError('versioned raw root list differs from selected retrieval root')
    state_binding=rules['state_scope_selection']
    registration=contained_path(out,state_binding['registration'],False)
    if digest(registration)!=state_binding['registration_sha256']:
        raise ValueError('prospective state registration identity changed')
    state_rules=record.load(registration)
    state_relative=state_binding['relative_output_root']
    if state_rules.get('relative_output_root')!=state_relative:
        raise ValueError('state root differs from its prospective registration')
    state_root=contained_path(out,state_relative,True)
    prospective=contained_path(out,state_relative+'/prospective_registration.json',False)
    if digest(prospective)!=state_binding['registration_sha256']:
        raise ValueError('executed state input differs from registered input')
    record.track(prospective)
    execution_path=contained_path(out,state_relative+'/analysis/execution.json',False)
    execution=record.load(execution_path)
    control=record.load(contained_path(out,state_relative+'/manifest.json',False))
    if execution.get('status')!='COMPLETED' or execution.get('measured_rows')!=25200 or \
       execution.get('warmup_rows')!=2520 or execution.get('total_transfers')!=27720 or \
       control.get('status')!='COMPLETED' or control.get('execution_sha256')!=digest(execution_path):
        raise ValueError('state timing matrix is not closed')
    state_raw=contained_path(out,state_relative+'/raw',True)
    roots.append(state_raw)
    expected.append(state_relative+'/raw')
    archives=[]
    for archived in state_binding.get('archived_state_roots',[]):
        archive_relative=archived['relative_output_root']
        archive_registration=contained_path(out,archived['registration'],False)
        archive_rules=record.load(archive_registration)
        archive_prospective=contained_path(out,archive_relative+'/prospective_registration.json',False)
        if digest(archive_registration)!=archived['registration_sha256'] or \
           digest(archive_prospective)!=archived['registration_sha256'] or \
           archive_rules.get('relative_output_root')!=archive_relative:
            raise ValueError('archived state identity differs from its closed registration')
        record.track(archive_prospective)
        archive_execution_path=contained_path(out,archive_relative+'/analysis/execution.json',False)
        archive_execution=record.load(archive_execution_path)
        archive_control=record.load(contained_path(out,archive_relative+'/manifest.json',False))
        if archive_execution.get('status')!=archived['required_closed_status'] or \
           archive_control.get('status')!=archived['required_closed_status'] or \
           archive_control.get('execution_sha256')!=digest(archive_execution_path):
            raise ValueError('archival failed state process is not closed with retained evidence')
        roots.append(contained_path(out,archive_relative+'/raw',True))
        expected.append(archive_relative+'/raw')
        archives.append({'root':archive_relative,'status':archive_execution['status'],
                         'raw_schema_policy':'observed auxiliary archive; not complete state matrix'})
    if any(left==right or left.is_relative_to(right) or right.is_relative_to(left)
           for i,left in enumerate(roots) for right in roots[i+1:]):
        raise ValueError('selected raw roots overlap')
    if state_binding.get('all_raw_roots')!=expected:
        raise ValueError('selected and archival raw scope differs from its registered contract')
    return roots,{'file':str(selection_path),'sha256':digest(selection_path),
        'authoritative_retrieval_root':relative,'state_registration':str(registration),
        'state_registration_sha256':digest(registration),'state_root':str(state_root),
        'state_raw_root':str(state_raw),'archived_state_roots':archives,'raw_roots':expected}


def raw_inventory(out,roots):
    inventory={};paths=[];resolved_seen=set()
    for root in roots:
        selected=sorted(root.rglob('*.jsonl'))
        for path in selected:
            resolved=path.resolve(strict=True)
            if not resolved.is_file() or not resolved.is_relative_to(root) or not resolved.is_relative_to(out):
                raise ValueError('raw inventory path escaped its selected root or is not a file')
            if resolved in resolved_seen:raise ValueError('duplicate resolved file in selected raw inventory')
            resolved_seen.add(resolved)
        inventory[root.relative_to(out).as_posix()]=[path.relative_to(out).as_posix() for path in selected]
        paths.extend(selected)
    return inventory,sorted(paths)


def audit(out,core_exports,usage_exports,quality_exports,destination,contract=None,postrun_ready=None):
    out=Path(out).resolve(strict=True);destination=Path(destination)
    if destination.exists():raise ValueError('immutable new destination required')
    destination.mkdir(parents=True);record=Audit(destination)
    report={'status':'NOT_EXECUTED','created_utc':datetime.now(timezone.utc).isoformat(),'paid_api_calls':0,
        'raw_modified':False,'module_sha256':digest(__file__),'sensitive_values_in_errors':False,
        'all115_semantic_coverage_certified':False,'validations':[],'readback':[]}
    try:
        ready=Path(postrun_ready) if postrun_ready else out/'logs/mechanisms/formal_complete_sequence_v1.result.json'
        if not ready.is_file():
            report.update(status='GATE_REAL_PRESSURE_SEQUENCE_NOT_FINISHED');return report
        ready_result=record.load(ready)
        if ready_result.get('exit_code')!=0 or ready_result.get('timed_out'):
            report.update(status='GATE_REAL_PRESSURE_SEQUENCE_FAILED');return report
        try:
            report['dependency_versions']={name:version(name) for name in ('jsonschema','referencing','jsonschema-specifications','rpds-py','attrs','rfc3339-validator','six','pyarrow')}
            from jsonschema import Draft202012Validator
        except ImportError as error:
            report.update(status='DEPENDENCY_UNAVAILABLE_NOT_EXECUTED',dependency_error_type=type(error).__name__)
            return report
        contract=Path(contract) if contract else out/'analysis/schema_validation_contract_v3.json'
        rules=record.load(contract)
        if digest(__file__)!=rules['implementation_sha256']:raise ValueError('schema validator source identity changed')
        raw_roots,selection=selected_raw_roots(out,rules,record)
        report['raw_scope_selection']=selection
        if any(report['dependency_versions'].get(name)!=pin for name,pin in rules['dependency_plan']['pins'].items()):
            report.update(status='DEPENDENCY_VERSION_MISMATCH_NOT_EXECUTED');return report
        authority_path=out/'analysis/field_authority_and_join_contract.json'
        if digest(authority_path)!=rules['required_field_contract_sha256']:raise ValueError('115-item authority contract identity changed')
        authority=record.load(authority_path);ledger=required_ledger(authority)
        (destination/'required_field_coverage_115.json').write_text(canonical({'required_field_groups':115,'system_entries':230,
            'status':'NOT_CERTIFIED_BY_SCHEMA_VALIDATION','entries':ledger}),encoding='utf-8')
        plan=record.load(out/'manifests/formal_plan.json');ids=[u['run_id'] for u in plan]
        tasks={};duplicates=0
        for line,h,row in record.rows(out/'raw/task_runs.jsonl'):
            key=row.get('run_id')
            if key in tasks:duplicates+=1
            tasks[key]=row
        if len(ids)!=6504 or len(set(ids))!=6504 or duplicates or set(tasks)!=set(ids) or \
           any(r.get('status') not in TERMINAL or r.get('model_evidence')!='real_provider' for r in tasks.values()):
            report.update(status='GATE_INCOMPLETE_NO_TABLE_VALIDATION');return report
        if record.invalid_lines:report.update(status='GATE_INVALID_JSON_NO_TABLE_VALIDATION');return report
        report['gate']='6504 unique real-provider terminal records'
        core_exports=Path(core_exports);usage_exports=Path(usage_exports);quality_exports=Path(quality_exports)
        upstream=[];core_entries={}
        for directory,filename,allowed in (
            (core_exports,'export_audit.json',{'PASS'}),
            (usage_exports,'provider_usage_authority_audit.json',{'PASS_PRIMARY_USAGE_COMPLETE','COMPLETE_WITH_USAGE_UNKNOWNS'}),
            (quality_exports,'quality_appendix_audit.json',{'PASS_DESCRIPTIVE_APPENDIX'})):
            path=directory/filename;previous=record.load(path)
            upstream.append({'path':str(path),'sha256':digest(path),'status':previous.get('status')})
            if previous.get('status') not in allowed:record.failure('UPSTREAM_AUDIT_DID_NOT_PASS',path)
            if filename=='export_audit.json':
                core_entries={item['table']:item for item in previous.get('exports',[])}
            else:
                for table in previous.get('tables',[]):
                    for name,expected_hash in table.get('sha256',{}).items():
                        artifact=record.track(directory/name)
                        if digest(artifact)!=expected_hash:record.failure('UPSTREAM_DERIVED_ARTIFACT_HASH_MISMATCH',artifact)
        report['upstream_audits']=upstream
        before_inventory,raw_paths=raw_inventory(out,raw_roots)
        report.update(raw_inventory_files=len(raw_paths),raw_inventory_before=before_inventory,
            raw_inventory_files_by_root={root:len(paths) for root,paths in before_inventory.items()})
        state_rules=record.load(selection['state_registration'])
        # Every raw JSONL is validated. Extra raw ledgers receive a disclosed observed
        # schema, never a fabricated independent protocol or required-field contract.
        schema_dir=destination/'supplemental_observed_schemas';schema_dir.mkdir()
        for path in raw_paths:
            table=path.stem;independent=None
            if path.parent==out/'raw' and table in CORE:
                schema_path=core_exports/(table+'.schema.json');schema=record.load(schema_path)
                independent=structural_schema(table)
                (destination/(table+'.minimum_contract.schema.json')).write_text(canonical(independent),encoding='utf-8')
            elif path.parent==Path(selection['state_raw_root']):
                match=re.fullmatch(r'(producer|child_stage|resources)_batch_[123]',table)
                if not match:raise ValueError('unregistered state raw table name')
                kind=match.group(1)
                schema_path=Path(selection['state_root'])/'analysis'/(kind+'.schema.json')
                schema=record.load(schema_path)
                if canonical(schema)!=canonical(state_rules['raw_schemas'][kind]):
                    raise ValueError('state declared schema differs from its prospective contract')
            else:
                observed={}
                for _,_,row in record.rows(path):merge(observed,row)
                schema={'$schema':DRAFT,'$comment':'Observed auxiliary raw shape only; no protocol coverage inference',**finalize(observed)}
                schema_path=schema_dir/(hashlib.sha256(str(path.relative_to(out)).encode()).hexdigest()+'.schema.json')
                schema_path.write_text(canonical(schema),encoding='utf-8')
            item=record.validate_rows(path,schema,schema_path,independent)
            if item['valid_json_rows']==0 and table in CORE:record.failure('EMPTY_REQUIRED_CORE_TABLE',path)
            report['validations'].append(item)
        for table in CORE:
            raw=out/'raw'/(table+'.jsonl')
            if raw not in raw_paths:record.failure('MISSING_REQUIRED_CORE_RAW_TABLE',raw);continue
            storage=record.load(core_exports/(table+'.storage.json'))
            if storage.get('source_sha256')!=digest(raw):record.failure('STORAGE_SOURCE_HASH_MISMATCH',raw)
            if storage.get('source_presence_column')!=PRESENT:record.failure('STORAGE_PRESENCE_CONTRACT_MISMATCH',raw)
            result=record.readback(raw,core_exports/(table+'.csv'),core_exports/(table+'.parquet'),storage)
            previous=core_entries.get(table,{})
            previous_readback=previous.get('readback',{})
            checks={'source_decoded_sha256':'jsonl','csv_reconstructed_sha256':'csv','parquet_reconstructed_sha256':'parquet'}
            if previous.get('status')!='PASS' or previous.get('raw_sha256')!=digest(raw) or previous_readback.get('status')!='PASS' or previous.get('rows')!=result['rows'] or \
               any(previous_readback.get(name)!=result['canonical_sha256'][key] for name,key in checks.items()):
                record.failure('UPSTREAM_CORE_READBACK_LINK_MISMATCH',raw)
            for suffix,key in (('csv','csv_sha256'),('parquet','parquet_sha256')):
                artifact=core_exports/(table+'.'+suffix)
                if previous.get(key)!=digest(artifact):record.failure('UPSTREAM_CORE_ARTIFACT_HASH_MISMATCH',artifact)
            report['readback'].append({'table':table,**result})
        for directory,expected in ((usage_exports,DERIVED_USAGE),(quality_exports,DERIVED_QUALITY)):
            actual=sorted(directory.rglob('*.jsonl'))
            for name in expected:
                if directory/(name+'.jsonl') not in actual:record.failure('MISSING_REQUIRED_DERIVED_TABLE',directory/(name+'.jsonl'))
            for path in actual:
                schema_path=path.with_suffix('.schema.json');schema=record.load(schema_path)
                report['validations'].append(record.validate_rows(path,schema,schema_path))
                report['readback'].append({'table':str(path),**record.readback(path,path.with_suffix('.csv'),path.with_suffix('.parquet'))})
        after_inventory,after_paths=raw_inventory(out,raw_roots)
        report['raw_inventory_after']=after_inventory
        if after_inventory!=before_inventory or after_paths!=raw_paths:
            record.failure('RAW_INVENTORY_CHANGED',out,selected_roots=selection['raw_roots'])
        for path,before in record.sources.items():
            if digest(path)!=before:record.failure('SOURCE_CHANGED_DURING_AUDIT',path)
        report.update(status='FAIL_SCHEMA_OR_READBACK' if record.error_count else 'PASS_SCHEMA_READBACK_SEMANTIC115_NOT_CERTIFIED',
            required_field_groups=115,required_field_system_entries=230,
            required_field_coverage_status='NOT_CERTIFIED_REQUIRES_SEPARATE_FIELD_AND_JOIN_AUDITS',
            scope='All JSONL under OUT/raw, selected retrieval raw, prospectively registered state raw and closed failed-state archives, plus requested derived JSONL rows validated; core6 also independent minimum structure and selected state rows use their declared prospective schemas. Observed schemas and full readback never prove115 semantic coverage.')
    except Exception as error:
        record.failure('AUDIT_EXECUTION_ERROR',error_type=type(error).__name__,
                       error_text_sha256=hashlib.sha256(str(error).encode()).hexdigest())
        report.update(status='ERROR_RETAINED_NO_CERTIFICATION',error_type=type(error).__name__)
    finally:
        record.failures.close();report.update(error_count=record.error_count,invalid_standard_json_lines=record.invalid_lines,
            input_sha256={str(path):h for path,h in record.sources.items()},
            failure_details_file='validation_failures.jsonl',failure_details_sha256=digest(destination/'validation_failures.jsonl'))
        (destination/'schema_validation_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',default=str(DEFAULT_OUT));parser.add_argument('--destination',required=True)
    parser.add_argument('--core-exports',required=True);parser.add_argument('--usage-exports',required=True);parser.add_argument('--quality-exports',required=True)
    parser.add_argument('--contract');parser.add_argument('--postrun-ready');args=parser.parse_args()
    result=audit(args.out,args.core_exports,args.usage_exports,args.quality_exports,args.destination,args.contract,args.postrun_ready)
    print(json.dumps({key:result.get(key) for key in ('status','error_count','required_field_coverage_status')}))
    raise SystemExit(0 if result['status']=='PASS_SCHEMA_READBACK_SEMANTIC115_NOT_CERTIFIED' else 2)
