"""One public runtime diagnosis of wire/source differences; not experiment data."""
from __future__ import annotations
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
sys.path.append('/drivers')
from bootstrap import install_before_workers_and_external_imports
from calibrate_contract import deny_internet


def differences(a,b,path='$'):
    if type(a)!=type(b):return [{'path':path,'kind':'type','wire':a,'source':b}]
    if isinstance(a,dict):
        rows=[]
        for key in sorted(set(a)|set(b)):
            if key not in a or key not in b:rows.append({'path':path+'.'+key,'kind':'key','wire':a.get(key),'source':b.get(key)})
            else:rows.extend(differences(a[key],b[key],path+'.'+key))
        return rows
    if isinstance(a,list):
        if len(a)!=len(b):return [{'path':path,'kind':'length','wire_length':len(a),'source_length':len(b)}]
        return [d for index,(x,y) in enumerate(zip(a,b)) for d in differences(x,y,path+f'[{index}]')]
    return [] if a==b else [{'path':path,'kind':'value','wire':a,'source':b}]


def main():
    corpus=Path(sys.argv[1]);destination=Path(sys.argv[2])
    if destination.exists():raise RuntimeError('diagnosis output exists')
    destination.mkdir(parents=True)
    sys.addaudithook(deny_internet);install_before_workers_and_external_imports()
    from agent_runtime.config import Config,LLMConfig
    from agent_runtime.providers.base import LLMResponse
    from agent_runtime.runtime.builder import build_context,build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator
    from agent_runtime.protocol.schema_atoms import decode_value
    from formal_fact_calibration import make_task
    from prompt_adapter import model_prompt
    row=json.loads(corpus.read_text().splitlines()[0])
    assert row['split']=='pilot'
    cfg=Config(llm=LLMConfig(provider='mock',output_contract='none'),llm_advisory=False,llm_judge=False,
        llm_coalesce='off',llm_context_mode='inbound',text_style='lean',control_transport='uds',
        state_shared_memory=True,pipeline_topology='request_reply',task_retry_budget=0,
        reference_first_data_plane=True,structured_wire_codec='session_msgpack_dict',
        data_dir='/opt/lingxi/protocol-repair-wire-diagnosis-v1')
    cfg=cfg.with_overrides(embedding=replace(cfg.embedding,provider='docsem',dim=192))
    ctx=build_context(cfg,'structured',task_id=row['task_id'],use_memory=False,use_state_exchange=False,use_sandbox=False)
    captured=[]
    def complete(prompt,**kwargs):
        wire=prompt.split('Inbound message:\n',1)[1].split('\n\nTask: ',1)[0]
        frame=json.loads(wire);inline=frame.get('ip',{})['evidence']['observations']
        diff=differences(inline,row['observations'])
        try:
            decoded=decode_value(frame.get('ip',{}))['evidence']['observations']
            back=differences(decoded,row['observations'])
        except Exception as exc:back=[{'decode_error_type':type(exc).__name__,'message':str(exc)}]
        try:model_prompt(prompt,row['observations']);guard='PASS'
        except Exception as exc:guard={'type':type(exc).__name__,'message':str(exc)}
        captured.append({'wire':wire,'raw_wire_vs_source':diff,'decoded_wire_vs_source':back,
                         'source_observations':row['observations'],'existing_prompt_guard':guard})
        return LLMResponse(text=json.dumps(row['expected']),prompt_tokens=0,completion_tokens=0,
                           model='offline_wire_diagnosis',metadata={'usage_source':'offline_fixture_not_provider'})
    ctx.llm.complete=complete;orch=Orchestrator(build_default_agents(cfg))
    try:result=asyncio.run(orch.run_task(make_task(row),ctx))
    finally:orch.close_agent_processes()
    audit={'status':'DIAGNOSIS_ONLY_NOT_CALIBRATION_PASS','task_id':row['task_id'],'paid_api_calls':0,
           'pipeline_returned_success_without_guard':result.success,'pipeline_error':result.error,'captures':captured,
           'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (destination/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'captures':len(captured),'pipeline_error':result.error,
                      'raw_difference_count':len(captured[0]['raw_wire_vs_source']) if captured else None,
                      'decoded_differences':captured[0]['decoded_wire_vs_source'] if captured else None},ensure_ascii=False))


if __name__=='__main__':main()
