"""Actual openEuler runtime/framework scaffold calibration with offline fixtures.

Never loads a key or creates an Internet socket. This does not measure provider
usage or model quality. Public cases only, in an independent output namespace.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

sys.path.insert(0,'/opt/lingxi/formal-checkout/src')
sys.path.append('/drivers')
from bootstrap import install_before_workers_and_external_imports
from oracle import check


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deny_internet(event,args):
    if event=='socket.__new__' and args[1] in (socket.AF_INET,socket.AF_INET6):
        raise RuntimeError('offline calibration prohibits IPv4/IPv6 socket creation')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--corpus',required=True,type=Path)
    parser.add_argument('--destination',required=True,type=Path);args=parser.parse_args()
    if args.destination.exists():raise RuntimeError('new calibration version required; refusing overwrite')
    args.destination.mkdir(parents=True)
    source_hashes={str(p):digest(p) for p in Path(__file__).parent.glob('*.py')}
    input_hashes={p.name:digest(p) for p in args.corpus.iterdir() if p.is_file()}
    sys.addaudithook(deny_internet)
    contract=install_before_workers_and_external_imports()
    from agent_runtime.config import Config,LLMConfig
    from agent_runtime.providers.base import LLMResponse
    from agent_runtime.providers.factory import build_embedding
    from agent_runtime.runtime.builder import build_context,build_default_agents
    from agent_runtime.runtime.orchestrator import Orchestrator
    from agent_runtime.memory.store import MemoryStore
    from agent_runtime.agents.model_decision import LABEL_ACTIONS,parse_decision
    from formal_fact_calibration import ARMS,make_task
    from formal_prompt_adapter import model_prompt
    import formal_external_adapters as external
    # External adapter imports capture instructions; bootstrap has already run.
    # Use the same independent read-only fixture checker as this candidate study.
    external.check=check
    allowed=dict(LABEL_ACTIONS)
    assert len(allowed)==24
    captured_contract=json.loads(external.DECISION_INSTRUCTION.split('Allowed pairs: ',1)[1])
    assert captured_contract==allowed
    for label,action in allowed.items():assert parse_decision(canonical({'diagnosis':label,'action':action}))=={'diagnosis':label,'action':action}
    tasks=[json.loads(line) for line in (args.corpus/'tasks.jsonl').read_text().splitlines()]
    selection={'service':(1,3,5),'migration':(2,4,6),'resources':(4,6),'network_permissions':(2,5)}
    public=[r for r in tasks if r['split']=='pilot' and r['observations']['checkpoint'] in selection[r['domain']]]
    assert len(public)==10
    base=Config(llm=LLMConfig(provider='mock',output_contract='none'),llm_advisory=False,llm_judge=False,
                llm_coalesce='off',llm_context_mode='inbound',text_style='lean',control_transport='uds',
                state_shared_memory=True,pipeline_topology='request_reply',task_retry_budget=0,
                reference_first_data_plane=True,structured_wire_codec='session_msgpack_dict',
                state_precision='adaptive',state_deduplicate=True,state_versioned_delta=True,
                random_seed=20261020,data_dir='/opt/lingxi/protocol-repair-offline-calibration-v1')
    stores={};rows=[];failures=[]
    start=time.monotonic()
    stream=(args.destination/'runtime_units.jsonl').open('x',encoding='utf-8')
    def record(row):
        row['effective_contract_sha256']=contract['effective_contract_sha256']
        row['provider_calls']=0;row['scope']='offline scaffold calibration, not measured model quality or usage'
        rows.append(row);stream.write(canonical(row)+'\n');stream.flush()
        if not row['passed']:failures.append({k:row.get(k) for k in ('unit_id','task_id','arm','framework','error')})
    def native(row,arm,processes=False,wrong=False):
        mode,state,use_memory=ARMS[arm]
        dim={'small':192,'medium':768,'large':1536}[row['state_spec']['size_class']]
        cfg=base.with_overrides(embedding=replace(base.embedding,provider='docsem',dim=dim),agent_processes=processes)
        key=(arm,row['chain_id'],processes,wrong)
        memory=None
        if use_memory:
            if key not in stores:
                memory_cfg=cfg.with_overrides(embedding=replace(cfg.embedding,dim=256))
                stores[key]=MemoryStore(build_embedding(memory_cfg))
            memory=stores[key]
        ctx=None;orch=None;captured=[];pids={}
        selected=dict(row['expected'])
        if wrong:selected['action']=next(a for a in allowed.values() if a!=selected['action'])
        unit={'unit_id':f"offline-{row['task_id']}-{arm}-mp{int(processes)}-wrong{int(wrong)}",
              'task_id':row['task_id'],'arm':arm,'processes':processes,'wrong_action_fixture':wrong,
              'passed':False,'selected_response':selected}
        try:
            ctx=build_context(cfg,mode,task_id=row['task_id'],use_memory=use_memory,
                              use_state_exchange=state,use_sandbox=False,memory=memory)
            def complete(prompt,**kwargs):
                rewritten,audit=model_prompt(prompt,row['observations'])
                # Check the actual allowed set in every representation, including fork RPC.
                encoded=rewritten.split('Allowed pairs: ',1)[1]
                actual_contract,_=json.JSONDecoder().raw_decode(encoded)
                assert actual_contract==allowed
                captured.append({'prompt_sha256':hashlib.sha256(rewritten.encode()).hexdigest(),
                                 'utf8_bytes':len(rewritten.encode()),'fact_audit':audit,
                                 'effective_contract_verified':True})
                return LLMResponse(text=canonical(selected),prompt_tokens=0,completion_tokens=0,
                                   model='offline_contract_fixture',metadata={'usage_source':'offline_fixture_not_provider'})
            ctx.llm.complete=complete
            orch=Orchestrator(build_default_agents(cfg))
            result=asyncio.run(orch.run_task(make_task(row),ctx))
            host=getattr(orch,'_host',None)
            if host:pids=host.pids()
            provenance=ctx.blackboard.get('execution_provenance') or {}
            unit.update(pipeline_success=result.success,error=result.error,captured_calls=captured,
                        state_consumed=bool(ctx.blackboard.get('state_consumed')),worker_pids=pids,
                        execution_provenance=provenance)
            # No label->action fallback may turn this deliberately wrong action into truth.
            unit['passed']=(bool(result.success)==(not wrong) and len(captured)==1
                            and provenance.get('action')==selected['action']
                            and provenance.get('probe')==row['observations']['checks'][selected['action']]
                            and (not state or unit['state_consumed']))
        except Exception as exc:unit['error']={'type':type(exc).__name__,'message':str(exc)}
        finally:
            if orch:orch.close_agent_processes()
            unit['workers_remaining']=[pid for pid in pids.values() if Path(f'/proc/{pid}').exists()]
            if unit['workers_remaining']:unit['passed']=False
            record(unit)
    try:
        for row in public:
            for arm in ARMS:native(row,arm)
            for arm in ('S0','SS0','SS1'):native(row,arm,True)
        negative=public[0]
        for arm in ARMS:native(negative,arm,wrong=True)
        for framework in ('autogen','langgraph'):
            for ordinal,row in enumerate(public+[negative]):
                wrong=ordinal==len(public);selected=dict(row['expected'])
                if wrong:selected['action']=next(a for a in allowed.values() if a!=selected['action'])
                def complete(messages,metadata):
                    content=messages[-1]['content'];head,data=content.split('\nTask handoff: ',1)
                    assert json.loads(head.split('Allowed pairs: ',1)[1])==allowed
                    assert json.loads(data)['evidence']['observations']==row['observations']
                    return canonical(selected)
                result=asyncio.run(external.run_external(framework,row,complete,
                    unit_id=f"offline-{framework}-{row['task_id']}-wrong{int(wrong)}"))
                record({'unit_id':result['unit_id'],'task_id':row['task_id'],'framework':framework,
                        'wrong_action_fixture':wrong,'pipeline_success':result['success'],
                        'role_order':result['role_order'],'fact_audit':result['fact_audit'],
                        'fixture_check':result['fixture_check'],'answer':result['answer'],'error':result['errors'],
                        'passed':result['success']==(not wrong) and result['logical_model_invocations']==1
                            and result['answer']['action']==selected['action']
                            and result['fixture_check']['current_record_matches_check']==(not wrong)})
        command_rows=[]
        for row in public:
            command=shlex.split(row['observations']['checks'][row['expected']['action']])
            command[0]=sys.executable
            # Source strings point at the prospective location, which is not yet frozen.
            command[command.index('--tasks')+1]=str(args.corpus/'tasks.jsonl')
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            result_check=json.loads(result.stdout) if result.returncode==0 else None
            passed=result.returncode==0 and result_check['current_record_matches_check'] and result_check['read_only']
            command_rows.append({'task_id':row['task_id'],'command':command,'exit_code':result.returncode,
                                 'check':result_check,'stderr':result.stderr,'passed':passed})
            if not passed:failures.append({'task_id':row['task_id'],'scope':'executable_read_only_check'})
        (args.destination/'executable_checks.json').write_text(json.dumps(command_rows,indent=2)+'\n')
    finally:
        stream.close()
        for store in stores.values():store.close()
    assert input_hashes=={p.name:digest(p) for p in args.corpus.iterdir() if p.is_file()}
    result={'status':'PASS_OFFLINE_CONTRACT_FACTS_NOT_PROVIDER_CALIBRATION' if not failures else 'FAIL_RETAINED',
            'public_tasks':len(public),'runtime_units':len(rows),'native_units':sum('arm' in r for r in rows),
            'external_units':sum('framework' in r for r in rows),'failures':failures,
            'paid_api_calls':0,'provider_usage_calibrated':False,'wall_seconds':time.monotonic()-start,
            'effective_contract':contract,'business_source_bytes_modified':False,
            'external_check_override':'same independently authored measurement oracle; fixed before all framework units',
            'input_sha256':input_hashes,'helper_sha256':source_hashes,'inputs_unchanged':True,
            'network_guard':'sys audit hook rejects IPv4/IPv6 socket construction; AF_UNIX allowed'}
    (args.destination/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('effective_contract','input_sha256','helper_sha256')},ensure_ascii=False))
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
