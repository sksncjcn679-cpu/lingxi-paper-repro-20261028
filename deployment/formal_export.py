"""Lossless table exports and JSON schemas after all provider phases are terminal.

Authoritative nested JSONL stays untouched. CSV cells are typed JSON values. A presence
column distinguishes missing fields from explicit null. Parquet keeps safe scalar types
and uses canonical JSON for heterogeneous, nested or out-of-range integer columns.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import json
from itertools import zip_longest
from pathlib import Path

OUT=Path('/evidence/formal_20261019')
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


def export_table(name,destination):
    import pyarrow as pa
    import pyarrow.parquet as pq
    source=OUT/'raw'/f'{name}.jsonl'
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
        '$id':f'urn:lingxi:formal_20261019:{name}',
        '$comment':'observed complete authoritative raw JSONL schema; conditional event fields remain optional',
        **finalized(schema)}
    record={'schema_version':1,'table':name,'source':str(source),'source_sha256':initial_hash,
        'rows':count,'raw_json_schema_file':f'{name}.schema.json','column_storage':{field.name:str(field.type) for field in arrow_schema},
        'canonical_json_parquet_columns':sorted(encoded),
        'csv_encoding':'EVERY cell is one JSON value; strings include JSON quotes; null is literal null; missing distinguished by presence column',
        'source_presence_column':PRESENT,'null_policy':'unknown/not applicable remains null; missing vs null vs empty preserved; no zero replacement',
        'authority_notes':{
            'provider_cached_input_tokens':'raw task field is a runtime default/lower-bound counter and may be 0 when not reported; authoritative cache requires explicit usage_raw details, otherwise unknown',
            'provider_wait_sec':'client SDK call elapsed time; provider queue latency and pure network RTT are not independently visible',
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
        'readback':readback,'schema_status':'standard Draft2020-12 observed schema; validation run required separately'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--destination',default=str(OUT/'exports/v1'))
    args=parser.parse_args();destination=Path(args.destination)
    if destination.exists():raise RuntimeError('exports already exist; never overwrite')
    plan=json.loads((OUT/'manifests/formal_plan.json').read_text())
    planned={unit['run_id'] for unit in plan}
    task_ids=[row['run_id'] for row in read_rows(OUT/'raw/task_runs.jsonl')]
    if set(task_ids)!=planned or len(task_ids)!=len(planned):
        raise RuntimeError('all 6504 public+formal terminal units required before immutable export')
    destination.mkdir(parents=True)
    results=[export_table(name,destination) for name in TABLES]
    report={'status':'PASS' if all(r['status']=='PASS' for r in results) else 'INCOMPLETE',
        'authoritative_nested_format':'original raw/*.jsonl','exports':results,
        'scope':'format and row-count certification only; independent measurement/statistical audit required'}
    (destination/'export_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'status':report['status'],'tables':len(results),'rows':{r['table']:r.get('rows') for r in results}}))


if __name__=='__main__':main()
