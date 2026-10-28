"""Full four-role SS1 offline chain/concurrency matrix with resumable cell logs.

Synthetic fixture outputs are not provider responses or paper task-quality evidence.
Every task still executes native Planner/Retriever/Executor/Reviewer, UDS, state,
memory adoption and feedback. There is no model latency projection.
"""
from __future__ import annotations
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
import numpy as np
import psutil
from agent_runtime.config import Config, LLMConfig
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.base import LLMResponse
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from formal_fact_calibration import make_task
from formal_fixture_checks import automatic_label
from formal_corpus import ACTION
from formal_prompt_adapter import model_prompt

OUT=Path('/evidence/formal_20261019')
LENGTHS=(1,2,4,8,16,32)
CONCURRENCIES=(1,2,4,8,16)

def config():
    cfg=Config(llm=LLMConfig(provider='mock',output_contract='none'),llm_advisory=False,llm_judge=False,
        llm_coalesce='off',llm_context_mode='inbound',text_style='lean',control_transport='uds',
        state_shared_memory=True,pipeline_topology='request_reply',task_retry_budget=0,
        memory_fast_path=False,data_dir='/opt/lingxi/formal-stress-data')
    return cfg.with_overrides(embedding=replace(cfg.embedding,provider='docsem',dim=256))

def corpus():
    rows=[json.loads(s) for s in (OUT/'corpus/automatic_validated_v1/tasks.jsonl').read_text().splitlines()]
    return [r for r in rows if r['split']=='formal']

def unit_row(template,unit_id,round_index):
    row=deepcopy(template);row['task_id']=unit_id
    row['observations']['incident_id']=unit_id
    row['observations']['checkpoint']=round_index+1
    row['observations']['previous_checkpoint']=round_index or None
    row['observations']['checks']={a:f'fixture-check {unit_id} {a}' for a in row['observations']['checks']}
    return row

def run_unit(row,cfg,memory,hook=None):
    started=time.perf_counter_ns();c0=time.process_time_ns();ctx=None;result=None;error=None
    calls=[];hook_evidence={}
    try:
        ctx=build_context(cfg,'structured',task_id=row['task_id'],use_memory=True,
            use_state_exchange=True,use_sandbox=False,memory=memory)
        def complete(prompt,**kwargs):
            rewritten,audit=model_prompt(prompt,row['observations'])
            diagnosis=automatic_label(row)
            response={'diagnosis':diagnosis,'action':ACTION[diagnosis]}
            calls.append({'prompt_sha256':hashlib.sha256(rewritten.encode()).hexdigest(),
                'response':response,'fact_audit':audit,'source':'deterministic_current_evidence_parser',
                'provider_usage':None})
            return LLMResponse(text=json.dumps(response),prompt_tokens=0,completion_tokens=0,
                model='offline_evidence_fixture',metadata={'usage_source':'not_provider'})
        ctx.llm.complete=complete
        agents=build_default_agents(cfg)
        if hook:hook(ctx,agents,hook_evidence)
        result=asyncio.run(Orchestrator(agents).run_task(make_task(row),ctx))
    except Exception as exc:
        error={'type':type(exc).__name__,'message':str(exc)[:1024]}
    finally:
        if ctx is not None:
            # Normal and infrastructure paths both close these; idempotent cleanup
            # also makes post-fault leak accounting explicit.
            for service in (ctx.state_exchange,ctx.channel):
                try:
                    if service is not None:service.close()
                except Exception:pass
    metrics=asdict(result.metrics) if result else None
    messages=[m.model_dump(mode='json') for m in result.messages] if result else []
    return {'task_id':row['task_id'],'fixture_source_task_id':row.get('fixture_source_task_id'),
        'chain_id':row['chain_id'],'domain':row['domain'],'pid':os.getpid(),
        'wall_ns':time.perf_counter_ns()-started,'process_cpu_ns':time.process_time_ns()-c0,
        'cpu_scope':'whole process; concurrent thread observations overlap and must not be summed',
        'success':bool(result and result.success),'error':error or (result.error if result else None),
        'state_consumed':bool(ctx and ctx.blackboard.get('state_consumed')),
        'actual_evidence_key_order':list(ctx.blackboard.get('evidence',{})) if ctx else [],
        'outputs':result.outputs if result else None,
        'memory_candidate_id':ctx.blackboard.get('memory_candidate_id') if ctx else None,
        'memory_adopted_id':ctx.blackboard.get('memory_adopted_id') if ctx else None,
        'reused_memory_ids':ctx.blackboard.get('reused_memory_ids',[]) if ctx else [],
        'metrics':metrics,'messages':messages,'fixture_calls':calls,'hook_evidence':hook_evidence,
        'provider_prompt_tokens':None,'provider_completion_tokens':None,
        'byte_scope':'native Channel payload/framing/ACK metrics; handshake excluded by native counter',
        'evidence_class':'OFFLINE_MECHANISM_ONLY'}

def chain(length,batch,index,cfg,templates,cell):
    memory=MemoryStore(build_embedding(cfg),dedup_threshold=1.01)
    rows=[];started=time.perf_counter_ns()
    try:
        for step in range(length):
            template=templates[(index*6+step)%len(templates)]
            task_id=f'{cell}-b{batch:02}-c{index:02}-s{step:02}'
            row=unit_row(template,task_id,step);row['fixture_source_task_id']=template['task_id']
            rows.append(run_unit(row,cfg,memory))
    finally:memory.close()
    return {'chain_index':index,'wall_ns':time.perf_counter_ns()-started,'tasks':rows}

def dump(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)

def cell(length,concurrency):
    stem=f'stress_v3_L{length}_K{concurrency}';cfg=config();templates=corpus()
    raw=OUT/'raw'/f'{stem}.jsonl';batches=OUT/'raw'/f'{stem}_batches.jsonl'
    initial_shm=set(Path('/dev/shm').iterdir());initial_fd=psutil.Process().num_fds()
    n=0;failures=0;state_failures=0;started=time.perf_counter();cpu0=time.process_time()
    with raw.open('x',encoding='utf-8') as f,batches.open('x',encoding='utf-8') as fb:
        # One native warmup batch, retained separately and excluded from endpoints.
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            warmed=list(executor.map(lambda i:chain(1,-1,i,cfg,templates,stem+'-warm'),range(concurrency)))
        dump(OUT/'raw'/f'{stem}_warmup.json',warmed)
        steady_fd=psutil.Process().num_fds()
        for batch in range(30):
            begin=time.perf_counter_ns();c0=time.process_time_ns()
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                chains=list(executor.map(lambda i:chain(length,batch,i,cfg,templates,stem),range(concurrency)))
            wall=time.perf_counter_ns()-begin
            task_rows=[r for ch in chains for r in ch['tasks']]
            for r in task_rows:
                r.update({'experiment_id':r['task_id'],'length':length,'concurrency':concurrency,'batch':batch})
                f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n');n+=1
                failures+=int(not r['success']);state_failures+=int(not r['state_consumed'])
            f.flush()
            br={'experiment_id':f'{stem}-b{batch:02}','length':length,'concurrency':concurrency,
                'batch':batch,'tasks_planned':length*concurrency,'tasks_recorded':len(task_rows),
                'wall_ns':wall,'process_cpu_ns':time.process_time_ns()-c0,
                'throughput_tasks_per_sec':len(task_rows)/(wall/1e9),
                'successes':sum(r['success'] for r in task_rows),
                'chain_wall_ns':[ch['wall_ns'] for ch in chains],
                'rss_bytes':psutil.Process().memory_info().rss,'fd_count':psutil.Process().num_fds()}
            fb.write(json.dumps(br,allow_nan=False)+'\n');fb.flush()
            if batch%5==0:print(json.dumps({'cell':stem,'batch':batch,'tasks':n,'failures':failures}),flush=True)
        steady_end_fd=psutil.Process().num_fds()
    import gc;gc.collect()
    new_shm=sorted(str(p) for p in set(Path('/dev/shm').iterdir())-initial_shm)
    dump(OUT/'analysis'/f'{stem}_status.json',{'status':'COMPLETE','planned_tasks':length*concurrency*30,
        'recorded_tasks':n,'failures':failures,'state_not_consumed':state_failures,
        'wall_sec':time.perf_counter()-started,'cpu_sec':time.process_time()-cpu0,
        'new_shm_after_cleanup':new_shm,'fd_before':initial_fd,'fd_after':psutil.Process().num_fds(),
        'fd_after_warmup_with_logs_open':steady_fd,'fd_after_last_batch_with_logs_open':steady_end_fd,
        'fd_steady_delta':steady_end_fd-steady_fd,
        'raw_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),'real_model_calls':0})

def continuous():
    cfg=config();templates=corpus();memory=MemoryStore(build_embedding(cfg),dedup_threshold=1.01)
    startfd=psutil.Process().num_fds();shm=set(Path('/dev/shm').iterdir());rows=[]
    try:
        with (OUT/'raw/stress_continuous_100.jsonl').open('x',encoding='utf-8') as f:
            for i in range(100):
                template=templates[i%len(templates)]
                row=unit_row(template,f'stress-continuous-{i:03}',i);row['fixture_source_task_id']=template['task_id']
                result=run_unit(row,cfg,memory);result['round']=i
                result['rss_bytes']=psutil.Process().memory_info().rss;result['fd_count']=psutil.Process().num_fds()
                result['memory_records']=memory.count();rows.append(result)
                f.write(json.dumps(result,ensure_ascii=False,allow_nan=False)+'\n');f.flush()
    finally:memory.close()
    dump(OUT/'analysis/stress_continuous_100_status.json',{'rounds':100,'failures':sum(not r['success'] for r in rows),
        'state_not_consumed':sum(not r['state_consumed'] for r in rows),'fd_before':startfd,
        'fd_after':psutil.Process().num_fds(),'new_shm_after_cleanup':sorted(str(p) for p in set(Path('/dev/shm').iterdir())-shm),
        'fd_first_round_with_log_open':rows[0]['fd_count'],'fd_last_round_with_log_open':rows[-1]['fd_count'],
        'fd_steady_delta':rows[-1]['fd_count']-rows[0]['fd_count'],
        'real_model_calls':0,'status':'COMPLETE'})

def main():
    p=argparse.ArgumentParser();p.add_argument('--length',type=int);p.add_argument('--concurrency',type=int)
    p.add_argument('--continuous',action='store_true');p.add_argument('--prepare-only',action='store_true');a=p.parse_args()
    if a.length:cell(a.length,a.concurrency);return
    if a.continuous:continuous();return
    plan={'lengths':LENGTHS,'concurrencies':CONCURRENCIES,'batches_per_cell':30,
        'planned_tasks':sum(LENGTHS)*sum(CONCURRENCIES)*30,'continuous_rounds':100,
        'configuration':'SS1 native four-role pipeline; local deterministic evidence fixture',
        'real_provider_tokens':None,'proxy_tokens':'native message text_tokens only, not billing',
        'worker_timeout_sec':1800,'resource_ceiling':'container 2CPU/2GiB, parent 200ms sampler',
        'warmup':'one K-concurrent one-task chain batch per cell, retained separately'}
    path=OUT/'analysis/stress_matrix_plan.json'
    if not path.exists():dump(path,plan)
    if a.prepare_only:return
    for length in LENGTHS:
        for concurrency in CONCURRENCIES:
            if (OUT/'analysis/STOP_OFFLINE_AFTER_CELL').exists():
                print('Explicit offline boundary stop requested; unfinished cells remain pending.',flush=True);return
            stem=f'stress_v3_L{length}_K{concurrency}';done=OUT/'analysis'/f'{stem}_status.json'
            if done.exists():continue
            if (OUT/'raw'/f'{stem}.jsonl').exists():raise RuntimeError(f'unfinished raw: {stem}; retain and audit before new version')
            command=[sys.executable,__file__,'--length',str(length),'--concurrency',str(concurrency)]
            started=time.perf_counter()
            with (OUT/'logs/mechanisms'/f'{stem}.stdout.log').open('x') as stdout,(OUT/'logs/mechanisms'/f'{stem}.stderr.log').open('x') as stderr:
                try:r=subprocess.run(command,stdout=stdout,stderr=stderr,timeout=1800);code=r.returncode;timeout=False
                except subprocess.TimeoutExpired:code=None;timeout=True
            dump(OUT/'analysis'/f'{stem}_process.json',{'returncode':code,'timed_out':timeout,'wall_sec':time.perf_counter()-started})
            print(json.dumps({'cell':stem,'returncode':code,'timed_out':timeout}),flush=True)
    if not (OUT/'analysis/stress_continuous_100_status.json').exists():continuous()

if __name__=='__main__':main()
