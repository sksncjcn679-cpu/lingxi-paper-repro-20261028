"""Prepare a separate, paired replication; never modify a historical input."""
from pathlib import Path
from collections import Counter
import ast
import hashlib
import json
import shutil

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT.parent/'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'
NEW=ROOT/'main_batch'
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2)
def main():
    NEW.mkdir(exist_ok=False)
    for name in ['manifests','corpus','raw','logs','analysis','memory','runtime_data']:(NEW/name).mkdir()
    native=ROOT/'code/native';native.mkdir(exist_ok=False)
    src=OLD/'formal_snapshot_v1/drivers'
    for p in src.glob('*.py'):shutil.copyfile(p,native/p.name)
    shutil.copyfile(OLD/'corpus/candidate_v2/tasks.jsonl',NEW/'corpus/tasks.jsonl')
    original=json.loads((OLD/'manifests/formal_plan.json').read_text(encoding='utf-8'))
    public_ids=[u['task_id'] for u in original if u['phase']=='public_calibration']
    first=public_ids[0]
    selected=[u for u in original if u['phase']=='main' or (u['phase']=='public_calibration' and u['task_id']==first)]
    assert Counter(u['phase'] for u in selected)=={'main':2160,'public_calibration':6}
    for u in selected:
        oldid=u['run_id'];u['origin_run_id']=oldid;u['origin_study_id']=u['study_id']
        u['study_id']='completion_main_20261021_v1'
        u['run_id']=hashlib.sha256(('completion_main_20261021_v1:'+oldid).encode()).hexdigest()[:32]
        u['unit_id']=u['run_id'];u['memory_namespace_sha256']=hashlib.sha256(('completion_main_20261021_v1:'+u['memory_namespace_sha256']).encode()).hexdigest()
        u['replication_status']='separate_new_measurement_not_historical_usage_repair'
    save(NEW/'manifests/formal_plan.json',selected)
    lim=json.loads((OLD/'manifests/budget_limits.json').read_text())
    lim.update(planned_task_units=2166,max_provider_attempts=6498,
        counts={'main':2160,'public_calibration':6},
        observed_reported_total_tokens_stop=6498*(131072+1500),
        missing_usage_stop='Immediately halt later provider calls and task units on unconfirmed usage; retain attempted failure and all unrun cells.')
    save(NEW/'manifests/budget_limits.json',lim)
    save(NEW/'manifests/replication_design.json',{
        'scope':'Independent fixed full120-task main replication, not selective rerun of107 missing cases',
        'original_plan_sha256':digest(OLD/'manifests/formal_plan.json'),
        'tasks_and_order':'Same original main phase order;120 tasks/20chains,6arms,3repeats',
        'main_task_units':2160,'public_accounting_calibration_units':6,
        'provider_seed':'Not sent; local seed20261020 retained, no claim deterministic provider outputs',
        'quality_noninferiority_wording':'WITHHELD_PROSPECTIVELY; no new power claim',
        'missing':'No zero fill, no success-only primary population, stop subsequent calls on unknown usage',
        'analysis':'Same frozen v3 five input-token/five observable-IPC family,10000domain-chain bootstrap,exact20chain signflip; source copied unchanged',
        'state_applicable_task_count':108,'historical_results_pooled':False})
    save(NEW/'manifests/source_copy_bindings.json',{
        'historical_drivers':[{'name':p.name,'sha256':digest(p)} for p in sorted(src.glob('*.py'))],
        'corpus_sha256':digest(NEW/'corpus/tasks.jsonl'),
        'source_business_code':'Immutable existing80471 image formal-checkout; no business-code patch',
        'instrumentation_changes':'New cloned driver only; output isolation + exact HTTP ID capture + fail-closed usage'} )
    print(json.dumps({'prepared_main':2160,'public_calibration':6,'new_dir':str(NEW)},ensure_ascii=False))
if __name__=='__main__':main()
