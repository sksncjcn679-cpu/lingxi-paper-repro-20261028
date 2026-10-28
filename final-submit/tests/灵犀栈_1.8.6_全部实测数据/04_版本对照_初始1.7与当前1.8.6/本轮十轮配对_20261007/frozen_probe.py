"""One isolated cell, shared evaluator, original runtime selected by PYTHONPATH."""
from pathlib import Path
import asyncio, hashlib, importlib.util, json, os, sys, time
from dataclasses import asdict, replace

BASE = Path(__file__).resolve().parent

def shared(name):
    path = BASE/'OS-SOTA1.7-exp/src/agent_runtime/evaluation'/f'{name}.py'
    spec = importlib.util.spec_from_file_location('proof_'+name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def fingerprint(tasks):
    return hashlib.sha256(json.dumps([{'topic':t.topic,'description':t.description,
        'payload':t.payload,'expected':t.expected,'tags':t.tags} for t in tasks],
        sort_keys=True,default=str).encode()).hexdigest()

def main():
    kind, arm, repeat = sys.argv[1], sys.argv[2], int(sys.argv[3])
    import agent_runtime
    from agent_runtime.config import load_config
    from agent_runtime.evaluation.runner import ExperimentConfig, run_once
    from agent_runtime.protocol.routine import reset_routine_registry
    result = {'runtime_file':agent_runtime.__file__, 'kind':kind,'arm':arm,'repeat':repeat}
    cw = shared('contest_workload')
    if kind == 'offline':
        mode, state = {'T':('text',False),'S':('structured',False),'SS':('structured',True)}[arm]
        cfg = ExperimentConfig(arm,arm,mode,state,False,True,text_style='lean',
            control_transport='uds',structured_wire_codec='session_msgpack',
            state_shared_memory=state,state_precision='adaptive')
        # Identical warm-up, then 3 measured executions in every cell.
        run_once(cfg,cw._self_contained_tasks,42)
        rows=[]
        for _ in range(3):
            reset_routine_registry()
            rows.append(run_once(cfg,cw._self_contained_tasks,42))
        result.update(task_sha=fingerprint(cw._self_contained_tasks()),rows=rows)
    elif kind == 'memory':
        on = arm == 'on'
        result.update(task_sha=fingerprint(cw._linked_tasks()+cw._extended_linked_tasks()),
            normal=cw._memory_phase(on),extended=cw._memory_phase(on,extended=True))
    elif kind == 'real_advisory':
        ext = shared('external_llm_benchmark')
        cfg = load_config(load_env=True).with_overrides(tokenizer_backend='tiktoken',
            llm_context_mode='inbound',llm_coalesce='off',llm_judge=False,text_style='lean',
            pipeline_topology='request_reply')
        cfg = cfg.with_overrides(llm=replace(cfg.llm,max_tokens=128,temperature=0.0,
            output_contract='none',max_retries=0,timeout_sec=60))
        calls=[]
        import agent_runtime.runtime.builder as builder
        original = builder.build_llm
        class Recorder:
            def __init__(self,p):
                self.p=p; self.name=p.name; self.model=p.model
                self.usage_present=None
                create=p._create_negotiated
                def observed_create(*args,**kwargs):
                    response=create(*args,**kwargs)
                    self.usage_present=response.usage is not None
                    return response
                p._create_negotiated=observed_create
            def complete(self,prompt,**kwargs):
                started=time.perf_counter()
                response=self.p.complete(prompt,**kwargs)
                calls.append({'prompt_sha':hashlib.sha256(prompt.encode()).hexdigest(),
                    'prompt_chars':len(prompt),'prompt_tokens':response.prompt_tokens,
                    'completion_tokens':response.completion_tokens,'model':response.model,
                    'latency_sec':time.perf_counter()-started,'metadata':response.metadata,
                    'provider_usage_present':self.usage_present})
                return response
        builder.build_llm=lambda config:Recorder(original(config))
        table,_=ext._variant_table(cfg)
        row=ext._run_variant_once(cfg,table[arm],repeat,'uds')
        result.update(task_sha=fingerprint(ext._tasks()),row=row,calls=calls,
            model=cfg.llm.model,base_url=cfg.llm.base_url,max_tokens=128,
            claim_boundary='Model replies are advisory; quality is not real model decision quality.')
    else:
        raise ValueError(kind)
    print(json.dumps(result,ensure_ascii=False,default=str),flush=True)

if __name__ == '__main__': main()
