"""Evaluate the production candidate task path with identical socket-copy and mmap inputs."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
import numpy as np
from agent_runtime.agents.candidate_workflow import run_candidate_workflow
from agent_runtime.config import Config
from agent_runtime.evaluation.runner import _env_info, _allocate_result_dir, _write_checksum_manifest
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.runtime.task import Task, VerdictResult
from agent_runtime.state_exchange.fd_passing import fd_passing_supported

def candidates(n):
    base=['service cannot bind address already in use port 8080', 'systemd permission denied state directory',
          'native CPython ABI mismatch requires rebuild', 'DNS resolver name lookup failure',
          'filesystem full prevents log write', 'kernel OOM killer memory limit']
    return [base[i % len(base)] + f' document identifier {i}' for i in range(n)]

def run(repeat=5):
    rows=[]; workflow=[]
    for count in (100, 1500):
        documents=candidates(count); query='service cannot bind port address already in use'
        reference=None
        for mode in ('copy','sealed_fd'):
            if mode=='sealed_fd' and not fd_passing_supported():
                rows.append({'candidates':count,'mode':mode,'supported':False}); continue
            for rep in range(repeat):
                r=run_candidate_workflow(query,documents,mode=mode)
                if reference is None: reference=r['ranked_indices']
                assert r['ranked_indices']==reference
                rows.append({'candidates':count,'repeat':rep,'supported':True,'ranking_matches_copy':True,**r})
        for mode in ('copy','sealed_fd'):
            if mode=='sealed_fd' and not fd_passing_supported(): continue
            expected=reference[:3]
            def verifier(outputs, expected=expected):
                ok=outputs.get('answer')==expected
                return VerdictResult(success=ok,quality=float(ok))
            task=Task(topic='candidate-document-retrieval',description='Rank candidate documents using received candidate vectors.',
                      payload={'question':'rank_candidates','operands':['query','candidates'],'facts':{'query':query,'candidates':documents}},
                      expected={'answer':expected},verifier=verifier)
            cfg=Config(large_state_handoff=mode,llm_advisory=False)
            ctx=build_context(cfg,'structured',task_id=task.task_id,use_state_exchange=True)
            result=asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task,ctx))
            assert result.success, result.error
            workflow.append({'candidates':count,'mode':mode,'success':result.success,'answer':result.outputs['answer'],
                             'handoff':ctx.blackboard['candidate_handoff'],'full_task_latency_sec':result.metrics.latency_sec})
    summary=[]
    for count in (100,1500):
        for mode in ('copy','sealed_fd'):
            measured=[r for r in rows if r['candidates']==count and r['mode']==mode and r.get('supported')]
            if not measured:
                summary.append({'candidates':count,'mode':mode,'supported':False}); continue
            summary.append({'candidates':count,'mode':mode,'supported':True,'runs':len(measured),
                'state_bytes':measured[0]['state_bytes'],'socket_bytes_mean':float(np.mean([r['socket_bytes'] for r in measured])),
                'e2e_p50_ms':float(np.percentile([r['end_to_end_ms'] for r in measured],50)),
                'e2e_p95_ms':float(np.percentile([r['end_to_end_ms'] for r in measured],95))})
    return {'suite':'candidate_workflow','generated_at_utc':datetime.now(timezone.utc).isoformat(),'env':_env_info(),
            'methodology':{'repeat':repeat,'default_threshold_bytes':1<<20,'small_state_policy':'copy unless explicitly opted in',
                'receiver_use':'read-only mmap -> numpy view -> cosine ranking -> task answer',
                'physical_accounting':'state materialization reported separately; socket bytes include header, fd marker and ACK, not memory pages',
                'latency':'includes both process startup and embedding, not only handoff syscall'},'summary':summary,'raw':rows,'task_workflows':workflow}

def run_cli(args):
    p=run(args.repeat); out=_allocate_result_dir('results','candidate_workflow')
    (out/'summary.json').write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Candidate vectors in the task workflow','','| Candidates | Mode | State B | Socket B | E2E p50/p95 ms |','|---|---|---:|---:|---:|']
    for r in p['summary']:
        if not r.get('supported'): lines.append(f"| {r['candidates']} | {r['mode']} | unsupported | | |"); continue
        lines.append(f"| {r['candidates']} | {r['mode']} | {r['state_bytes']} | {r['socket_bytes_mean']:.0f} | {r['e2e_p50_ms']:.2f}/{r['e2e_p95_ms']:.2f} |")
    lines+=['',p['methodology']['receiver_use'],p['methodology']['physical_accounting'],f"Source SHA-256: `{p['env']['source_tree_sha256']}`",'']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8'); _write_checksum_manifest(out)
    print('\n'.join(lines)); print(f'Results: {out}'); return 0
