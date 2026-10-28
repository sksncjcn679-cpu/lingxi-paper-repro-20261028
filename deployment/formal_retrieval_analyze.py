"""Rebuild retrieval tables from retained query rows; never mix in model evidence."""
from __future__ import annotations
import csv
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import numpy as np

OUT=Path('/evidence/formal_20261019')
METRICS=('p_at_1','recall_at_3','recall_at_10','mrr','ndcg_at_10','false_retrieval','refusal')

def main():
    cells=defaultdict(list);process=[];pool_counts=[]
    for path in sorted((OUT/'raw').glob('retrieval_n*_build[123].jsonl')):
        for line in path.open():
            r=json.loads(line);cells[(r['pool_size'],r['mode'])].append({k:r[k] for k in
                ('pool_size','mode','build','chain_id','domain','source_task_id','query_id','answerable',
                 'latency_ns','rss_bytes','candidate_count','status',*METRICS)})
    for path in sorted((OUT/'analysis').glob('retrieval_n*_build[123]_process.json')):
        process.append(json.loads(path.read_text()))
    for path in sorted((OUT/'analysis').glob('retrieval_n*_build[123]_build.json')):
        pool_counts.append(json.loads(path.read_text()))
    planned=14400;observed=sum(len(v) for v in cells.values());summary=[]
    for (size,mode),rows in sorted(cells.items()):
        domains=sorted({r['domain'] for r in rows});chain_order=sorted({(r['domain'],r['chain_id']) for r in rows})
        # All queries of a task and all three build repeats stay inside its chain.
        chain_index={ch:i for i,ch in enumerate(chain_order)}
        rng=np.random.default_rng(261019)
        draws=[]
        for domain in domains:
            indices=np.array([i for i,(d,c) in enumerate(chain_order) if d==domain])
            draws.append(rng.choice(indices,size=(10000,len(indices)),replace=True))
        picks=np.concatenate(draws,axis=1)
        row={'pool_size':size,'mode':mode,'planned_query_units':720,'recorded_query_units':len(rows),
            'distinct_queries':len({r['query_id'] for r in rows}),
            'distinct_source_tasks':len({r['source_task_id'] for r in rows}),
            'chains':len(chain_order),'builds':len({r['build'] for r in rows}),
            'answerable_query_units':sum(r['answerable'] for r in rows),
            'no_answer_query_units':sum(not r['answerable'] for r in rows),
            'execution_failures':sum(r['status']!='COMPLETE' for r in rows),
            'latency_p50_ms':float(np.quantile([r['latency_ns']/1e6 for r in rows],.5)),
            'latency_p95_ms':float(np.quantile([r['latency_ns']/1e6 for r in rows],.95)),
            'peak_query_rss_mib':max(r['rss_bytes'] for r in rows)/1048576,
            'mean_candidates':float(np.mean([r['candidate_count'] for r in rows]))}
        cluster_latencies=[]
        for domain,chain in chain_order:
            cluster_latencies.append([r['latency_ns']/1e6 for r in rows if r['domain']==domain and r['chain_id']==chain])
        if len({len(v) for v in cluster_latencies})==1:
            latency_draws=np.asarray(cluster_latencies)[picks].reshape(10000,-1)
            for quantile,label in ((.5,'p50'),(.95,'p95')):
                replicates=np.quantile(latency_draws,quantile,axis=1)
                row[f'latency_{label}_ci95_low_ms']=float(np.quantile(replicates,.025))
                row[f'latency_{label}_ci95_high_ms']=float(np.quantile(replicates,.975))
            del latency_draws
        for metric in METRICS:
            eligible=[r for r in rows if r[metric] is not None]
            sums=np.zeros(len(chain_order));counts=np.zeros(len(chain_order))
            for r in eligible:
                i=chain_index[(r['domain'],r['chain_id'])];sums[i]+=r[metric];counts[i]+=1
            values=sums[picks].sum(axis=1)/counts[picks].sum(axis=1)
            row[metric]=float(sum(r[metric] for r in eligible)/len(eligible)) if eligible else None
            row[metric+'_ci95_low']=float(np.quantile(values,.025))
            row[metric+'_ci95_high']=float(np.quantile(values,.975))
            row[metric+'_denominator']=len(eligible)
        summary.append(row)
    (OUT/'tables').mkdir(exist_ok=True)
    if summary:
        with (OUT/'tables/retrieval_quality_scale.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
    audit={'planned_query_units':planned,'recorded_query_units':observed,
        'complete':observed==planned and len(cells)==20,
        'source_files':[{'path':str(p.relative_to(OUT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted((OUT/'raw').glob('retrieval_n*_build[123].jsonl'))],
        'pool_builds':pool_counts,'processes':process,
        'statistics':'10000 domain-stratified chain-cluster bootstrap; all task queries and build repeats retained in cluster',
        'mrr_scope':'MRR@10; native returns at most 10 candidates, no extrapolation beyond measured ranks',
        'raw_first_query_flag_correction':'raw cold_postings_after_build_or_feedback marks the first query of each mode, not a measured cache miss; actual initial postings build is first semantic query, and feedback refresh occurs only if feedback changed the store',
        'feedback_scope':'known stale/wrong synthetic distractors receive two pre-seeded harmful-history observations; no claim these are real provider adoptions',
        'method_scope':{'semantic':'native semantic-only query, BM25 disabled',
            'keyword':'native keyword-only overlap, no query embedding',
            'tag':'native domain tag overlap, no query embedding',
            'hybrid':'native semantic+keyword+domain tags with BM25 rerank',
            'hybrid_feedback':'same hybrid query after explicit negative synthetic feedback history'},
        'no_answer_scope':'native candidate retrieval nonempty/empty; not adoption-gate false acceptance',
        'label_scope':'deterministic causal/component procedure compatibility; user-authorized automatic labels, no human reviews',
        'initialization_scope':'native metadata/vector schema bulk initialization; initialization times are not runtime ingestion/dedup throughput',
        'gold_filter':False,'real_model_calls':0}
    (OUT/'analysis/retrieval_quality_scale_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(json.dumps({'observed':observed,'planned':planned,'cells':len(cells),'complete':audit['complete']}),flush=True)

if __name__=='__main__':main()
