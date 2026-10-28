"""Derived pressure/fault tables; planned denominators include retained failures."""
from collections import defaultdict
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np

OUT=Path('/evidence/formal_20261019')

def csv_write(path,rows):
    if not rows:
        if path.exists():
            header=path.read_text(encoding='utf-8').splitlines()[0]
            path.write_text(header+'\n',encoding='utf-8')
        return
    columns=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(rows)

def main():
    data=[];sources=[];total=0
    for path in sorted((OUT/'raw').glob('stress_v3_L*_K*.jsonl')):
        if path.stem.endswith('_batches'):continue
        rows=[json.loads(s) for s in path.open()];total+=len(rows)
        bp=path.with_name(path.stem+'_batches.jsonl')
        batches=[json.loads(s) for s in bp.open()] if bp.exists() else []
        if not rows:continue
        latency=np.array([r['wall_ns']/1e9 for r in rows])
        through=np.array([r['throughput_tasks_per_sec'] for r in batches])
        rng=np.random.default_rng(261019)
        bootstrap=through[rng.integers(0,len(through),(10000,len(through)))].mean(axis=1)
        proxy=sum((r['metrics'] or {}).get('text_tokens',0) for r in rows)
        wire=sum((r['metrics'] or {}).get('ipc_framed_bytes',0)+(r['metrics'] or {}).get('ipc_ack_bytes',0) for r in rows)
        row={'length':rows[0]['length'],'concurrency':rows[0]['concurrency'],
            'planned_batches':30,'recorded_batches':len(batches),
            'planned_tasks':rows[0]['length']*rows[0]['concurrency']*30,'recorded_tasks':len(rows),
            'successes':sum(r['success'] for r in rows),'failures':sum(not r['success'] for r in rows),
            'state_consumed':sum(r['state_consumed'] for r in rows),
            'throughput_mean_tasks_per_sec':float(through.mean()),
            'throughput_ci95_low':float(np.quantile(bootstrap,.025)),
            'throughput_ci95_high':float(np.quantile(bootstrap,.975)),
            'task_latency_p50_sec':float(np.quantile(latency,.5)),
            'task_latency_p95_sec':float(np.quantile(latency,.95)),
            'task_latency_p99_sec':float(np.quantile(latency,.99)),
            'message_proxy_tokens_total':proxy,'native_observable_frame_ack_bytes_total':wire,
            'message_proxy_tokens_per_chain':proxy/(rows[0]['concurrency']*len(batches)),
            'native_observable_frame_ack_bytes_per_chain':wire/(rows[0]['concurrency']*len(batches)),
            'peak_batch_rss_mib':max(b['rss_bytes'] for b in batches)/1048576,
            'provider_tokens':None,'scope':'offline SS1; bytes exclude native handshake counters'}
        data.append(row)
        sources.append({'path':str(path.relative_to(OUT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    csv_write(OUT/'tables/stress_chain_concurrency.csv',data)
    slopes=[]
    for k in (1,2,4,8,16):
        rs=[r for r in data if r['concurrency']==k]
        if len(rs)>1:
            x=np.array([r['length'] for r in rs]);
            slopes.append({'concurrency':k,'observed_length_levels':len(rs),
                'proxy_message_token_slope_per_chain_step':float(np.polyfit(x,[r['message_proxy_tokens_per_chain'] for r in rs],1)[0]),
                'native_frame_ack_byte_slope_per_chain_step':float(np.polyfit(x,[r['native_observable_frame_ack_bytes_per_chain'] for r in rs],1)[0]),
                'model_tokens':None,'interpretation':'descriptive linear slope over measured offline chain levels only'})
    csv_write(OUT/'tables/stress_growth_slopes.csv',slopes)
    faults=[]
    for path in sorted((OUT/'raw').glob('stress_fault_*.jsonl')):
        rows=[json.loads(s) for s in path.open()]
        if not rows:continue
        fault_name=rows[0]['fault']
        scope=rows[0]['injection']['hook_evidence'].get('scope','')
        if fault_name=='wrong_memory':
            scope='wrong procedural memory actually adopted into fixture prompt; exact-final-answer reuse not exercised; fixture success is not model robustness'
        faults.append({'fault':fault_name,'planned_trials':30,'recorded_trials':len(rows),
            'actual_injections':sum(r['injection']['hook_evidence'].get('injected',False) for r in rows),
            'recovery_successes':sum(r['recovery_success'] for r in rows),
            'recovery_rate_planned_denominator':sum(r['recovery_success'] for r in rows)/30,
            'injection_task_failures':sum(not r['injection']['success'] for r in rows),
            'unflagged_nonfinite_consumptions':sum(r['unflagged_nonfinite_consumption'] for r in rows),
            'duplicate_business_executions':sum(r['duplicate_business_executions'] for r in rows),
            'shm_leak_trials':sum(bool(r['new_shm_after_cleanup']) for r in rows),
            'scope':scope})
        sources.append({'path':str(path.relative_to(OUT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    csv_write(OUT/'tables/stress_fault_recovery.csv',faults)
    report={'planned_pressure_tasks':58590,'recorded_pressure_tasks':total,
        'pressure_cells_complete':sum(r['recorded_tasks']==r['planned_tasks'] and r['recorded_batches']==30 for r in data),
        'planned_pressure_cells':30,'planned_fault_trials':180,'recorded_fault_trials':sum(r['recorded_trials'] for r in faults),
        'continuous_status':json.loads((OUT/'analysis/stress_continuous_100_status.json').read_text()) if (OUT/'analysis/stress_continuous_100_status.json').exists() else None,
        'sources':sources,'real_provider_calls':0,
        'excluded_preparation_runs':[
            'stress_L1_K1: 30 fixture response serialization errors before v2; retained unchanged',
            'stress_v2_L1_K1: 30 passing smoke units overlapped retrieval, excluded from final pressure timing'],
        'statistics':'10000 independent batch bootstrap for throughput; tail latencies pooled measured native fixture task times',
        'limits':['SS1 fixed 4-role graph; no agent-count extrapolation','No provider time, usage or real-model task-quality claim',
                  'Completed-message redelivery is not concurrent-first-delivery exactly-once','Native handshake bytes excluded from Channel metric']}
    (OUT/'analysis/stress_matrix_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'pressure_tasks':total,'cells':len(data),'fault_trials':report['recorded_fault_trials']}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-root',type=Path,default=OUT)
    args=parser.parse_args();OUT=args.output_root;main()
