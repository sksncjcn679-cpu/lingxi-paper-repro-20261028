"""Rebuild retrieval-v2 tables with the unchanged frozen retrieval analyzer."""
from __future__ import annotations
import argparse
import collections
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path('/evidence/formal_20261019'))
    parser.add_argument('--destination',type=Path)
    args=parser.parse_args();destination=(args.destination or args.out/'retrieval_input_correction_v2').resolve()
    target=destination/'analysis/retrieval_v2_analysis.json'
    if target.exists():raise RuntimeError('analysis already executed; never replace reviewed outputs')
    expected={(size,build,mode,q['query_id']) for q in map(json.loads,(destination/'corpus/queries.jsonl').read_text(encoding='utf-8').splitlines())
        for size in (100,1000,5000,20000) for build in (1,2,3) for mode in ('semantic','keyword','tag','hybrid','hybrid_feedback')}
    observed=set();cells=collections.Counter();failures=0;hashes={};full_native=collections.defaultdict(set)
    for path in sorted((destination/'raw').glob('retrieval_n*_build[123].jsonl')):
        hashes[str(path.relative_to(destination))]=sha(path)
        for line in path.open(encoding='utf-8'):
            row=json.loads(line);key=(row['pool_size'],row['build'],row['mode'],row['query_id'])
            if key not in expected or key in observed:raise RuntimeError('unexpected or duplicate v2 ID')
            observed.add(key);cells[key[:3]]+=1;failures+=row['status']!='COMPLETE'
            if row['mode'] in {'semantic','hybrid','hybrid_feedback'}:
                full_native[key[:3]].add((row['query'],tuple(row['input_keywords']),tuple(row['input_tags'])))
    if observed!=expected or len(expected)!=14400 or len(cells)!=60 or set(cells.values())!={240}:
        raise RuntimeError('v2 full 14400 matrix missing; no partial confirmatory table')
    if set(map(len,full_native.values()))!={240}:raise RuntimeError('actual v2 native text inputs are not all unique')
    source=destination/'drivers/formal_retrieval_analyze.py'
    inventory=json.loads((destination/'analysis/input_inventory_sha256.json').read_text(encoding='utf-8'))
    if sha(source)!=inventory['drivers/formal_retrieval_analyze.py']:raise RuntimeError('frozen analyzer changed')
    spec=importlib.util.spec_from_file_location('unchanged_v2_retrieval_analysis',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.OUT=destination;module.main()
    if any(sha(destination/name)!=digest for name,digest in hashes.items()):raise RuntimeError('raw changed during analysis')
    record=dict(status='COMPLETE_REQUIRES_REPORT_NUMERIC_AND_VISUAL_QA',finished_utc=datetime.now(timezone.utc).isoformat(),
        observed_query_units=len(observed),planned_query_units=14400,cells=60,rows_per_cell=240,
        actual_native_unique_text_input_count_per_semantic_hybrid_cell=240,execution_failures=failures,
        original_analyzer_sha256=sha(source),raw_sha256=hashes,table_sha256=sha(destination/'tables/retrieval_quality_scale.csv'),
        input_correction_scope='Postrun correction of original 240-distinct-query execution defect; old 14400 not pooled.',
        real_model_calls=0,human_review=False)
    with target.open('x',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
    print(json.dumps(record,ensure_ascii=False))

if __name__=='__main__':main()
