"""User-authorized deterministic validation and labels; no claim of human review or ratings."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import shutil
import subprocess
import sys

from formal_fixture_checks import automatic_label,check
from formal_corpus import ACTION

OUT=Path('/evidence/formal_20261019')
SOURCE=OUT/'corpus/candidate_v3'
TARGET=OUT/'corpus/automatic_validated_v1'

def sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def main():
    tasks=[json.loads(s) for s in (SOURCE/'tasks.jsonl').read_text().splitlines()]
    queries=[json.loads(s) for s in (SOURCE/'queries.jsonl').read_text().splitlines()]
    errors=[]
    for row in tasks:
        observed=automatic_label(row)
        if observed!=row['expected']['diagnosis'] or ACTION[observed]!=row['expected']['action']:
            errors.append({'task_id':row['task_id'],'issue':'author proposal disagrees with independent evidence parser'})
        matched=[action for action in ACTION.values() if check(row,action)['current_record_matches_check']]
        if matched!=[ACTION[observed]]:errors.append({'task_id':row['task_id'],'issue':'read-only checks not uniquely consistent'})
        if any(k in row['observations'] for k in ['expected','diagnosis','answer','label']):
            errors.append({'task_id':row['task_id'],'issue':'answer leakage field in observations'})
        previous=next((t for t in tasks if t['task_id'] in row['parent_ids']),None)
        if row['split']=='formal' and previous and previous['expected']==row['expected']:
            errors.append({'task_id':row['task_id'],'issue':'same consecutive final diagnosis/action'})
    if errors:
        (OUT/'analysis/automatic_validation_failed.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2))
        raise RuntimeError(f'automatic validation failed: {errors[:3]}')
    TARGET.mkdir(exist_ok=False)
    task_by_id={r['task_id']:r for r in tasks}
    labels=[]
    for row in tasks:
        row['author_candidate_content_sha256']=row.pop('content_sha256')
        row['human_review']='NOT_PERFORMED_USER_AUTHORIZED_AUTOMATIC_VALIDATION'
        row['automatic_validation']={'validator':'current_evidence_parser_and_fixture_checks_v1','passed':True,
            'human_equivalence_claim':False}
        row['content_sha256']=sha(row)
    for query in queries:
        task=task_by_id[query['source_task_id']]
        no_answer=query['proposed_no_answer']
        relevant=[] if no_answer else [r['task_id'] for r in tasks if r['split']=='formal' and r['chain_id']==task['chain_id'] and r['observations']['checkpoint']<task['observations']['checkpoint']]
        labels.append({'query_id':query['query_id'],'no_answer':not relevant,'relevant_source_task_ids':relevant,
            'label_source':'deterministic_causal_component_procedure_compatibility_v1',
            'rule':'program bound to same component/chain; only prior checkpoints; unavailable firmware-attestation intent has no eligible program',
            'human_labels':False,'scope_limitation':'rule-based compatibility labels, not human-assessed semantic relevance'})
        query['human_label_1']=None;query['human_label_2']=None
        query['adjudicated_labels']=None
        query['automatic_labels']=labels[-1]
    for name,values in [('tasks.jsonl',tasks),('queries.jsonl',queries),('labels.jsonl',labels)]:
        (TARGET/name).write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in values))
    (TARGET/'label_policy.md').write_text('用户明确取消人工标注/评分。本集采用任务当前证据的确定性解析与只读检查；检索标签采用同组件/同链前序程序兼容规则。未进行人工语义相关性评审，不报告人工盲评分、评审一致性或裁决。自动规则的局限需随 Recall/MRR 等指标共同报告。\n')
    record={'status':'AUTOMATIC_VALIDATION_PASSED','tasks':len(tasks),'formal_tasks':120,'pilot_tasks':24,
        'queries':len(queries),'no_answer_queries':sum(r['no_answer'] for r in labels),
        'query_domain_counts':dict(Counter(r['domain'] for r in queries)),
        'formal_difficulty_by_domain':{domain:dict(Counter(r['difficulty'] for r in tasks if r['domain']==domain and r['split']=='formal')) for domain in ['service','migration','resources','network_permissions']},
        'formal_state_applicable':sum(r['state_spec']['applicable'] for r in tasks if r['split']=='formal'),
        'no_formal_model_outcomes_used':True,'human_review':False,'user_exception':'manifests/protocol_changes.md',
        'corpus_path':str(TARGET),'runtime_measurement_audit':'PENDING','model_budget_freeze':'PENDING'}
    (OUT/'analysis/automatic_corpus_validation.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
    (TARGET/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(TARGET.iterdir()) if p.is_file()))
    print(json.dumps(record,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
