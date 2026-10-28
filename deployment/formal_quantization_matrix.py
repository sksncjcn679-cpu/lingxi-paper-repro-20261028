"""Protocol precision matrix with actual independent-process decoding and ranking decisions."""
from pathlib import Path
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
import traceback
import numpy as np

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
from agent_runtime.state_exchange.exchange import StateExchange,OperandRankingProbe,_decode_candidate
from agent_runtime.state_exchange.shared_buffer import SharedVectorBuffer

OUT=Path('/evidence/formal_20261019')
DIMS=[128,256,768,1536]
DISTRIBUTIONS=['gaussian','sparse','heavy_tail','near_tie']
PRECISIONS=['float32','float16','int8','adaptive']


def fixture(dim,distribution,index):
    rng=np.random.default_rng(50219+dim*1000+DISTRIBUTIONS.index(distribution)*200+index)
    if distribution=='heavy_tail':v=rng.standard_t(2,size=dim).astype(np.float32)
    else:v=rng.normal(size=dim).astype(np.float32)
    if distribution=='sparse':v*=rng.random(dim)<.05
    if not np.any(v):v[0]=1
    candidates=rng.normal(size=(8,dim)).astype(np.float32)
    if distribution=='near_tie':
        delta=rng.normal(size=dim).astype(np.float32)
        delta-=v*(float(delta@v)/float(v@v))
        candidates[1]=candidates[0]+delta*np.float32(1e-3)+v*np.float32(1e-7)
    return v,candidates


class FixedEmbedding:
    name='synthetic-precision-fixture'
    def __init__(self,vector):self.vector=vector;self.dim=len(vector)
    def embed(self,texts):return np.stack([self.vector for _ in texts])


def receiver(conn):
    try:
        while True:
            data=conn.recv()
            if data is None:break
            dim,dist,index,precision,descriptors=data
            original,candidates=fixture(dim,dist,index)
            probe=OperandRankingProbe(candidates)
            start=time.perf_counter_ns();cpu=time.process_time_ns()
            with ExitStack() as stack:
                emb=stack.enter_context(SharedVectorBuffer.attach_readonly(descriptors['embedding']))
                scale=None
                if 'scale' in descriptors:
                    scale_array=stack.enter_context(SharedVectorBuffer.attach_readonly(descriptors['scale']))
                    scale=scale_array if precision.startswith('q4b') else float(scale_array[0])
                decoded=_decode_candidate(precision,emb,scale,dim)
                actual=probe(decoded)
                denom=float(np.linalg.norm(original)*np.linalg.norm(decoded))
                cosine=float(original@decoded)/denom if denom else 1.0
                expected=probe(original)
            conn.send({'consumer_pid':os.getpid(),'receiver_ns':time.perf_counter_ns()-start,
                'receiver_cpu_ns':time.process_time_ns()-cpu,'decision':actual,'expected_decision':expected,
                'full_ranking_preserved':actual==expected,'top1_preserved':actual[0]==expected[0],
                'cosine':cosine,'consumer_read':'actual shared encoded embedding and encoded scale; reference only used for verification'})
    finally:conn.close()


def main():
    plan={'protocol_section':'4.2 quantization and decision preservation','dimensions':DIMS,'distributions':DISTRIBUTIONS,
        'precisions':PRECISIONS,'unique_states_per_dim_distribution':200,'unique_states':3200,'planned_rows':12800,
        'reference_only_inputs':'deterministic candidate/reference generator, not production transport bytes',
        'decision':'stable 8-candidate full ranking and top-1, actual decoded state in independent process',
        'adaptive':'native runtime certified precision ladder; report actual precision and fallback, no fictitious int8-only claim'}
    (OUT/'manifests/quantization_plan.json').write_text(json.dumps(plan,indent=2))
    parent,child=mp.get_context('spawn').Pipe();worker=mp.get_context('spawn').Process(target=receiver,args=(child,))
    worker.start();child.close();before={p.name for p in Path('/dev/shm').iterdir()}
    hashes=set();count=0
    try:
        with (OUT/'raw/quantization.jsonl').open('x') as output:
            for dim in DIMS:
                for dist in DISTRIBUTIONS:
                    for index in range(200):
                        vector,candidates=fixture(dim,dist,index)
                        digest=hashlib.sha256(vector.tobytes()).hexdigest();hashes.add(digest)
                        for precision in PRECISIONS:
                            exchange=StateExchange(FixedEmbedding(vector),task_id=f'quant-{dim}-{dist}-{index}-{precision}',
                                use_shared_memory=True,precision=precision,deduplicate=False)
                            try:
                                start=time.perf_counter_ns();cpu=time.process_time_ns()
                                ref=exchange.put_plan_state({'operation':'rank','operands':[f'c{i}' for i in range(8)]},decision_probe=OperandRankingProbe(candidates))
                                encode_ns=time.perf_counter_ns()-start;encode_cpu=time.process_time_ns()-cpu
                                descriptors=exchange.get_shared_descriptors(ref.uri)
                                if not descriptors:raise RuntimeError('shared encoded state unavailable')
                                parent.send((dim,dist,index,ref.precision,descriptors))
                                if not parent.poll(60):raise TimeoutError('quantization receiver timeout')
                                result=parent.recv()
                                state_bytes=descriptors['embedding'].nbytes+descriptors.get('scale',type('Empty',(),{'nbytes':0})()).nbytes
                                row={'dimension':dim,'distribution':dist,'state_index':index,'source_sha256':digest,
                                    'requested_precision':precision,'actual_precision':ref.precision,
                                    'embedding_logical_bytes':vector.nbytes,'encoded_embedding_and_scale_bytes':state_bytes,
                                    'total_plan_state_bytes':ref.nbytes,'producer_encode_materialize_ns':encode_ns,
                                    'producer_encode_cpu_ns':encode_cpu,'certificate_count':exchange.certificates,
                                    'fallback_or_precision_escalation':bool(exchange.certificate_rejections or (precision=='adaptive' and ref.precision not in ['int4','int8'])),
                                    'producer_pid':os.getpid(),'synthetic_mechanism':True,**result}
                                output.write(json.dumps(row)+'\n');count+=1
                            finally:exchange.close()
                    output.flush();print(json.dumps({'dimension':dim,'distribution':dist,'rows':count}),flush=True)
            output.flush();os.fsync(output.fileno())
        status={'status':'COMPLETED','rows':count,'unique_states':len(hashes),'planned_rows':12800}
    except Exception as exc:
        status={'status':'FAILED','error':str(exc),'traceback':traceback.format_exc(),'rows':count};raise
    finally:
        parent.send(None);parent.close();worker.join(10)
        if worker.is_alive():worker.terminate();worker.join()
        status['consumer_exit']=worker.exitcode
        status['shm_residue']=sorted({p.name for p in Path('/dev/shm').iterdir()}-before)
        worker.close()
        (OUT/'analysis/quantization_status.json').write_text(json.dumps(status,indent=2))

if __name__=='__main__':main()
