"""Prospective original-protocol task-unit plan; no runtime/provider imports."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import uuid
from contract import LABEL_ACTIONS

STUDY='protocol_repair_20261020_v1'
ARMS=('T0','S0','SS0','T1','S1','SS1')
ABLATIONS=('reference_first_off','session_dictionary_off','state_dedup_off','quantization_off',
           'delta_off','bm25_off','feedback_isolation_off','adoption_gate_off')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'))


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True);a=p.parse_args()
    if a.destination.exists():raise RuntimeError('plan destination exists; retain it and choose a new version')
    tasks=[json.loads(line) for line in (a.corpus/'tasks.jsonl').read_text(encoding='utf-8').splitlines()]
    formal=[r for r in tasks if r['split']=='formal'];public=[r for r in tasks if r['split']=='pilot']
    subset=[r for r in formal if r['observations']['checkpoint'] in (1,3,5)]
    assert len(formal)==120 and len(public)==24 and len(subset)==60
    contract_sha=hashlib.sha256(canonical(LABEL_ACTIONS).encode()).hexdigest()
    units=[]
    def phase(name,rows,arms,repeats,model='main',processes=False,intervention=None):
        for repeat in range(repeats):
            for index,row in enumerate(rows):
                rotation=(index+repeat)%len(arms)
                for arm in arms[rotation:]+arms[:rotation]:
                    unit={'study_id':STUDY,'phase':name,'task_id':row['task_id'],'chain_id':row['chain_id'],
                          'domain':row['domain'],'difficulty':row['difficulty'],'task_sha256':row['content_sha256'],
                          'arm':arm,'repeat':repeat,'model':model,'model_id':'qwen3.7-plus' if model=='main' else 'doubao-seed-2-0-mini-260215',
                          'processes':processes,'intervention':intervention,'seed':20261020,
                          'topology':'framework_inprocess' if name.startswith('external_') else 'agent_processes_uds' if processes else 'runtime_uds',
                          'effective_contract_sha256':contract_sha}
                    unit['run_id']=uuid.uuid5(uuid.NAMESPACE_URL,STUDY+'/'+canonical(unit)).hex
                    unit['unit_id']=unit['run_id']
                    memory_key=[STUDY,name,model,arm,repeat,row['chain_id']]
                    if intervention and intervention.startswith('memory_'):memory_key.append(row['task_id'])
                    unit['memory_namespace_sha256']=hashlib.sha256(canonical(memory_key).encode()).hexdigest()
                    units.append(unit)
    phase('public_calibration',public,ARMS,1)
    phase('main',formal,ARMS,3)
    phase('cross_model',subset,ARMS,2,model='cross')
    phase('multiprocess',subset,('S0','SS0','SS1'),2,processes=True)
    for framework in ('autogen','langgraph'):phase('external_'+framework,subset,('T0',),2)
    phase('ablation_baseline',subset,('SS1',),3)
    for item in ABLATIONS:phase('ablation_'+item,subset,('SS1',),3,intervention=item)
    for codec in ('pure_json','pure_msgpack'):phase('serialization_'+codec,subset,('SS1',),3,intervention=codec)
    for condition in ('none','related','unrelated','stale','wrong'):
        phase('memory_condition_'+condition,subset,('SS1',),3,intervention='memory_'+condition)
    assert len(units)==6504 and len({u['run_id'] for u in units})==6504
    main=[u for u in units if u['phase']=='main']
    assert len(main)==2160
    for repeat in range(3):
        for chain in {r['chain_id'] for r in formal}:
            for arm in ARMS:
                members=[u for u in main if u['repeat']==repeat and u['chain_id']==chain and u['arm']==arm]
                assert len(members)==6 and len({u['memory_namespace_sha256'] for u in members})==1
    a.destination.mkdir(parents=True)
    (a.destination/'task_unit_plan.json').write_text(json.dumps(units,indent=2)+'\n')
    (a.destination/'subset_60_task_ids.json').write_text(json.dumps([r['task_id'] for r in subset],indent=2)+'\n')
    budget={'status':'CANDIDATE_REQUIRES_PUBLIC_PILOT_BUDGET_GATE','planned_units':6504,'maximum_attempts_per_unit':3,
            'maximum_provider_attempts':19512,'max_tokens_per_attempt':1500,'timeout_seconds':60,
            'max_actual_utf8_request_bytes':131072,'temperature':0,'endpoint':'https://www.dmxapi.cn/v1/chat/completions',
            'currency_ceiling':'UNSPECIFIED','currency_cost_estimate':'UNKNOWN_PENDING_PUBLIC_USAGE_AND_VERIFIED_PRICE',
            'missing_failed_attempt_usage':'unknown; never zero or backfilled from a later study',
            'resume':'skip completed run IDs; unmatched attempt start or task start blocks automatic paid replay',
            'authorization':'existing full original-protocol goal; preparation is not a freeze or start signal'}
    (a.destination/'candidate_budget_rules.json').write_text(json.dumps(budget,indent=2)+'\n')
    receipt={'status':'CANDIDATE_PLAN_PREPARED_NOT_FROZEN_NOT_EXECUTED','study_id':STUDY,
             'units':len(units),'phase_counts':dict(Counter(u['phase'] for u in units)),
             'formal_tasks':120,'formal_chains':20,'public_tasks':24,'subset_tasks':60,
             'subset_rule':'checkpoints 1/3/5 across all formal chains, before new model outcomes',
             'six_arm_order':'balanced (task index + repeat) rotation; causal task order retained',
             'effective_contract_sha256':contract_sha,'tasks_sha256':sha(a.corpus/'tasks.jsonl'),
             'plan_sha256':sha(a.destination/'task_unit_plan.json'),'helper_sha256':sha(Path(__file__)),
             'paid_api_calls':0,'new_data_may_fill_old_missing_usage':False,
             'power_gate':'after public-only pilot: expand within original protocol before formal freeze or withhold noninferiority language',
             'remaining_gates':['complete recorder calibration','public real pilot and usage/time/price budget estimate',
                                'prospective power/statistical gate','freeze full code/config/corpus/analysis identities']}
    (a.destination/'preparation_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
