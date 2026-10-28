"""Register original-protocol population correction using frozen metadata only."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

OUT = Path('/evidence/formal_20261019')


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    freeze_path = OUT / 'manifests/formal-freeze.json'
    freeze = json.loads(freeze_path.read_text())
    corpus_path = OUT / 'corpus/automatic_validated_v1/tasks.jsonl'
    plan_path = OUT / 'manifests/formal_plan.json'
    old_path = Path('/drivers/formal_statistics.py')
    for path in (corpus_path, plan_path, old_path):
        if hashlib.sha256(path.read_bytes()).hexdigest() != freeze['files'][str(path)]:
            raise RuntimeError('frozen input identity mismatch: ' + path.name)
    corpus = [json.loads(line) for line in corpus_path.read_text().splitlines()]
    formal = sorted(r['task_id'] for r in corpus if r['split'] == 'formal')
    applicable = sorted(r['task_id'] for r in corpus if r['split'] == 'formal' and r['state_spec']['applicable'])
    plan = [u for u in json.loads(plan_path.read_text()) if u['phase'] == 'main']
    assert len(formal) == 120 and len(applicable) == 108 and len(plan) == 2160
    populations = [{'baseline':a,'target':b,'unique_tasks':n,'chains':20,'repeats':3,
                    'planned_paired_units_per_arm':n*3,
                    'population':'predeclared_state_applicable' if n==108 else 'all_formal_tasks',
                    'endpoints':['provider_input_tokens','observable_ipc_bytes']}
                   for a,b,n in [('T0','S0',120),('S0','SS0',108),('T0','T1',120),('S0','S1',120),('SS0','SS1',120)]]
    value = {'schema_version':3,'created_utc':datetime.now(timezone.utc).isoformat(),
        'status':'ORIGINAL_PROTOCOL_IMPLEMENTATION_OMISSION_CORRECTION_REGISTERED',
        'original_protocol_authority':'original_protocol.md sections 4.1 and 6.2',
        'reason':'original section 4.1 excludes NOT_APPLICABLE from state increment; frozen all120 analyze call omitted this estimand-specific restriction',
        'research_protocol_change':False,'analysis_implementation_population_correction':True,
        'timing':'during collection after original freeze, before inspecting formal aggregate effects',
        'formal_raw_files_read':[],'formal_effects_inspected':False,
        'frozen_files_modified':False,'source_protocol_sha256':'ca5602a6f8fcb852f5824bf6c4fd7e71074b280c35ba5705676806d17d593843',
        'frozen_statistics_sha256':freeze['files'][str(old_path)],
        'formal_task_ids_sha256':sha(formal),'applicable_task_ids_sha256':sha(applicable),
        'formal_task_ids':formal,'applicable_task_ids':applicable,
        'active_confirmatory_family_size':10,'confirmatory_populations':populations,
        'multiplicity_rule':'one joint Holm correction: eight p_raw from other four all120 contrasts plus two S0_SS0 p_raw on frozen108; no second active family',
        'archive_policy':'preserve all120 frozen-method replay including all numerical p_holm; mark archive_only and do not choose or report its significance as a second confirmatory route',
        'state_all120_policy':'descriptive overall deployment-strategy difference, not pure state increment; no confirmatory p_holm',
        'other_108_subgroup_policy':'descriptive p_raw only; no p_holm or additional confirmatory family',
        'state_failure_policy':'pre-treatment applicability labels only; retain all108 including failed paths, unknown usage, rejection and timeout; no filter by observed consumption or success',
        'frozen_methods_retained':'same frozen bootstrap/permutation/quality methods and identical random seed offsets; only the S0_SS0 population and single combined Holm inputs differ',
        'noninferiority_wording':'WITHHELD_PROSPECTIVELY',
        'implementation_status':'new independent formal_statistics_protocol_aligned_v3.py pending; its SHA will be separately registered'}
    target = OUT / 'analysis/stats/statistical_plan_protocol_alignment_v3.json'
    with target.open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'status':value['status'],'formal_task_ids_sha256':value['formal_task_ids_sha256'],
                      'applicable_task_ids_sha256':value['applicable_task_ids_sha256'],
                      'frozen_statistics_sha256':value['frozen_statistics_sha256']}))


if __name__ == '__main__':
    main()
