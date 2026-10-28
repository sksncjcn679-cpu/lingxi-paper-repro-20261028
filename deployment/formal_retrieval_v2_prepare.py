"""Prepare the original 240-query requirement repair; no retrieval/model runs."""
from __future__ import annotations
import argparse
import collections
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

DEFAULT_OUT = Path('/evidence/formal_20261019')
MODES = ('semantic', 'keyword', 'tag', 'hybrid', 'hybrid_feedback')
NOW = 1792368000.0

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def write(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)

def tokens(text):
    return sorted(set(re.findall(r'[a-zA-Z][a-zA-Z0-9_]+', text.lower())))

def actual_input(query, mode):
    """All explicit and default native MemoryStore.retrieve conditions."""
    return dict(query=query['text'] if mode in {'semantic','hybrid','hybrid_feedback'} else None,
        keywords=tokens(query['text']) if mode in {'keyword','hybrid','hybrid_feedback'} else None,
        tags=[query['domain']] if mode in {'tag','hybrid','hybrid_feedback'} else None,
        top_k=10, now=NOW, memory_type=None, include_low_quality=False,
        include_quarantined=False, current_task_id=None,
        bm25_rerank=mode in {'hybrid','hybrid_feedback'})

def prepare(out, destination, drivers):
    out=out.resolve(); destination=destination.resolve(); drivers=drivers.resolve()
    if destination.exists(): raise RuntimeError('new destination required; never overwrite prepared input')
    if destination == out or out/'raw' in destination.parents or out/'corpus' in destination.parents:
        raise RuntimeError('destination must not overlap original raw or frozen corpus')
    source=out/'corpus/automatic_validated_v1'
    old=[json.loads(line) for line in (source/'queries.jsonl').read_text(encoding='utf-8').splitlines()]
    tasks={t['task_id']:t for t in map(json.loads,(source/'tasks.jsonl').read_text(encoding='utf-8').splitlines())}
    labels={r['query_id']:r for r in map(json.loads,(source/'labels.jsonl').read_text(encoding='utf-8').splitlines())}
    assert len(old)==240 and len({q['query_id'] for q in old})==240
    groups=collections.defaultdict(list)
    for q in old: groups[q['text']].append(q)
    duplicates=[sorted(v,key=lambda q:q['query_id']) for v in groups.values() if len(v)>1]
    assert len(groups)==210 and len(duplicates)==30 and all(len(v)==2 for v in duplicates)
    changed={v[-1]['query_id'] for v in duplicates}
    new=copy.deepcopy(old); diffs=[]; validation=[]
    forbidden_fields=('expected','diagnosis','action','procedure','verifier','automatic_labels')
    for before,after in zip(old,new):
        task=tasks[before['source_task_id']]
        assert task['split']=='formal' and task['domain']==before['domain']
        assert before['automatic_labels']==labels[before['query_id']]
        if before['query_id'] in changed:
            # A fixed, exhaustive public field; no outcome-dependent wording or selection.
            evidence=task['observations']['current_evidence']
            assert isinstance(evidence,str) and evidence.strip()
            suffix=' 当前任务已公开的观测上下文：'+evidence
            after['text']=before['text']+suffix
            assert before['source_task_id'] not in suffix and before['query_id'] not in suffix
            other=next(v[0] for v in duplicates if v[-1]['query_id']==before['query_id'])
            assert evidence != tasks[other['source_task_id']]['observations']['current_evidence']
            diffs.append(dict(query_id=before['query_id'], source_task_id=before['source_task_id'],
                duplicate_kept_query_id=other['query_id'], source_field='observations.current_evidence',
                selection='lexicographically last query_id of each exact-text duplicate pair',
                transformation='append the entire already-public current_evidence verbatim',
                before_text=before['text'], after_text=after['text'], appended_public_evidence=evidence,
                before_text_sha256=hashlib.sha256(before['text'].encode()).hexdigest(),
                after_text_sha256=hashlib.sha256(after['text'].encode()).hexdigest(),
                source_public_field_sha256=hashlib.sha256(evidence.encode()).hexdigest(),
                prohibited_source_fields_not_read=forbidden_fields))
        assert {k:v for k,v in before.items() if k!='text'}=={k:v for k,v in after.items() if k!='text'}
        gold=after['automatic_labels']['relevant_source_task_ids']
        causal=[t['task_id'] for t in tasks.values() if t['split']=='formal' and
            t['chain_id']==task['chain_id'] and t['observations']['component']==task['observations']['component'] and
            t['observations']['checkpoint'] < task['observations']['checkpoint']]
        no_answer=after['automatic_labels']['no_answer']
        if no_answer:
            assert not gold and (not causal or '独立存储阵列固件签名验证规程' in after['text'])
        else: assert set(gold)==set(causal) and gold
        validation.append(dict(query_id=after['query_id'], original_type_preserved=True,
            all_nontext_fields_identical=True, label_equals_original_labels_file=True,
            formal_source_task=True, gold_causal_component_rule_pass=True,
            no_answer=no_answer, no_answer_intent_preserved=True,
            appended_only_public_observation=after['query_id'] in changed))
    assert len(diffs)==30 and len({q['text'] for q in new})==240
    assert collections.Counter(q['domain'] for q in new)==dict(service=60,migration=60,resources=60,network_permissions=60)
    assert collections.Counter(q['query_class'] for q in new)==collections.Counter(q['query_class'] for q in old)
    assert sum(q['automatic_labels']['no_answer'] for q in new)==80
    native=[]; counts={}
    for mode in MODES:
        hashes=[]
        for q in new:
            inp=actual_input(q,mode); digest=hashlib.sha256(canonical(inp).encode()).hexdigest(); hashes.append(digest)
            native.append(dict(query_id=q['query_id'],mode=mode,native_input=inp,native_input_sha256=digest))
        counts[mode]=len(set(hashes))
    assert counts['semantic']==counts['hybrid']==counts['hybrid_feedback']==240
    identity=json.loads((out/'analysis/retrieval_stress_driver_identity_final.json').read_text(encoding='utf-8'))
    frozen={x['name']:x['sha256'] for x in identity['files']}
    for filename in ('formal_retrieval_matrix.py','formal_retrieval_analyze.py'):
        assert sha(drivers/filename)==frozen[filename], 'original retrieval implementation changed'
    destination.mkdir(parents=True)
    for sub in ('corpus','raw','analysis','tables','logs/mechanisms','drivers'):
        (destination/sub).mkdir(parents=True,exist_ok=True)
    corpus=destination/'corpus'
    for filename in ('tasks.jsonl','labels.jsonl','label_policy.md'):
        shutil.copyfile(source/filename,corpus/filename)
        assert sha(source/filename)==sha(corpus/filename)
    data=''.join(json.dumps(q,ensure_ascii=False,allow_nan=False)+'\n' for q in new)
    for filename in ('queries.jsonl','queries_v2.jsonl'):
        with (corpus/filename).open('x',encoding='utf-8',newline='\n') as f:f.write(data)
    for filename in ('formal_retrieval_matrix.py','formal_retrieval_analyze.py'):
        shutil.copyfile(drivers/filename,destination/'drivers'/filename)
    for filename in ('formal_retrieval_v2_prepare.py','formal_retrieval_v2_run.py','formal_retrieval_v2_analyze.py'):
        shutil.copyfile(drivers/filename,destination/'drivers'/filename)
    write(destination/'analysis/query_diffs.json',diffs)
    write(destination/'analysis/query_semantic_validation.json',dict(status='PASS_AUTOMATIC_NO_HUMANS',
        rows=validation, records=240, changed=30, distinct_texts=240, native_unique_input_counts=counts,
        semantic_validation_boundary='Deterministic public-field provenance, intent-preservation and existing causal/component compatibility rule; no human semantic ratings.',
        keyword_tag_projection='Keyword and tag modes deliberately discard text; projected-input counts do not redefine the 240 distinct-query requirement.'))
    write(destination/'analysis/native_input_inventory.json',native)
    inventory={str(p.relative_to(destination)).replace('\\','/'):sha(p) for p in destination.rglob('*') if p.is_file()}
    write(destination/'analysis/input_inventory_sha256.json',inventory)
    registration=dict(status='PREPARED_NOT_EXECUTED',created_utc=datetime.now(timezone.utc).isoformat(),
        original_out=str(out),destination=str(destination),original_queries_sha256=sha(source/'queries.jsonl'),
        original_tasks_sha256=sha(source/'tasks.jsonl'),original_labels_sha256=sha(source/'labels.jsonl'),
        queries_v2_sha256=sha(corpus/'queries.jsonl'),input_inventory_sha256=sha(destination/'analysis/input_inventory_sha256.json'),
        original_driver_hashes={n:frozen[n] for n in ('formal_retrieval_matrix.py','formal_retrieval_analyze.py')},
        planned_query_units=14400,pool_sizes=[100,1000,5000,20000],builds=3,
        seeds=[26101901,26101902,26101903],modes=MODES,query_ids=240,answerable=160,no_answer=80,
        native_unique_input_counts=counts,original_unique_texts=210,corrected_unique_texts=240,changed_records=30,
        experimental_timing='POSTRUN_INPUT_DEFECT_CORRECTION_NOT_PREFORMAL_FREEZE',
        old_results_policy='Original 14400 retrieval units retained as input-defect archive_only; never pool with v2.',
        prior_outcome_visibility='Original retrieval outputs already existed and were audited; deterministic correction selects exact duplicates and entire public facts without consulting scores/ranks/latencies.',
        before_new_outcome=True,new_raw_files_at_registration=[],new_model_calls=0,human_review=False,
        only_user_authorized_scoring_exception='Automatic validation replaces manual dual review/scoring.',
        invariant_conditions='Same IDs/domains/classes/source tasks/labels/pools/seeds/algorithms/modes/metrics/full 14400 matrix; only 30 query text suffixes changed.',
        runtime_path='/opt/lingxi/formal-checkout/src',sequential_workers=True,worker_rss_ceiling_mib=1400,
        expected_pool_bytes='Each v2 pool must equal corresponding original actual pool SHA before retrieval. Verify in v2 runner.')
    write(destination/'analysis/before_new_outcome_registration.json',registration)
    with (destination/'checksums_inputs.sha256').open('x',encoding='utf-8',newline='\n') as f:
        for p in sorted(destination.rglob('*')):
            if p.is_file() and p.name!='checksums_inputs.sha256':f.write(sha(p)+'  '+str(p.relative_to(destination)).replace('\\','/')+'\n')
    return registration

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--drivers',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args()
    destination=args.destination or args.out/'retrieval_input_correction_v2'
    print(json.dumps(prepare(args.out,destination,args.drivers),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
