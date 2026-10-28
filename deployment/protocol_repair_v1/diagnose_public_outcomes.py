"""Bounded, read-only attribution of public quality failures, never prompt tuning."""
import json
from pathlib import Path
from collections import Counter
import sys
sys.stdout.reconfigure(encoding='utf-8')
base=Path(__file__).resolve().parents[2]/'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'
execution=json.loads((base/'public_pilot_v1/execution.json').read_text(encoding='utf-8'))
assert execution.get('finished_utc') and execution['status']!='RUNNING'
tasks=[json.loads(x) for x in (base/'raw/task_runs.jsonl').read_text(encoding='utf-8').splitlines()]
cases={r['task_id']:r for r in map(json.loads,(base/'corpus/candidate_v2/tasks.jsonl').read_text(encoding='utf-8').splitlines())}
failed={r['run_id']:r for r in tasks if not r['success']}
responses={}
for line in (base/'raw/model_io.jsonl').read_text(encoding='utf-8').splitlines():
    row=json.loads(line)
    if row.get('run_id') in failed and row.get('direction')=='response':
        value=row.get('response',{})
        if isinstance(value,dict):
            choices=value.get('choices') or []
            text=(choices[0].get('message') or {}).get('content') if choices else None
        else:text=str(value)
        responses[row['run_id']]=text
details=[]
for rid,row in failed.items():
    raw=responses.get(rid)
    try:decision=json.loads(raw) if raw else None
    except ValueError:decision=None
    expected=cases[row['task_id']]['expected']
    details.append({'run_id':rid,'task_id':row['task_id'],'arm':row['arm'],'error':row.get('error'),
        'expected':expected,'response_json':decision,'response_text_if_not_json':raw if decision is None else None,
        'diagnosis_matches':isinstance(decision,dict) and decision.get('diagnosis')==expected['diagnosis'],
        'action_matches':isinstance(decision,dict) and decision.get('action')==expected['action']})
report={'scope':'new public cohort only; response attribution, no rerun or prompt/label changes',
        'terminal_units':len(tasks),'successful_units':sum(r['success'] for r in tasks),'failed_units':len(failed),
        'failure_counts_by_task':dict(Counter(r['task_id'] for r in failed.values())),
        'failure_counts_by_arm':dict(Counter(r['arm'] for r in failed.values())),
        'diagnosis_matches_among_failed':sum(x['diagnosis_matches'] for x in details),
        'action_matches_among_failed':sum(x['action_matches'] for x in details),'details':details}
dest=base/'analysis/public_quality_attribution_v1'
dest.mkdir(exist_ok=False)
with (dest/'findings.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
print(json.dumps({k:v for k,v in report.items() if k!='details'},ensure_ascii=False))
for item in details[:8]:print(json.dumps(item,ensure_ascii=False))
