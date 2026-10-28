import asyncio
import json
import pytest
from agent_runtime.agents.model_decision import parse_decision
from agent_runtime.config import Config, LLMConfig
from agent_runtime.evaluation.decision_workload import make_tasks, FixtureDecisionProvider
from agent_runtime.memory.models import MemoryRecord
from agent_runtime.memory.store import MemoryStore
from agent_runtime.providers.base import LLMResponse
from agent_runtime.providers.lexical_embedding import LexicalEmbedding
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator

class Fixed:
    name=model='fixed'
    def __init__(self,text): self.text=text
    def complete(self,*args,**kwargs): return LLMResponse(text=self.text,prompt_tokens=1,completion_tokens=1,model='fixed')

def execute(task, provider):
    cfg=Config(llm=LLMConfig(provider='mock'),llm_advisory=False,llm_coalesce='task')
    ctx=build_context(cfg,'structured',task_id=task.task_id,use_state_exchange=True)
    ctx.llm=provider
    # Critical decisions must bypass the empty advisory queue even when batching is enabled.
    result=asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task,ctx))
    return result,ctx

def test_model_text_changes_real_task_success_and_action():
    task=make_tasks()[0]
    good,ctx=execute(task,Fixed(json.dumps({'diagnosis':'port_conflict','action':'inspect_listener'})))
    assert good.success and ctx.blackboard['execution_provenance']['source']=='llm_decision'
    wrong,ctx=execute(task,Fixed(json.dumps({'diagnosis':'permission_denied','action':'inspect_permissions'})))
    assert not wrong.success
    assert wrong.outputs['answer']['diagnosis']=='permission_denied'
    empty,_=execute(task,Fixed(''))
    assert not empty.success

def test_fixture_all_eight_distinct_incidents():
    tasks=make_tasks()
    assert len({json.dumps(t.payload,sort_keys=True) for t in tasks})==8
    assert len({t.expected['answer']['diagnosis'] for t in tasks})==8
    for task in tasks:
        result,_=execute(task,FixtureDecisionProvider())
        assert result.success

def test_decision_contract_does_not_silently_repair():
    with pytest.raises(ValueError): parse_decision('{"diagnosis":"port_conflict"}')
    with pytest.raises(ValueError): parse_decision('{"diagnosis":"unknown","action":"inspect_listener"}')
    # Valid but wrong action remains wrong for the task verifier to catch.
    assert parse_decision('{"diagnosis":"port_conflict","action":"inspect_disk"}')['action']=='inspect_disk'

def test_lineage_persists_and_bounds_cycles(tmp_path):
    path=str(tmp_path/'memory.sqlite')
    store=MemoryStore(LexicalEmbedding(dim=64),db_path=path,dedup_threshold=2)
    a=MemoryRecord(memory_id='a',source_agent='reviewer',task_topic='os',summary='root')
    b=MemoryRecord(memory_id='b',source_agent='reviewer',task_topic='os',summary='child',derived_from=['a','missing'])
    c=MemoryRecord(memory_id='c',source_agent='reviewer',task_topic='os',summary='grandchild',derived_from=['b','c'])
    for r in (a,b,c): store.write(r)
    store.close()
    store=MemoryStore(LexicalEmbedding(dim=64),db_path=path)
    lineage=store.get_lineage('c')
    assert {n['memory_id'] for n in lineage['nodes']}=={'a','b','c'}
    assert lineage['missing']==['missing']
    assert store.get_lineage('c',max_nodes=1)['truncated']
    store.close()

def test_unsupported_landlock_never_executes_attack_battery(monkeypatch):
    from agent_runtime.evaluation import sandbox_security_benchmark as suite
    class Probe:
        last_guard={'seccomp':True,'landlock_abi':0}
        def __init__(self,*args,**kwargs): pass
        def run(self,*args,**kwargs):
            return type('Result',(),{'exit_code':0})()
    monkeypatch.setattr(suite,'CodeActExecutor',Probe)
    monkeypatch.setattr(suite,'_attacks',lambda: pytest.fail('unsafe battery executed'))
    result=suite.run_sandbox_security_benchmark(repeat=1)
    assert result['status']=='unsupported_security_layer' and not result['all_contained']

@pytest.mark.parametrize('mismatch', ['source_tree_sha256', 'platform', 'host_identity'])
def test_scorecard_rejects_stale_or_other_host_guard(tmp_path, monkeypatch, mismatch):
    from agent_runtime.evaluation import competition_scorecard as score
    env = {'source_tree_sha256':'frozen','platform':'target','host_identity':'host-a'}
    monkeypatch.setattr(score,'_env_info',lambda:env)
    directory = tmp_path/'results'/'20261001-sandbox_security'
    directory.mkdir(parents=True)
    payload = {'env':{**env,mismatch:'different'},'guard_report':{'seccomp':True,'landlock_abi':6},
               'all_contained':True,'all_functional':True,'no_file_escaped':True}
    (directory/'summary.json').write_text(json.dumps(payload))
    assert not score._kernel_guard_passed(tmp_path)
    payload['env']=env
    (directory/'summary.json').write_text(json.dumps(payload))
    assert score._kernel_guard_passed(tmp_path)
    payload['all_contained']=False
    (directory/'summary.json').write_text(json.dumps(payload))
    assert not score._kernel_guard_passed(tmp_path)

def test_invalid_candidate_transport_config_rejected():
    with pytest.raises(ValueError): Config(large_state_handoff='pretend_shm')

def test_retrieval_multi_relevant_recall_and_truncated_mrr():
    from agent_runtime.evaluation.retrieval_expanded import ranking_metrics
    partial=ranking_metrics([2,3,4,0],{0,2})
    assert partial['recall_at_3']==0.5 and partial['hit_at_3']==1
    assert partial['mrr']==1
    absent=ranking_metrics([3,4],{0,2})
    assert absent['mrr']==0 and absent['recall_at_3']==0

def test_consumed_procedure_uses_standard_adoption_and_feedback():
    from agent_runtime.agents.model_decision import diagnose, PROCEDURE
    from agent_runtime.agents.reviewer import ReviewerSummarizerAgent
    from agent_runtime.runtime.task import VerdictResult
    task=make_tasks()[0]
    cfg=Config(llm_advisory=False)
    store=MemoryStore(LexicalEmbedding(dim=64))
    record=MemoryRecord(memory_id='parent',source_agent='reviewer',task_topic=task.topic,summary='procedure',solution=PROCEDURE)
    store.write(record)
    ctx=build_context(cfg,'structured',task_id=task.task_id,use_memory=True,memory=store)
    ctx.llm=FixtureDecisionProvider()
    ctx.blackboard.update(memory_candidate_id='parent',reused_solution=PROCEDURE,plan={'operation':'llm_diagnose','operands':['observations']})
    value=diagnose(task.payload['facts'],ctx)
    assert ctx.blackboard['reused_memory_ids']==['parent']
    ReviewerSummarizerAgent()._memorize(task,value,VerdictResult(success=True,quality=1),ctx)
    assert ctx.metrics.snapshot.memory_used==ctx.metrics.snapshot.memory_effective==1
    assert store.get('parent').reuse_count==1
    assert any(r.derived_from==['parent'] for r in store.all_records())
    store.close()

@pytest.mark.skipif(__import__('sys').platform != 'linux', reason='Linux seccomp subprocess')
def test_signal_fallback_when_landlock_absent():
    # signal 0 checks permission only, so even a failed filter cannot kill the parent.
    import subprocess, sys
    code = '''import os,tempfile
from agent_runtime.sandbox.kernel_guard import apply_in_current_process
report=apply_in_current_process(tempfile.mkdtemp())
if report['seccomp'] and report['landlock_abi'] < 6:
    try: os.kill(os.getppid(),0)
    except PermissionError: print('denied')
    else: raise AssertionError('outward signal permitted')
else: print('scoped_or_unsupported')
'''
    result=subprocess.run([sys.executable,'-c',code],text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip() in {'denied','scoped_or_unsupported'}
