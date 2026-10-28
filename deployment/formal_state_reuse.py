"""Protocol state-reuse grid: exact repeat fractions, native dedup/delta and invalidation checks."""
from pathlib import Path
import json
import os
import sys
import time
import traceback
import numpy as np

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
from agent_runtime.state_exchange.exchange import StateExchange
from agent_runtime.state_exchange.delta import VersionedStateLedger
from agent_runtime.runtime.errors import ReferenceError_
from formal_quantization_matrix import FixedEmbedding

OUT=Path('/evidence/formal_20261019')

def main():
    plan={'protocol_section':'4.2 state reuse','repeat_percent':[0,25,50,75,100],'changed_percent':[1,10,50],
        'sequences_per_cell_per_batch':100,'batches':3,'states_per_sequence':5,'transitions_per_sequence':4,
        'numeric_dimensions':100,'fraction_note':'repeat fractions apply exactly to four transitions; modification fraction applies to 100 coordinates of nonrepeated states',
        'planned_sequences':4500,'planned_state_rows':22500,'provider_calls':0}
    (OUT/'manifests/state_reuse_plan.json').write_text(json.dumps(plan,indent=2))
    count=0;sequence_count=0;invalidations=0;errors=0
    try:
        with (OUT/'raw/state_reuse.jsonl').open('x') as output:
            for batch in range(1,4):
                for repeated in plan['repeat_percent']:
                    for modified in plan['changed_percent']:
                        for sequence in range(100):
                            rng=np.random.default_rng(80000+batch*10000+repeated*100+modified*1000+sequence)
                            vector=rng.normal(size=100).astype(np.float32)
                            embedding=FixedEmbedding(vector)
                            exchange=StateExchange(embedding,task_id=f'reuse-{batch}-{repeated}-{modified}-{sequence}',precision='float32',deduplicate=True)
                            ledger=VersionedStateLedger(namespace=f'reuse-{batch}-{repeated}-{modified}-{sequence}')
                            repeat_flags=np.array([True]*(repeated//25)+[False]*(4-repeated//25));rng.shuffle(repeat_flags)
                            last_delta=None;last_ref=None;prior_snapshots=[]
                            try:
                                for step in range(5):
                                    exact_repeat=step>0 and bool(repeat_flags[step-1])
                                    if step and not exact_repeat:
                                        vector=vector.copy();indices=rng.choice(100,modified,replace=False);vector[indices]+=np.float32(.5+step)
                                    embedding.vector=vector
                                    start=time.perf_counter_ns();cpu=time.process_time_ns()
                                    ref=exchange.put_plan_state({'operation':'rank','operands':['left','right']})
                                    put_ns=time.perf_counter_ns()-start
                                    read_start=time.perf_counter_ns();restored=exchange.get_plan_state(ref.uri)['embedding']
                                    identical=bool(np.array_equal(restored,vector));read_ns=time.perf_counter_ns()-read_start
                                    value={'coordinates':vector.tolist(),'sequence':sequence}
                                    delta_start=time.perf_counter_ns()
                                    delta=ledger.put_snapshot(value) if last_delta is None else ledger.update(last_delta.uri,value)
                                    delta_ns=time.perf_counter_ns()-delta_start
                                    verified=ledger.resolve(delta.uri)==value
                                    immutable=all(ledger.resolve(uri)==old for uri,old in prior_snapshots)
                                    expected_dedup=bool(exact_repeat)
                                    ok=identical and verified and immutable and ref.deduplicated==expected_dedup
                                    errors+=int(not ok)
                                    row={'batch':batch,'repeat_percent':repeated,'modified_percent':modified,'sequence':sequence,'step':step,
                                        'exact_repeat':exact_repeat,'success':ok,'state_uri':ref.uri,'deduplicated':ref.deduplicated,
                                        'materialized_bytes':ref.materialized_bytes,'logical_bytes':ref.logical_nbytes,
                                        'put_ns':put_ns,'read_ns':read_ns,'delta_update_ns':delta_ns,
                                        'delta_kind':delta.kind,'delta_materialized_bytes':delta.materialized_bytes,'delta_logical_bytes':delta.logical_bytes,
                                        'delta_deduplicated':delta.deduplicated,'delta_ops':delta.delta_ops,'delta_depth':delta.depth,
                                        'immutable_prior_versions_verified':immutable,'cpu_ns':time.process_time_ns()-cpu,
                                        'measured_scope':'synthetic native state store/delta; local bytes, not socket traffic'}
                                    output.write(json.dumps(row)+'\n');count+=1
                                    prior_snapshots.append((delta.uri,value));last_delta=delta;last_ref=ref
                                exchange.invalidate(last_ref.uri)
                                try:exchange.get_plan_state(last_ref.uri)
                                except ReferenceError_:invalidations+=1
                                else:errors+=1
                                sequence_count+=1
                            finally:exchange.close()
                        output.flush();print(json.dumps({'batch':batch,'repeat_percent':repeated,'modified_percent':modified,'rows':count}),flush=True)
            output.flush();os.fsync(output.fileno())
        status={'status':'COMPLETED' if errors==0 else 'FAILED','rows':count,'sequences':sequence_count,
            'invalidation_rejections':invalidations,'errors':errors}
    except Exception as exc:
        status={'status':'FAILED','error':str(exc),'traceback':traceback.format_exc(),'rows':count};raise
    finally:(OUT/'analysis/state_reuse_status.json').write_text(json.dumps(status,indent=2))

if __name__=='__main__':main()
