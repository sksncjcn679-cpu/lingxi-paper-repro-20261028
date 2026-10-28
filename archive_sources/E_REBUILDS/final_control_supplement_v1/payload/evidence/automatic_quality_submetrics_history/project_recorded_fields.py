"""Project existing explicit audit booleans; never parse responses or score again."""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
import ast
import csv
import hashlib
import json
import re

WORK = Path(__file__).resolve().parents[4]
NEW = WORK / 'paper_experiments_20261019_pilot/protocol_repair_20261020_v1'
OUT = Path(__file__).resolve().parent
REF = Path('E:/codex_rebuilds/corrected_postrun_v2/reference/virtual/evidence/protocol_repair_20261020_v1')
CHECKS = REF / 'analysis/full_metric_audit_v3/unit_checks.jsonl'
OBS = REF / 'analysis/full_metric_audit_v3/audited_observations.jsonl'
AUDITOR = WORK / 'deployment/protocol_repair_v1/postrun_analysis/full_metric_audit.py'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def stream(p):
    with p.open('rb') as fh:
        for line in fh:
            yield json.loads(line)


def main():
    if (OUT / 'receipt.json').exists():
        raise RuntimeError('fresh destination required')
    assert sha(CHECKS) == '881b50585f285b317416cfa8afcd224b47e6e64b6e1c362a679ba6eeb289da56'
    assert sha(OBS) == '1c2031eec1ad922c753199fc4cc0a7760fdeca0f110becc6d2e83db0dc12281a'
    assert sha(AUDITOR) == '7c4cc24bb84274bf96a4c05a00d489491798a84455bd596702db762c4977295f'
    source = AUDITOR.read_text(encoding='utf-8')
    node = next(x for x in ast.parse(source).body if isinstance(x, ast.FunctionDef) and x.name == 'audit_fixture_truth')
    fields = ['selected_action_present', 'selected_action_matches_current_measurements', 'recorded_readonly_check_present']
    checks = {}
    for row in stream(CHECKS):
        assert row['run_id'] not in checks
        checks[row['run_id']] = row
    assert len(checks) == 6504
    groups = defaultdict(list)
    inventory = defaultdict(Counter)
    def inspect(value, path=''):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == '_tool_signatures':
                    continue  # dynamic command-key counters, not response-validity fields
                inspect(child, path + '/' + key)
        elif isinstance(value, list):
            return  # no response bodies are sought or inspected
        elif re.search(r'json|pars|valid|schema|complet|required|answer|diagnos|quality|judgement|selected|action', path, re.I):
            inventory[path][type(value).__name__] += 1
    observed_ids = set()
    for row in stream(OBS):
        rid = row['run_id']
        assert rid not in observed_ids and rid in checks
        observed_ids.add(rid)
        c = checks[rid]
        for name in ('phase', 'task_id', 'arm', 'model'):
            assert c[name] == row[name]
        group = (row['phase'], row['arm'], row['model'], row['model_id'], row['topology'])
        groups[group].append(c)
        inspect(row)
        inspect(c, '/unit_checks')
    assert observed_ids == set(checks)
    outputs = []
    for group, rows in sorted(groups.items()):
        base = dict(zip(['phase', 'arm', 'model', 'model_id', 'topology'], group))
        for field in fields:
            yes = sum(row.get(field) is True for row in rows)
            no = sum(row.get(field) is False for row in rows)
            unknown = len(rows) - yes - no
            outputs.append({**base, 'field': field, 'planned_terminal_units': len(rows),
                'recorded_true': yes, 'recorded_false': no, 'unknown_or_nonboolean': unknown,
                'recorded_true_over_planned': yes / len(rows),
                'status': 'RECORDED_BOOLEAN_PROJECTION_ONLY',
                'meaning': 'the existing canonical-fixture audit flag; no new score, JSON inference or success-only selection'})
        for missing in ('model_output_json_valid', 'model_output_required_fields_complete'):
            outputs.append({**base, 'field': missing, 'planned_terminal_units': len(rows),
                'recorded_true': '', 'recorded_false': '', 'unknown_or_nonboolean': len(rows),
                'recorded_true_over_planned': '', 'status': 'NOT_RECORDED_IN_INSPECTED_METADATA',
                'meaning': 'no explicit per-output validity/completeness boolean is retained in these projections; not inferred from success, dict outputs, parser counters or response hashes'})
    with (OUT / 'recorded_quality_fields.csv').open('w', encoding='utf-8-sig', newline='') as fh:
        wr = csv.DictWriter(fh, fieldnames=list(outputs[0]))
        wr.writeheader(); wr.writerows(outputs)
    totals = {}
    for cohort, selected in [('all6504', list(checks.values())), ('main2160', [x for x in checks.values() if x['phase'] == 'main'])]:
        totals[cohort] = {'denominator': len(selected)}
        for field in fields:
            yes = sum(x.get(field) is True for x in selected)
            no = sum(x.get(field) is False for x in selected)
            totals[cohort][field] = {'true': yes, 'false': no, 'unknown_or_nonboolean': len(selected)-yes-no}
        totals[cohort]['model_output_json_valid'] = {'known': 0, 'unknown': len(selected)}
        totals[cohort]['model_output_required_fields_complete'] = {'known': 0, 'unknown': len(selected)}
    save('metadata_field_inventory.json', {'scope': 'only scalar path/type presence; no string or response values emitted',
        'paths': {k: dict(v) for k, v in sorted(inventory.items())},
        'explicit_output_json_validity_or_required_fields_flags_found': False,
        'not_usable_as_json_validity': ['provider_usage_complete', 'metrics.llm_coalesce_parse_misses',
            'metrics.llm_coalesce_repairs', 'metrics.llm_judgements_correct', 'source_semantics', 'success', 'quality'],
        'reason': 'usage validity, coalescing counters, canonical success and response hashes do not encode original per-output JSON parse validity or required-field completeness'})
    save('method_scope.json', {'original_requirement': 'original plan5.1 action correctness, answer completeness, invalid JSON rate; user-amended automatic review only',
        'action_field': 'unit_checks.selected_action_matches_current_measurements',
        'existing_definition': 'audit_fixture_truth uses the frozen pure oracle check(case, selected_action).current_record_matches_check; absent/unsupported selected action leaves existing flag False',
        'selection_absence_separately_visible': 'selected_action_present and recorded_readonly_check_present are also projected',
        'projection_population': 'all6504 terminal records; public/formal/phase/arm/model/topology stay separate in CSV; main2160 separately summarized',
        'json_scope': 'No original response string is read, decoded or rescored. No success/dict/hash implication is used. The two inspected metadata products do not retain explicit original-output JSON-validity or complete-required-field booleans.',
        'M002_can_fully_close': False,
        'recoverable_now': ['recorded canonical selected-action correctness counts', 'action presence counts', 'readonly-check presence counts'],
        'not_recovered': ['original model-output invalid JSON rate', 'original model-output required-field completeness rate', 'open explanation completeness'],
        'limits': ['The action flag is canonical fixture semantics, not live Linux correctness.',
            'The 6504 mixed total is audit coverage, not a pooled scientific accuracy result.',
            'JSON flags absent from the inspected projections cannot be recreated from hashes. This does not assert every possible raw artefact was searched.',
            'Frozen native model-I/O logging is hash-only; the original response syntax is not invented.',
            'No new grading rule, hypothesis test, CI, p, Holm, provider/model/runtime invocation or new experiment.']})
    save('receipt.json', {'status': 'PARTIAL_M002_EXISTING_ACTION_FLAGS_PROJECTED_JSON_FLAGS_NOT_RECORDED',
        'actual_terminal': True, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'source_inputs': {str(p): {'sha256': sha(p), 'bytes': p.stat().st_size} for p in (CHECKS, OBS, AUDITOR)},
        'source_predicate_ast_sha256': hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest(),
        'rows': len(outputs), 'groups': len(groups), 'units': 6504, 'main_units': 2160,
        'totals': totals, 'M002_can_fully_close': False,
        'no_runtime_model_API_or_raw_response_read': True, 'no_new_quality_score_or_CI_p_Holm': True,
        'outputs': {n: {'sha256': sha(OUT/n), 'bytes': (OUT/n).stat().st_size} for n in
            ('recorded_quality_fields.csv', 'metadata_field_inventory.json', 'method_scope.json', 'project_recorded_fields.py')}})
    print(json.dumps({'status': 'PARTIAL_M002', 'totals': totals, 'receipt_sha256': sha(OUT/'receipt.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
