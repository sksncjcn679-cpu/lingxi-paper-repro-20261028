"""Materialize all 12 independent-build inputs before new retrieval outcomes."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
import retrieval_driver as retrieval
from calibrate_contract import deny_internet


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def native_input(query,mode):
    return {'query':query['text'] if mode in ('semantic','hybrid','hybrid_feedback') else None,
            'keywords':retrieval.tokens(query['text']) if mode in ('keyword','hybrid','hybrid_feedback') else None,
            'tags':[query['domain']] if mode in ('tag','hybrid','hybrid_feedback') else None,
            'top_k':10,'now':retrieval.NOW,'memory_type':None,'include_low_quality':False,
            'include_quarantined':False,'current_task_id':None,
            'bm25_rerank':mode in ('hybrid','hybrid_feedback')}


def main():
    p=argparse.ArgumentParser();p.add_argument('--destination',type=Path,required=True);a=p.parse_args()
    if a.destination.exists():raise RuntimeError('new retrieval input version required')
    a.destination.mkdir(parents=True);sys.addaudithook(deny_internet)
    tasks,queries=retrieval.load();before={p.name:sha(p) for p in retrieval.CORPUS.iterdir() if p.is_file()}
    assert len(queries)==240 and len({q['text'] for q in queries})==240
    assert Counter(q['domain'] for q in queries)=={'service':60,'migration':60,'resources':60,'network_permissions':60}
    native=[];native_counts={};pool_files={};gold_sets={}
    for mode in retrieval.MODES:
        hashes=[]
        for query in queries:
            value=native_input(query,mode);digest=hashlib.sha256(canonical(value).encode()).hexdigest();hashes.append(digest)
            native.append({'query_id':query['query_id'],'mode':mode,'input':value,'sha256':digest})
        native_counts[mode]=len(set(hashes))
    assert all(native_counts[m]==240 for m in ('semantic','hybrid','hybrid_feedback'))
    (a.destination/'native_input_inventory.json').write_text(json.dumps(native,ensure_ascii=False,indent=2)+'\n')
    for build in range(1,4):
        for size in retrieval.SIZES:
            records=retrieval.records(size,build,tasks,queries)
            assert len(records)==size and len({r.memory_id for r in records})==size
            for record in records:
                # Force full native MemoryRecord schema, including nested JSON validation.
                assert retrieval.MemoryRecord.model_validate_json(record.model_dump_json()).memory_id==record.memory_id
            positive=[r for r in records if r.memory_id.startswith('source:')]
            assert len(positive)==100
            for query in queries:
                relevant=[]
                for record in positive:
                    payload=record.payload
                    if (payload['chain_id']==query['chain_id'] and payload['checkpoint']<query['checkpoint']
                        and payload['trusted'] and payload['validity']=='current_procedure_v1'
                        and set(query['required_capabilities'])<=set(payload['capabilities'])):
                        relevant.append(record.memory_id)
                expected={'source:'+v for v in query['relevant_memory_ids']}
                assert set(relevant)==expected
                assert query['no_answer']==(not relevant)
                if build==1 and size==100:gold_sets[query['query_id']]=sorted(relevant)
                else:assert gold_sets[query['query_id']]==sorted(relevant)
            path=a.destination/f'pool_n{size}_build{build}.jsonl'
            with path.open('x',encoding='utf-8') as stream:
                for record in records:stream.write(record.model_dump_json()+'\n')
            counts=Counter(r.payload.get('fixture_condition','causal_source') for r in records)
            pool_files[path.name]={'sha256':sha(path),'records':size,'bytes':path.stat().st_size,
                                   'build':build,'seed':retrieval.SEEDS[build-1]+size,'conditions':dict(counts)}
            print(json.dumps({'size':size,'build':build,'status':'INPUT_PREPARED','records':size}),flush=True)
    assert before=={p.name:sha(p) for p in retrieval.CORPUS.iterdir() if p.is_file()}
    (a.destination/'gold_memory_sets.json').write_text(json.dumps(gold_sets,indent=2)+'\n')
    registration={'status':'PREPARED_ALL12_POOL_INPUTS_NOT_RETRIEVAL_EXECUTION','pool_files':pool_files,
                  'queries':240,'answerable':160,'no_answer':80,'sizes':retrieval.SIZES,'builds':3,'seeds':retrieval.SEEDS,
                  'modes':retrieval.MODES,'planned_query_units':14400,'native_unique_input_counts':native_counts,
                  'all_gold_sets_identical_across_pools':True,'causal_candidate_prefilter_at_query_time':False,
                  'pool100_boundary':'all100 source memories; future/current and cross-chain entries act as negatives for each query; explicit stale/wrong distractors in larger pools',
                  'feedback_basis':'pre-authored fixture_condition stale/wrong_conflict only; current query labels never used',
                  'tasks_queries_sha256':before,'driver_sha256':sha(Path(retrieval.__file__)),'preparer_sha256':sha(Path(__file__)),
                  'native_input_inventory_sha256':sha(a.destination/'native_input_inventory.json'),
                  'gold_memory_sets_sha256':sha(a.destination/'gold_memory_sets.json'),
                  'paid_api_calls':0,'retrieval_outcomes_observed':0,'human_scores':0,
                  'worker_rss_limit_mib':1400,'worker_timeout_sec':1200,'over_capacity_policy':'retain failure/timeout and mark incomplete; never extrapolate smaller pool'}
    (a.destination/'input_registration.json').write_text(json.dumps(registration,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in registration.items() if k not in ('pool_files','tasks_queries_sha256')},ensure_ascii=False))


if __name__=='__main__':main()
