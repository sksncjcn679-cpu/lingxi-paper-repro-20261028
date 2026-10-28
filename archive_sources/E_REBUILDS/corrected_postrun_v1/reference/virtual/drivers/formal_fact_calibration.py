"""Eight public tasks through all six real runtime paths, deterministic fixture only; never formal model evidence."""
from pathlib import Path
from dataclasses import replace
import asyncio
import hashlib
import json
import sys

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
from agent_runtime.config import Config,LLMConfig
from agent_runtime.providers.base import LLMResponse
from agent_runtime.providers.factory import build_embedding
from agent_runtime.runtime.task import Task,VerdictResult
from agent_runtime.runtime.builder import build_context,build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator
from agent_runtime.memory.store import MemoryStore
from formal_prompt_adapter import model_prompt

OUT=Path('/evidence/formal_20261019')
ARMS={'T0':('text',False,False),'S0':('structured',False,False),'SS0':('structured',True,False),
      'T1':('text',False,True),'S1':('structured',False,True),'SS1':('structured',True,True)}

def make_task(row):
    expected={**row['expected'],'probe':row['observations']['checks'][row['expected']['action']]}
    def verify(outputs):
        ok=outputs.get('answer')==expected
        return VerdictResult(success=ok,quality=float(ok),detail={'exact_contract_match':ok})
    return Task(task_id=row['task_id'],parent_task_id=(row['parent_ids']or[None])[0],topic=row['chain_id'],
        description='Diagnose current checkpoint and select its read-only fixture evidence check.',
        payload={'question':'llm_diagnose','operands':['observations','procedure'],'defect':row['domain'],
                 'facts':{'observations':row['observations'],'procedure':row['procedure']}},
        expected={'answer':expected},verifier=verify,tags=[row['domain'],row['difficulty']])

def main():
    rows=[json.loads(s) for s in (OUT/'corpus/candidate_v3/tasks.jsonl').read_text().splitlines()]
    public=[r for r in rows if r['split']=='pilot' and r['observations']['checkpoint']<=2]
    assert len(public)==8
    base=Config(llm=LLMConfig(provider='mock',output_contract='none'),llm_advisory=False,llm_judge=False,
        llm_coalesce='off',llm_context_mode='inbound',text_style='lean',control_transport='uds',state_shared_memory=True,
        pipeline_topology='request_reply',task_retry_budget=0,data_dir='/opt/lingxi/formal-calibration-data')
    base=base.with_overrides(embedding=replace(base.embedding,provider='docsem',dim=256))
    stores={};failures=[];count=0
    try:
        with (OUT/'raw/fact_equivalence_calibration.jsonl').open('x') as output:
            for row in public:
                for arm,(mode,state,mem) in ARMS.items():
                    memory=None
                    if mem:
                        key=(arm,row['chain_id'])
                        if key not in stores:stores[key]=MemoryStore(build_embedding(base))
                        memory=stores[key]
                    ctx=build_context(base,mode,task_id=row['task_id'],use_memory=mem,use_state_exchange=state,use_sandbox=False,memory=memory)
                    captured=[]
                    def complete(prompt,**kwargs):
                        rewritten,audit=model_prompt(prompt,row['observations'])
                        captured.append({'prompt':rewritten,'prompt_sha256':hashlib.sha256(rewritten.encode()).hexdigest(),'fact_audit':audit})
                        return LLMResponse(text=json.dumps(row['expected']),prompt_tokens=0,completion_tokens=0,
                            model='deterministic_calibration_fixture',metadata={'usage_source':'offline_fixture_not_provider'})
                    ctx.llm.complete=complete
                    result=asyncio.run(Orchestrator(build_default_agents(base)).run_task(make_task(row),ctx))
                    evidence={'task_id':row['task_id'],'arm':arm,'pipeline_success':result.success,
                        'state_consumed':bool(ctx.blackboard.get('state_consumed')),'error':result.error,
                        'captured_calls':captured,'scope':'offline recording calibration, not model quality or provider usage'}
                    if not result.success or not captured or (state and not evidence['state_consumed']):failures.append({'task_id':row['task_id'],'arm':arm,'error':result.error})
                    output.write(json.dumps(evidence,ensure_ascii=False)+'\n');output.flush();count+=1
    finally:
        for store in stores.values():store.close()
    report={'public_tasks':8,'runtime_units':count,'failures':failures,'status':'PASS' if not failures else 'FAILED',
        'real_model_calls':0,'provider_usage_calibrated':False,
        'main_prompt_condition':'native representations, one complete copy of business observations; resolve actual references when absent inline'}
    (OUT/'analysis/fact_equivalence_calibration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)
    if failures:raise SystemExit(1)

if __name__=='__main__':main()
