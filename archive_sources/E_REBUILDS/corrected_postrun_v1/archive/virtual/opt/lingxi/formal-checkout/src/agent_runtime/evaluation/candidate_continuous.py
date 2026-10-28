"""Frozen related-query crossover experiment with causal memory ablation.

The strong text baseline has the same long-lived workers, embedding provider's
own text-vector cache and numeric ranking. Session setup and first corpus upload
are charged to each arm. No previous answer or expected label is available to
workers. Independent oracle float32 ranking verifies every query.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
import numpy as np

from agent_runtime.agents.candidate_session import CandidateSession
from agent_runtime.config import Config, EmbeddingConfig
from agent_runtime.evaluation.runner import _env_info, _allocate_result_dir, _write_checksum_manifest
from agent_runtime.evaluation.stats import paired_ci95
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.runtime.task import Task, VerdictResult

ARMS={'T':('text',False),'S_binary':('copy',False),'SS_fd':('sealed_fd',False),
      'SS_memory':('sealed_fd',True)}


def workload(count=1500):
    groups=[
        ('service-diagnosis',[
            'service cannot bind socket address already in use port 8080',
            'worker permission denied EACCES state directory root mode 0700',
            'DNS resolver fails EAI_AGAIN upstream hostname SERVFAIL',
            'filesystem ENOSPC /var full logging append fails',
            'kernel Out of memory killed worker cgroup memory max',
            'SELinux audit avc denied name_bind app domain'],
         ['bind address conflict','worker state directory permission','upstream DNS name lookup',
          'logging filesystem full','cgroup killed process memory','audit AVC port denial']),
        ('migration-assessment',[
            'native CPython wheel cp310 i686 incompatible cp312 x86_64 rebuild',
            'systemd unit missing environment working directory deploy configuration',
            'ELF shared library libssl SONAME dependency unavailable install library',
            'kernel feature seccomp Landlock sandbox unavailable isolation unsupported',
            'package pure Python compatible architecture interpreter supported',
            'container volume uid mapping file access ownership migrate'],
         ['native wheel interpreter architecture ABI mismatch','systemd deployment environment',
          'ELF shared library missing dependency','sandbox kernel isolation feature',
          'pure Python package compatibility','container volume ownership uid'])]
    tasks=[]
    for topic,templates,queries in groups:
        docs=[templates[i%len(templates)]+f' evidence record {i:05d} '+
              f'host cluster-{i%31} revision-{i%17}' for i in range(count)]
        for qi,query in enumerate(queries):
            # Corpus version change must invalidate reuse, then subsequent queries reuse v2.
            if qi==3: docs=[*docs[:-1],docs[-1]+' changed evidence new revision']
            tasks.append({'group':topic,'query':query+f' investigation round {qi}',
                          'documents':list(docs),'corpus_revision':1 if qi<3 else 2})
    return tasks


def oracle(tasks):
    embedding=build_embedding(Config(embedding=EmbeddingConfig(provider='docsem')))
    return [np.argsort(-(embedding.embed(t['documents'])@embedding.embed([t['query']])[0]),
                      kind='stable').tolist() for t in tasks]


def run(repeat=5,count=1500,task_path=True):
    if repeat<2:raise ValueError('need repeated blocks')
    tasks=workload(count); expected=oracle(tasks)
    raw=[]; path_checks=[]
    for rep in range(repeat):
        order=list(ARMS);shift=rep%len(order);order=order[shift:]+order[:shift]
        for arm in order:
            mode,reuse=ARMS[arm];start=time.perf_counter()
            with CandidateSession(mode=mode,reuse=reuse) as session:
                setup=time.perf_counter()-start; rows=[]
                for index,(task,correct) in enumerate(zip(tasks,expected)):
                    r=session.rank(task['query'],task['documents'])
                    r.update(task_index=index,group=task['group'],success=r['ranked_indices']==correct,
                             corpus_revision=task['corpus_revision'])
                    if not r['success']:raise AssertionError('ranking changed by transport/reuse')
                    rows.append(r)
            wall=time.perf_counter()-start
            raw.append({'repeat':rep,'arm':arm,'session_setup_sec':setup,'sequence_wall_sec':wall,'tasks':rows,
                'strict_socket_bytes':sum(r['strict_socket_bytes'] for r in rows),
                'strict_text_bytes':sum(r['strict_text_bytes'] for r in rows),
                'state_materialized_bytes':sum(r['state_materialized_bytes'] for r in rows),
                'candidate_rows_materialized':sum(r['candidate_rows_materialized'] for r in rows),
                'new_candidate_text_embeddings':sum(r['new_candidate_text_embeddings'] for r in rows),
                'query_rows_embedded':sum(r['query_rows_embedded'] for r in rows),
                'memory_hits':sum(r['producer_memory_hit'] and r['receiver_memory_hit'] for r in rows),
                'nontext_transfers':sum(r['nontext_transfers'] for r in rows),
                'nontext_logical_bytes':sum(r['nontext_logical_bytes'] for r in rows),
                'successes':sum(r['success'] for r in rows)})
            print(json.dumps({'repeat':rep,'arm':arm,'wall_sec':round(wall,3),'successes':len(rows)},ensure_ascii=False),flush=True)
    if task_path:
        # All four outer agents also run in separate processes. Session service is
        # accessed through the existing RPC proxy; vectors move between its workers.
        with tempfile.TemporaryDirectory(prefix='candidate-memory-') as temporary:
            cfg=Config(llm_advisory=False,agent_processes=True,control_transport='uds',state_shared_memory=True)
            store=MemoryStore(build_embedding(cfg),db_path=str(Path(temporary)/'memory.sqlite'))
            session_start=time.perf_counter()
            with CandidateSession(mode='sealed_fd',reuse=True) as session:
                for i,(entry,correct) in enumerate(zip(tasks,expected)):
                    def verify(outputs,answer=correct[:3]):
                        ok=outputs.get('answer')==answer
                        return VerdictResult(success=ok,quality=float(ok))
                    task=Task(task_id=f'continuous-{i}',topic=entry['group'],description='Rank evidence for a new related query.',
                        payload={'question':'rank_candidates','operands':['query','candidates'],
                                 'facts':{'query':entry['query'],'candidates':entry['documents']}},
                        expected={'answer':correct[:3]},verifier=verify)
                    ctx=build_context(cfg,'structured',task_id=task.task_id,use_state_exchange=True,
                        use_memory=True,memory=store,candidate_session=session)
                    result=asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task,ctx))
                    if not result.success:raise AssertionError(result.error)
                    path_checks.append({'task_index':i,'success':result.success,'answer':result.outputs['answer'],
                        'runtime_latency_sec':result.metrics.latency_sec,'messages':result.metrics.message_count,
                        'ipc_frames':result.metrics.ipc_frames,'ipc_verified':result.metrics.ipc_verified,
                        'state_shm_transfers':result.metrics.state_shm_transfers,
                        'candidate_handoff':ctx.blackboard['candidate_handoff']})
            persisted=[r.model_dump(mode='json') for r in store.all_records() if r.memory_id.startswith('candidate_')]
            store.close()
            reopened=MemoryStore(build_embedding(cfg),db_path=str(Path(temporary)/'memory.sqlite'))
            assert all(reopened.get(r['memory_id']) is not None for r in persisted)
            reopened.close()
            path_wall=time.perf_counter()-session_start
    else:persisted=[];path_wall=None
    comparisons={}
    for a,b in [('T','S_binary'),('S_binary','SS_fd'),('SS_fd','SS_memory'),('T','SS_memory')]:
        block={}
        for metric in ('sequence_wall_sec','strict_socket_bytes','strict_text_bytes','candidate_rows_materialized','state_materialized_bytes'):
            av=[next(r[metric] for r in raw if r['arm']==a and r['repeat']==rep) for rep in range(repeat)]
            bv=[next(r[metric] for r in raw if r['arm']==b and r['repeat']==rep) for rep in range(repeat)]
            ci=paired_ci95(av,bv)
            block[metric]={'control_mean':float(np.mean(av)),'treatment_mean':float(np.mean(bv)),
                'saving':1-sum(bv)/sum(av) if sum(av) else None,'paired_ci95':ci,'decrease_supported':ci['high']<0}
        comparisons[b+'_vs_'+a]=block
    return {'suite':'candidate_continuous','generated_at_utc':datetime.now(timezone.utc).isoformat(),'env':_env_info(),
        'methodology':{'repeat':repeat,'tasks_per_sequence':len(tasks),'candidate_count':count,'arms':ARMS,
            'task_sha256':hashlib.sha256(json.dumps(tasks,sort_keys=True).encode()).hexdigest(),
            'baseline':'same persistent workers, provider vector cache, float32 and cosine math; compact text serialization',
            'setup':'worker startup/model load, first upload and close included in sequence_wall_sec',
            'memory':'corpus representation reuse, not previous answer; every distinct query recomputed',
            'invalidation':'corpus revision changes mid-sequence in both groups',
            'physical_accounting':'strict_socket_bytes includes controller and worker links, plus separate state materialization/nontext logical bytes',
            'model_tokens':'no LLM calls in this deterministic retrieval workload; no API-token claim',
            'order':'rotating arm order; all failures fatal and retained in run log'},
        'comparisons':comparisons,'raw':raw,'task_path_checks':path_checks,
        'persisted_corpus_memories':persisted,'task_path_wall_sec':path_wall}


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--repeat',type=int,default=5);p.add_argument('--count',type=int,default=1500)
    p.add_argument('--skip-task-path',action='store_true');args=p.parse_args()
    result=run(args.repeat,args.count,not args.skip_task_path)
    out=_allocate_result_dir('results','candidate_continuous')
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    _write_checksum_manifest(out);print('Results: '+str(out),flush=True)

if __name__=='__main__':main()
