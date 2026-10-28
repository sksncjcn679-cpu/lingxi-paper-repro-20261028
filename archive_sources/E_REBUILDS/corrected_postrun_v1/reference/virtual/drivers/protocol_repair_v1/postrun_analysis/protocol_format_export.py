"""Prepared lossless corrected-study format exports; never a scientific audit.

Authoritative nested JSONL stays untouched. CSV cells are typed JSON values. A presence
column distinguishes missing fields from explicit null. Parquet keeps safe scalar types
and uses canonical JSON for heterogeneous, nested or out-of-range integer columns.
Only execute after actual root-observed real/retrieval quiescence and terminal stages.
No scientific metric, estimate, CI, p value, multiplicity correction or score is computed.
The original exporter is copied, never imported or reconfigured through module globals.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import hashlib
import json
from itertools import zip_longest
from pathlib import Path
import re

STUDY='protocol_repair_20261020_v1'
OUT=Path('/evidence')/STUDY
FORMAL_FREEZE_SHA256='2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
PARENT_SOURCE_SHA256='d60acb82bc0859b86acdb8dc5ae667fd49f4339cfef27d75f4f38d86cd937939'
TERMINAL_STATUSES={'COMPLETED','FAILED','UNSUPPORTED','NON_COMPARABLE','INCOMPLETE','INCOMPLETE_BUDGET_STOP'}
TABLES=['task_runs','provider_calls','messages','states','memory_events','resource_samples']
COMMON=['run_id','unit_id','phase','task_id','chain_id','domain','difficulty','arm','repeat','model',
        'model_id','topology','processes','source_sha256','task_sha256','seed','model_evidence']
PRESENT='__source_present_fields'


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def json_type(value):
    if value is None:return 'null'
    if isinstance(value,bool):return 'boolean'
    if isinstance(value,int):return 'integer'
    if isinstance(value,float):return 'number'
    if isinstance(value,str):return 'string'
    if isinstance(value,list):return 'array'
    if isinstance(value,dict):return 'object'
    raise TypeError(type(value).__name__)


def merge(schema,value):
    kind=json_type(value)
    schema.setdefault('observed_types',set()).add(kind)
    if kind=='object':
        props=schema.setdefault('properties',{})
        for key,item in value.items():merge(props.setdefault(key,{}),item)
        schema['object_observations']=schema.get('object_observations',0)+1
        counts=schema.setdefault('presence_counts',Counter());counts.update(value.keys())
    elif kind=='array':
        for item in value:merge(schema.setdefault('items',{}),item)


def finalized(schema):
    kinds=sorted(schema.get('observed_types',[]))
    result={'type':kinds} if kinds else {}
    if 'properties' in schema:
        result['properties']={key:finalized(value) for key,value in sorted(schema['properties'].items())}
        count=schema.get('object_observations',0)
        result['required']=sorted(key for key,n in schema['presence_counts'].items() if n==count)
        result['additionalProperties']=False
        result['x-observed-object-count']=count
    if 'items' in schema:result['items']=finalized(schema['items'])
    return result


def read_rows(path):
    def reject_constant(value):
        raise ValueError('non-finite numeric literal is not standard JSON: '+value)
    with path.open(encoding='utf-8') as stream:
        for index,line in enumerate(stream,1):
            if not line.strip():continue
            try:yield json.loads(line,parse_constant=reject_constant)
            except ValueError as error:raise RuntimeError(f'invalid or incomplete standard JSONL record at {path.name}:{index}') from error


def hash_file(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while data:=stream.read(1024*1024):digest.update(data)
    return digest.hexdigest()


def verify_readback(source,csv_path,parquet_path,encoded):
    import pyarrow.parquet as pq
    csv.field_size_limit(max(csv.field_size_limit(),16*1024*1024))
    def decoded_parquet():
        for batch in pq.ParquetFile(parquet_path).iter_batches(batch_size=4000):
            for row in batch.to_pylist():
                present=json.loads(row.pop(PRESENT))
                yield {key:json.loads(row[key]) if key in encoded and row[key] is not None else row[key]
                       for key in present}
    hashes=[hashlib.sha256() for _ in range(3)]
    count=0;missing=object()
    with csv_path.open(newline='',encoding='utf-8-sig') as stream:
        csv_rows=csv.DictReader(stream)
        def decoded_csv():
            for row in csv_rows:
                present=json.loads(row.pop(PRESENT))
                yield {key:json.loads(row[key]) for key in present}
        for index,triple in enumerate(zip_longest(read_rows(source),decoded_csv(),decoded_parquet(),fillvalue=missing),1):
            if any(row is missing for row in triple):raise RuntimeError(f'{source.name}: export row-count mismatch at row {index}')
            serialized=[canonical(row).encode() for row in triple]
            if serialized[0]!=serialized[1] or serialized[0]!=serialized[2]:
                raise RuntimeError(f'{source.name}: export value/type/presence mismatch at row {index}')
            for digest,value in zip(hashes,serialized):digest.update(value+b'\n')
            count+=1
    return {'rows':count,'source_decoded_sha256':hashes[0].hexdigest(),
        'csv_reconstructed_sha256':hashes[1].hexdigest(),'parquet_reconstructed_sha256':hashes[2].hexdigest(),
        'status':'PASS','scope':'every row canonical value/type/field presence, including missing vs null vs empty string'}


def export_table(name,destination,study_out):
    import pyarrow as pa
    import pyarrow.parquet as pq
    source=study_out/'raw'/f'{name}.jsonl'
    if not source.exists():
        return {'table':name,'status':'INCOMPLETE','reason':'required raw table missing'}
    initial_hash=hash_file(source)
    schema={};count=0;types={};large_integer_columns=set()
    for row in read_rows(source):
        if PRESENT in row:raise RuntimeError('reserved source-presence column collides with raw data')
        merge(schema,row);count+=1
        for key,value in row.items():
            types.setdefault(key,set()).add(json_type(value))
            if type(value) is int and not -(2**63)<=value<2**63:large_integer_columns.add(key)
    if not count:return {'table':name,'status':'INCOMPLETE','reason':'required raw table has no rows; no invalid empty schema generated'}
    columns=[key for key in COMMON if key in types]+sorted(set(types)-set(COMMON))
    arrow_fields=[];encoded=set()
    for column in columns:
        kinds=types[column]-{'null'}
        if column in large_integer_columns:dtype=pa.string();encoded.add(column)
        elif not kinds:dtype=pa.null()
        elif kinds=={'integer'}:dtype=pa.int64()
        elif kinds=={'number'}:dtype=pa.float64()
        elif kinds<= {'boolean'}:dtype=pa.bool_()
        elif kinds<= {'string'}:dtype=pa.string()
        else:dtype=pa.string();encoded.add(column)
        arrow_fields.append(pa.field(column,dtype,nullable=True))
    arrow_fields.append(pa.field(PRESENT,pa.string(),nullable=False))
    arrow_schema=pa.schema(arrow_fields,metadata={b'authoritative_source':str(source).encode(),
        b'nested_cells':b'canonical JSON strings; reserved presence column distinguishes absent from null',
        b'raw_sha256':initial_hash.encode()})
    csv_path=destination/f'{name}.csv';parquet_path=destination/f'{name}.parquet'
    if csv_path.exists() or parquet_path.exists():raise RuntimeError('choose new export directory; existing exports are immutable')
    with csv_path.open('x',newline='',encoding='utf-8-sig') as stream,pq.ParquetWriter(parquet_path,arrow_schema,compression='zstd') as writer:
        csv_writer=csv.DictWriter(stream,fieldnames=[*columns,PRESENT]);csv_writer.writeheader()
        batch=[]
        for row in read_rows(source):
            item={column:(canonical(row[column])
                  if column in encoded and row.get(column) is not None else row.get(column)) for column in columns}
            item[PRESENT]=canonical(sorted(row))
            csv_writer.writerow({**{column:canonical(row.get(column)) for column in columns},PRESENT:item[PRESENT]})
            batch.append(item)
            if len(batch)>=4000:
                writer.write_table(pa.Table.from_pylist(batch,schema=arrow_schema));batch=[]
        if batch:writer.write_table(pa.Table.from_pylist(batch,schema=arrow_schema))
    if hash_file(source)!=initial_hash:raise RuntimeError('raw source changed during export; export is not certified')
    readback=verify_readback(source,csv_path,parquet_path,encoded)
    if readback['rows']!=count:raise RuntimeError('readback count differs from initial raw table count')
    if hash_file(source)!=initial_hash:raise RuntimeError('raw source changed during independent readback')
    observed={'$schema':'https://json-schema.org/draft/2020-12/schema',
        '$id':f'urn:lingxi:{STUDY}:{name}',
        '$comment':'observed complete authoritative raw JSONL schema; conditional event fields remain optional; format/row/type/presence only, not semantic or scientific metric certification',
        **finalized(schema)}
    record={'schema_version':1,'study_id':STUDY,'table':name,'source':str(source),'source_sha256':initial_hash,
        'rows':count,'raw_json_schema_file':f'{name}.schema.json','column_storage':{field.name:str(field.type) for field in arrow_schema},
        'canonical_json_parquet_columns':sorted(encoded),
        'csv_encoding':'EVERY cell is one JSON value; strings include JSON quotes; null is literal null; missing distinguished by presence column',
        'source_presence_column':PRESENT,'null_policy':'unknown/not applicable remains null; missing vs null vs empty preserved; no zero replacement',
        'authority_notes':{
            'provider_cached_input_tokens':'preserve each recorded value verbatim; unknown raw None remains null and an absent field remains absent; explicit provider usage_raw cache details alone support cache attribution; never infer zero, cache hit or cache miss from unknown usage',
            'provider_wait_sec':'client SDK call elapsed time; provider queue latency and pure network RTT are not independently visible',
            'unobserved_resources':'VM host load, provider queue latency and PSS stay unknown when absent or null; no inferred columns or replacement values are created',
            'source_format':'CSV cells are JSON values; parse cells and presence column to reconstruct; do not interpret JSON string quotes as business data'},
        'declared_semantics':{
            'task_runs':'one frozen task/arm/repeat/model/topology unit; all terminal failures retained',
            'provider_calls':'one actual provider attempt; usage_raw is authoritative, HTTP/auth headers omitted',
            'messages':'one native Channel message; tokenizer proxy independent from provider usage; ACK may be deferred to task barrier',
            'states':'native produce/read events; reference object has encoded/logical/materialized bytes; cleanup linked by run_id to terminal SHM/worker audit',
            'memory_events':'native write/retrieve/adopt/feedback events; candidate/adoption/feedback are distinct, no causal claim from adopted success',
            'resource_samples':'200ms live process-group samples; RSS sum counts shared pages per process, not PSS; missed short-lived peaks possible'}[name]}
    (destination/f'{name}.schema.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2))
    (destination/f'{name}.storage.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    actual_count=pq.ParquetFile(parquet_path).metadata.num_rows
    if actual_count!=count:raise RuntimeError('Parquet export row-count mismatch')
    return {'table':name,'status':'PASS','rows':count,'raw_sha256':initial_hash,
        'csv_sha256':hash_file(csv_path),'parquet_sha256':hash_file(parquet_path),
        'readback':readback,'schema_status':'standard Draft2020-12 complete observed schema; semantic/measurement/scientific validation outside scope'}


def lifecycle_gate(out,quiescence_receipt):
    # Small lifecycle metadata only. No plan or raw is opened before this gate.
    execution_path=out/'formal_execution_v1/execution.json'
    execution=json.loads(execution_path.read_text(encoding='utf-8'))
    stages=execution.get('stages') or []
    if (execution.get('status')!='REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT'
            or not execution.get('finished_utc') or execution.get('supervised_child_observed_running') is True
            or [stage.get('name') for stage in stages]!=['main','native_remaining','external']
            or any(stage.get('status')!='TERMINAL' or type(stage.get('exit_code')) is not int
                   or stage['exit_code']!=0 or not stage.get('finished_utc') for stage in stages)):
        raise RuntimeError('all three actual formal stages must be terminal exit0 with finished timestamps')
    public_path=out/'public_pilot_v1/execution.json'
    public=json.loads(public_path.read_text(encoding='utf-8'))
    if not public.get('finished_utc') or public.get('status')=='RUNNING':
        raise RuntimeError('public lifecycle must also be terminal')
    receipt_path=quiescence_receipt.resolve()
    if not receipt_path.is_relative_to(out/'analysis') or receipt_path.suffix!='.json':
        raise RuntimeError('actual root quiescence receipt must be JSON inside this study analysis tree')
    quiet=json.loads(receipt_path.read_text(encoding='utf-8'))
    if (quiet.get('study_id')!=STUDY or quiet.get('status')!='ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING'
            or not quiet.get('verified_utc') or quiet.get('active_real_timing_processes')!=[]
            or quiet.get('active_retrieval_timing_processes')!=[]):
        raise RuntimeError('actual root process-window quiescence has not been documented')
    verified=datetime.fromisoformat(quiet['verified_utc'].replace('Z','+00:00'))
    finished=datetime.fromisoformat(execution['finished_utc'].replace('Z','+00:00'))
    if verified.tzinfo is None or finished.tzinfo is None or verified<finished or verified>datetime.now(timezone.utc):
        raise RuntimeError('quiescence must be a timezone-aware actual observation after formal finish')
    return {'execution':execution,'execution_sha256':hash_file(execution_path),
        'public_execution_sha256':hash_file(public_path),'quiescence_receipt':str(receipt_path),
        'quiescence_receipt_sha256':hash_file(receipt_path)}


def acquire_readonly_execution_locks(out,stack):
    # Existing Linux locks are held throughout raw export/readback; never create/write them.
    import fcntl
    for name in ('formal_sequence_execution.lock','real_provider_execution.lock'):
        stream=stack.enter_context((out/'manifests'/name).open('r'))
        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)


def frozen_plan_terminal_gate(out,lifecycle):
    # Invoked only under both execution locks and after actual lifecycle/quiescence.
    freeze_path=out/'manifests/formal-freeze.json'
    if hash_file(freeze_path)!=FORMAL_FREEZE_SHA256:
        raise RuntimeError('original independently recorded formal-freeze bytes changed')
    freeze=json.loads(freeze_path.read_text(encoding='utf-8'))
    if freeze.get('study_id')!=STUDY or lifecycle['execution'].get('formal_freeze_sha256')!=FORMAL_FREEZE_SHA256:
        raise RuntimeError('study or actual execution freeze identity mismatch')
    plan_path=out/'manifests/formal_plan.json'
    expected_plan_sha=freeze['files'].get(f'/evidence/{STUDY}/manifests/formal_plan.json')
    if not expected_plan_sha or hash_file(plan_path)!=expected_plan_sha:
        raise RuntimeError('actual plan must match the original formal-freeze binding')
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    planned={unit['run_id'] for unit in plan}
    if (len(plan)!=6504 or len(planned)!=6504 or any(type(rid) is not str or not rid for rid in planned)
            or hash_file(plan_path)!=expected_plan_sha):
        raise RuntimeError('original 6504 unique planned IDs required')
    source=out/'raw/task_runs.jsonl';initial_hash=hash_file(source)
    terminal_ids=set();count=0
    for row in read_rows(source):
        rid=row.get('run_id')
        if type(rid) is not str or rid not in planned or rid in terminal_ids:
            raise RuntimeError('unexpected, malformed or duplicate terminal ID')
        if row.get('status') not in TERMINAL_STATUSES:
            raise RuntimeError('task_runs contains a record without a recognized terminal status; no row is repaired or dropped')
        terminal_ids.add(rid);count+=1
    if count!=6504 or terminal_ids!=planned or hash_file(source)!=initial_hash:
        raise RuntimeError('stable complete terminal table must exactly match all 6504 planned IDs')
    return {'formal_freeze_sha256':FORMAL_FREEZE_SHA256,'formal_plan_sha256':expected_plan_sha,
        'planned_ids':len(planned),'terminal_ids':count,'task_runs_raw_sha256':initial_hash}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-out',type=Path,required=True,help=f'exact corrected study directory, normally {OUT}')
    parser.add_argument('--quiescence-receipt',type=Path,required=True,
        help='root receipt from actual process observation after termination; never synthesize from planned timing')
    parser.add_argument('--version',default='protocol_format_v1')
    args=parser.parse_args();out=args.study_out.resolve()
    if out.name!=STUDY or re.fullmatch(r'protocol_format_v[1-9][0-9]*',args.version) is None:
        parser.error('only the exact corrected study and fresh protocol_format_vN version are allowed')
    destination=out/'exports'/args.version
    if (out/'exports').resolve()!=out/'exports':
        parser.error('exports must be the direct study exports directory; symlink redirection is forbidden')
    if destination.exists():parser.error('exports already exist; retain them and choose a new version')
    with ExitStack() as stack:
        lifecycle=lifecycle_gate(out,args.quiescence_receipt)
        acquire_readonly_execution_locks(out,stack)
        closure=frozen_plan_terminal_gate(out,lifecycle)
        destination.mkdir(parents=True)
        results=[export_table(name,destination,out) for name in TABLES]
        for result in results:
            if result.get('status')=='PASS' and hash_file(out/'raw'/f"{result['table']}.jsonl")!=result['raw_sha256']:
                raise RuntimeError('raw source changed across the six-table export/readback batch')
        task_result=next(result for result in results if result['table']=='task_runs')
        if task_result.get('raw_sha256')!=closure['task_runs_raw_sha256']:
            raise RuntimeError('terminal table changed after the exact 6504-ID closure gate')
        report={'status':'FORMAT_ROW_TYPE_PRESENCE_PASS' if all(r['status']=='PASS' for r in results) else 'INCOMPLETE',
            'study_id':STUDY,'authoritative_nested_format':'unchanged original raw/*.jsonl','exports':results,
            'lifecycle_gate':lifecycle,'closure_gate':closure,'parent_exporter_source_sha256':PARENT_SOURCE_SHA256,
            'exporter_source_sha256':hash_file(Path(__file__)),
            'scope':'only lossless format/row/type/field-presence readback; observed schemas do not certify semantic coverage or scientific metrics; no scoring or statistics recomputed'}
        (destination/'export_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'status':report['status'],'tables':len(results),'rows':{r['table']:r.get('rows') for r in results}}))
        return 0 if report['status']=='FORMAT_ROW_TYPE_PRESENCE_PASS' else 2


if __name__=='__main__':raise SystemExit(main())
