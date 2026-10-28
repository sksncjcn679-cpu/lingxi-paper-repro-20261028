"""Independently check completed overhead pairs and publish the freeze reference."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math

import numpy as np
from scipy.stats import t as student_t

OUT = Path('/evidence/formal_20261019')
BASE = OUT / 'calibration/measurement_overhead_v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    raw = BASE / 'raw/overhead_units.jsonl'
    rows = [json.loads(line) for line in raw.read_text().splitlines()]
    report = json.loads((BASE / 'overhead_analysis.json').read_text())
    plan = json.loads((BASE / 'plan.json').read_text())
    errors = []
    pairs = defaultdict(dict)
    for row in rows:
        key = (row['task_id'], row['arm'], row['repeat'])
        if row['condition'] in pairs[key]:
            errors.append({'code': 'DUPLICATE_PAIR_CONDITION', 'key': key})
        pairs[key][row['condition']] = row
        if not row['success'] or row.get('error'):
            errors.append({'code': 'FIXTURE_EXECUTION_NOT_SUCCESS', 'key': key})
        if not row.get('native_journal_enabled'):
            errors.append({'code': 'JOURNAL_NOT_ENABLED', 'key': key})
        if row.get('real_provider_calls') != 0 or any(row.get(k) is not None for k in
                ('provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens')):
            errors.append({'code': 'MOCK_OVERHEAD_CLAIMS_REAL_PROVIDER', 'key': key})
        for metric in ('outer_wall_sec', 'outer_runtime_cpu_sec', 'observable_live_process_group_cpu_sec'):
            if not isinstance(row.get(metric), (int, float)) or not math.isfinite(row[metric]) or row[metric] < 0:
                errors.append({'code': 'INVALID_OVERHEAD_MEASUREMENT', 'key': key, 'metric': metric})
    if len(rows) != 288 or len(pairs) != 144 or report['observed_rows'] != 288:
        errors.append({'code': 'PLANNED_MATRIX_INCOMPLETE'})
    if report.get('quality_mismatches') or not report.get('all_fixture_tasks_success'):
        errors.append({'code': 'OVERHEAD_ANALYSIS_SCORING_MISMATCH'})
    if report.get('raw_sha256') != digest(raw):
        errors.append({'code': 'RAW_HASH_MISMATCH'})
    for key, conditions in pairs.items():
        if set(conditions) != {'direct_native', 'full_recorder'}:
            errors.append({'code': 'UNPAIRED_OVERHEAD_UNIT', 'key': key})
            continue
        a, b = conditions['direct_native'], conditions['full_recorder']
        for field in ('outputs', 'success', 'native_tool_calls', 'native_journal_enabled', 'state_consumed', 'source_sha256'):
            if a.get(field) != b.get(field):
                errors.append({'code': 'CONDITIONS_DIFFER_IN_EXECUTION_CONTRACT', 'key': key, 'field': field})
    first_condition = Counter()
    first_by_arm = defaultdict(Counter)
    for i in range(0, len(rows), 2):
        if i + 1 < len(rows):
            a, b = rows[i], rows[i + 1]
            if (a['task_id'], a['arm'], a['repeat']) != (b['task_id'], b['arm'], b['repeat']):
                errors.append({'code': 'MATCHED_CONDITIONS_NOT_ADJACENT', 'row': i})
            first_condition[a['condition']] += 1
            first_by_arm[a['arm']][a['condition']] += 1
    if dict(first_condition) != {'direct_native': 72, 'full_recorder': 72}:
        errors.append({'code': 'CONDITION_ORDER_NOT_BALANCED', 'counts': dict(first_condition)})
    for arm, counts in first_by_arm.items():
        if set(counts.values()) != {12} or len(counts) != 2:
            errors.append({'code': 'WITHIN_ARM_CONDITION_ORDER_NOT_BALANCED', 'arm': arm})
    summaries = []
    for summary in report['summary_by_arm']:
        arm = summary['arm']
        selected = [conditions for (task, group, repeat), conditions in pairs.items() if group == arm]
        if len(selected) != 24:
            errors.append({'code': 'ARM_PAIR_COUNT', 'arm': arm})
        independent = {'arm': arm, 'paired_units': len(selected), 'unique_public_tasks': 8,
                       'public_chains': 4, 'matched_complete_passes': 3, 'metrics': {}}
        for metric, given in summary['metrics'].items():
            baseline, full, differences = [], [], []
            repeat_differences = defaultdict(list)
            for conditions in selected:
                a, b = conditions['direct_native'], conditions['full_recorder']
                baseline.append(a[metric])
                full.append(b[metric])
                differences.append(b[metric] - a[metric])
                repeat_differences[a['repeat']].append(b[metric] - a[metric])
            mean = float(np.mean(differences))
            repeat_means = [float(np.mean(v)) for _, v in sorted(repeat_differences.items())]
            half = float(student_t.ppf(.975, 2) * np.std(repeat_means, ddof=1) / np.sqrt(3))
            ci = [float(np.mean(repeat_means) - half), float(np.mean(repeat_means) + half)]
            if abs(given['paired_overhead_mean'] - mean) > 1e-10 or any(abs(a - b) > 1e-10 for a, b in zip(ci, given['conditional_repeat_mean_ci95'])):
                errors.append({'code': 'OVERHEAD_SUMMARY_NOT_REPRODUCIBLE', 'arm': arm, 'metric': metric})
            independent['metrics'][metric] = {'baseline_mean': float(np.mean(baseline)),
                'full_recorder_mean': float(np.mean(full)), 'paired_overhead_mean': mean,
                'relative_overhead': float(np.mean(full) / np.mean(baseline) - 1) if np.mean(baseline) else None,
                'fixed_workload_repeat_mean_ci95': ci,
                'domain_stratified_chain_ci95': given['domain_stratified_chain_ci95']}
        summaries.append(independent)
    result = {'status': 'PASS' if not errors else 'FAIL', 'created_utc': datetime.now(timezone.utc).isoformat(),
        'measurement_units': len(rows), 'planned_measurement_units': 288, 'paired_units': len(pairs),
        'quality_mismatches': len(report.get('quality_mismatches', [])),
        'all_fixture_tasks_success': report.get('all_fixture_tasks_success'),
        'real_provider_calls': 0, 'model_tokens': None,
        'scope': 'complete recorder versus direct native path on the same eight fixed public deterministic fixtures, six groups, three matched passes',
        'measurement_errors': errors, 'condition_first_counts': dict(first_condition),
        'conditions_identical_for_checked_business_contract': not any(e['code'] == 'CONDITIONS_DIFFER_IN_EXECUTION_CONTRACT' for e in errors),
        'sources': {str(p.relative_to(OUT)): digest(p) for p in [raw, BASE / 'overhead_analysis.json', BASE / 'plan.json']},
        'summaries_by_arm': summaries,
        'not_subtracted_from_formal_wall': True,
        'limits': ['deterministic fixture timing quantifies measurement overhead; it is not real model/provider latency or token evidence',
            'fixed workload has one public chain per domain; domain-stratified chain bootstrap is degenerate and explicitly flagged',
            'reported nondegenerate interval is paired t across three complete matched passes (df2), conditional on fixed eight-task workload; no claim of task-population precision',
            'T0 interval spans zero; retain cold-start uncertainty instead of trimming it',
            'runtime CPU includes recorder/sampler threads; live process-group delta excludes unobserved CPU of children that exit',
            'all measured overhead, including positive 35-41 ms for most groups, is retained without correction of subsequent formal wall time'],
        'auditor_source_sha256': digest(__file__)}
    target = OUT / 'analysis/overhead_calibration.json'
    with target.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status': result['status'], 'measurement_units': len(rows), 'errors': len(errors),
                      'quality_mismatches': result['quality_mismatches'], 'output': str(target)}))
    raise SystemExit(bool(errors))


if __name__ == '__main__':
    main()
