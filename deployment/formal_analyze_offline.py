"""Rebuild current offline tables and protocol figures 5/6 from measured raw rows only."""
from pathlib import Path
from collections import defaultdict
import csv
import hashlib
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT=Path('/evidence/formal_20261019')

def load(name):
    return [json.loads(s) for s in (OUT/'raw'/name).read_text().splitlines()]

def csv_write(name,rows):
    with (OUT/name).open('w',newline='') as f:
        writer=csv.DictWriter(f,list(rows[0]));writer.writeheader();writer.writerows(rows)

def save(fig,name):
    fig.tight_layout()
    for ext in ['pdf','svg','png']:
        fig.savefig(OUT/'figures'/f'{name}.{ext}',dpi=600,bbox_inches='tight')
    plt.close(fig)

def main():
    states=sum((load(f'state_paths_batch_{i}.jsonl') for i in range(1,4)),[])
    quant=load('quantization.jsonl')
    reuse=load('state_reuse.jsonl')
    protocol=sum((load(f'protocol_v2_batch_{i}.jsonl') for i in range(1,4)),[])
    audit={'state_rows':len(states),'state_failures':sum(not r['success'] for r in states),
        'quantization_rows':len(quant),'quantization_unique_states':len({r['source_sha256'] for r in quant}),
        'reuse_rows':len(reuse),'reuse_failures':sum(not r['success'] for r in reuse),
        'protocol_v2_rows':len(protocol),'protocol_failures':sum(not r['success'] for r in protocol),
        'protocol_distinct_producers':len({r['producer_pid'] for r in protocol}),
        'protocol_distinct_consumers':len({r['consumer_pid'] for r in protocol}),
        'protocol_batch_interpretation':'three independent consumer processes; same producer process. Strict independent producer-batch audit pending.',
        'state_byte_boundary':'observable AF_UNIX data/framing/ACK; ancillary kernel overhead excluded',
        'model_quality_data':False,'formal_real_model_units':0,
        'quantization_adaptive_decision_failures':sum(r['requested_precision']=='adaptive' and not r['full_ranking_preserved'] for r in quant),
        'quantization_float32_decision_failures':sum(r['requested_precision']=='float32' and not r['full_ranking_preserved'] for r in quant)}
    # Every protocol row is hash-chained and byte accounting is exact at the declared stream boundary.
    bad_hashes=0
    for batch in range(1,4):
        previous='0'*64
        for row in load(f'protocol_v2_batch_{batch}.jsonl'):
            claimed=row.pop('record_sha256')
            if row['prev_sha256']!=previous or hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()!=claimed:bad_hashes+=1
            previous=claimed
            if row['observable_socket_bytes']!=row['payload_wire_bytes']+row['frame_bytes']+row['ack_bytes']:raise AssertionError('protocol byte accounting mismatch')
    audit['protocol_hash_chain_errors']=bad_hashes
    groups=defaultdict(list)
    for row in states:groups[(row['path'],row['condition'],row['logical_bytes'])].append(row)
    table=[]
    for (path,condition,size),rows in sorted(groups.items()):
        values=np.array([r['task_wall_ns']/1e6 for r in rows])
        table.append({'path':path,'condition':condition,'logical_bytes':size,'n':len(rows),
            'p50_ms':float(np.percentile(values,50)),'p95_ms':float(np.percentile(values,95)),
            'p99_ms':float(np.percentile(values,99)),'mean_ms':float(values.mean()),
            'mean_socket_bytes':float(np.mean([r['socket_observed_bytes'] for r in rows])),
            'mean_local_materialization_bytes':float(np.mean([r['local_materialization_bytes'] for r in rows])),
            'mean_prepare_ms':float(np.mean([r['producer_prepare_ns']/1e6 for r in rows])),
            'mean_handoff_ms':float(np.mean([r['handoff_roundtrip_ns']/1e6 for r in rows])),
            'mean_release_ms':float(np.mean([r['producer_release_ns']/1e6 for r in rows])),
            'scope':'synthetic transport, independent shared consumer; not model end-to-end latency'})
    csv_write('tables/state_paths.csv',table);csv_write('figures/figure_05.csv',table)
    plt.rcParams.update({'font.size':9,'pdf.fonttype':42,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(7,3.2),sharey=True)
    for axis,condition in zip(axes,['cold','hot']):
        for path,marker,color in [('copy','o','#183153'),('shm','s','#087F8C'),('memfd','^','#6356A5')]:
            rows=sorted([r for r in table if r['path']==path and r['condition']==condition],key=lambda r:r['logical_bytes'])
            x=[r['logical_bytes']/1024 for r in rows]
            axis.plot(x,[r['p50_ms'] for r in rows],marker=marker,color=color,label=f'{path} p50')
            axis.plot(x,[r['p95_ms'] for r in rows],linestyle=':',color=color,alpha=.65)
        axis.set_xscale('log',base=2);axis.set_xlabel('Logical state (KiB)');axis.set_title('First materialization' if condition=='cold' else 'Reused materialization')
        axis.grid(alpha=.2);axis.legend(fontsize=7)
    axes[0].set_ylabel('Measured wall time (ms)');save(fig,'figure_05')
    groups=defaultdict(list)
    for row in quant:groups[(row['dimension'],row['distribution'],row['requested_precision'])].append(row)
    qt=[]
    for (dim,dist,precision),rows in sorted(groups.items()):
        count=len(rows);success=sum(r['full_ranking_preserved'] for r in rows);p=success/count;z=1.959963984540054
        den=1+z*z/count;mid=(p+z*z/(2*count))/den;half=z*((p*(1-p)/count+z*z/(4*count*count))**.5)/den
        qt.append({'dimension':dim,'distribution':dist,'precision':precision,'unique_states':count,
            'full_ranking_preserved':success,'decision_rate':p,'wilson95_low':mid-half,'wilson95_high':mid+half,
            'top1_rate':sum(r['top1_preserved'] for r in rows)/count,
            'encoded_embedding_scale_bytes_mean':float(np.mean([r['encoded_embedding_and_scale_bytes'] for r in rows])),
            'cosine_mean':float(np.mean([r['cosine'] for r in rows])),
            'fallback_rate':sum(r['fallback_or_precision_escalation'] for r in rows)/count})
    csv_write('tables/quantization.csv',qt);csv_write('figures/figure_06.csv',qt)
    fig,axes=plt.subplots(2,2,figsize=(7,5.5),sharey=True)
    for axis,dist in zip(axes.flat,['gaussian','sparse','heavy_tail','near_tie']):
        for precision,marker,color in [('float32','o','#183153'),('float16','s','#087F8C'),('int8','^','#6356A5'),('adaptive','D','#D96C3F')]:
            rows=[r for r in qt if r['distribution']==dist and r['precision']==precision]
            axis.errorbar([r['encoded_embedding_scale_bytes_mean'] for r in rows],[r['decision_rate'] for r in rows],
                yerr=[[r['decision_rate']-r['wilson95_low'] for r in rows],[r['wilson95_high']-r['decision_rate'] for r in rows]],
                fmt=marker,color=color,label=precision,markersize=4,linestyle='none',capsize=2)
        axis.set_xscale('log',base=2);axis.set_ylim(-.02,1.02);axis.grid(alpha=.2);axis.set_title(dist.replace('_',' '))
        axis.set_xlabel('Encoded embedding + scale (bytes)');axis.set_ylabel('Full ranking preserved');axis.legend(fontsize=6)
    save(fig,'figure_06')
    (OUT/'analysis/offline_data_audit.json').write_text(json.dumps(audit,indent=2))
    print(json.dumps(audit,indent=2),flush=True)

if __name__=='__main__':main()
