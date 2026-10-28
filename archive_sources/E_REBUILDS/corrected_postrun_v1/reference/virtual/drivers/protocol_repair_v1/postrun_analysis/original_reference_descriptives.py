"""Prepared original-method descriptions from completed new-study projections.

Never reads formal raw, imports the old Report, draws a new cohort, computes a
paired safety bound, or adds p-values/Holm. Run only in an actual quiet postrun
window. This source has not been executed as part of its preparation.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import inspect
import json
import math
from pathlib import Path
import re


STUDY = 'protocol_repair_20261020_v1'
FREEZE_SHA = '2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
ENTRY_SHA = 'd87bb161630560f4fe7c62e061a167e36ba5d6c93a9c7a5dbd2e4d12c5240251'
METHOD_SOURCE_SHA = 'fed72d9777f6eaf8c630e19eb33f182e3f8acd01423d48dc20a39c9c4c460dc3'
METRIC_AUDITOR_SHA = '7c4cc24bb84274bf96a4c05a00d489491798a84455bd596702db762c4977295f'
METHOD_AST = {
    'quality_interval': '8764cb93f579df98f993e8e341e6c570f2515f6f6cee797c61ef1d4c268458f9',
    'cluster_mean': 'fe72a21fddecc1fb20fc38c62794605e81e2594066eac64141798b7d0816d13f',
}
ARMS = ('T0', 'S0', 'SS0', 'T1', 'S1', 'SS1')
DOMAINS = ('service', 'migration', 'resources', 'network_permissions')
RAW_INPUTS = {'task_runs.jsonl', 'provider_calls.jsonl', 'provider_attempt_starts.jsonl', 'task_unit_starts.jsonl'}
IDENTITY = ('study_id', 'phase', 'task_id', 'chain_id', 'domain', 'difficulty',
            'arm', 'repeat', 'model', 'model_id', 'intervention', 'unit_id', 'seed', 'topology', 'processes')
BOOTSTRAPS = 10000


# These three definitions preserve the original AST, including the default seed.
# np/beta are loaded only after input/lifecycle/source checks have completed.
def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def cluster_mean(rows, metric, seed=20261019):
    """Mean task value; repeats averaged first, domain-stratified chain bootstrap."""
    if not rows or not all(finite(row.get(metric)) for row in rows):
        return None, None, None
    tasks = defaultdict(list)
    for row in rows:
        tasks[(row["domain"], row["chain_id"], row["task_id"])].append(row[metric])
    chains = defaultdict(list)
    for (domain, chain, task), values in tasks.items():
        chains[(domain, chain)].append(float(np.mean(values)))
    grouped = defaultdict(list)
    for (domain, chain), values in chains.items():
        grouped[domain].append((len(values), float(np.mean(values))))
    rng = np.random.default_rng(seed)
    numerator, denominator = np.zeros(BOOTSTRAPS), np.zeros(BOOTSTRAPS)
    for domain, values in sorted(grouped.items()):
        lengths = np.asarray([v[0] for v in values])
        means = np.asarray([v[1] for v in values])
        selected = rng.integers(0, len(values), size=(BOOTSTRAPS, len(values)))
        numerator += (lengths[selected]*means[selected]).sum(axis=1)
        denominator += lengths[selected].sum(axis=1)
    low, high = np.quantile(numerator/denominator, [.025, .975])
    return float(np.mean([np.mean(v) for v in tasks.values()])), float(low), float(high)


def quality_interval(rows):
    """Conservative domain-stratified exact chain-event CI, including all-pass."""
    if not rows:
        return None, None, None
    chains = defaultdict(list)
    for row in rows:
        chains[(row["domain"],row["chain_id"])].append(row)
    domains=defaultdict(list)
    for (domain,chain),values in chains.items():
        domains[domain].append({"tasks":len({r['task_id'] for r in values}),
            "any_failure":any(not r["success"] for r in values),"any_success":any(r["success"] for r in values)})
    total_tasks=sum(c["tasks"] for values in domains.values() for c in values)
    lower=upper=0.
    alpha=.05/(2*len(domains))
    for values in domains.values():
        n=len(values); domain_tasks=sum(c["tasks"] for c in values)
        factor=n*max(c["tasks"] for c in values)/domain_tasks
        failure=sum(c["any_failure"] for c in values)
        success=sum(c["any_success"] for c in values)
        failure_upper=1. if failure==n else float(beta.ppf(1-alpha,failure+1,n-failure))
        success_upper=1. if success==n else float(beta.ppf(1-alpha,success+1,n-success))
        weight=domain_tasks/total_tasks
        lower+=weight*max(0.,1-factor*failure_upper)
        upper+=weight*min(1.,factor*success_upper)
    return float(np.mean([r["success"] for r in rows])), lower, upper


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Inputs:
    def __init__(self, out):
        self.out, self.hashes = out, {}

    def path(self, relative):
        path = Path(relative)
        path = path.resolve() if path.is_absolute() else (self.out / path).resolve()
        if not path.is_relative_to(self.out) or 'raw' in path.relative_to(self.out).parts:
            raise ValueError('only this study frozen metadata and completed safe projections may be read')
        if not path.is_file():
            raise FileNotFoundError(path)
        self.hashes[path.relative_to(self.out).as_posix()] = sha(path)
        return path

    def json(self, relative):
        return json.loads(self.path(relative).read_text(encoding='utf-8'))

    def rows(self, relative):
        path = self.path(relative)
        with path.open(encoding='utf-8') as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def stable(self):
        for relative, wanted in self.hashes.items():
            if sha(self.out / relative) != wanted:
                raise ValueError('input changed during descriptive preparation: ' + relative)


def unique(rows, key):
    result = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, str) or not value or value in result:
            raise ValueError('missing or duplicate ' + key)
        result[value] = row
    return result


def source_methods(path):
    if sha(path) != METHOD_SOURCE_SHA:
        raise ValueError('original pure-method source differs from reviewed historical bytes')
    original = {node.name: node for node in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(node, ast.FunctionDef)}
    current = {node.name: node for node in ast.parse(Path(__file__).read_text(encoding='utf-8')).body
               if isinstance(node, ast.FunctionDef)}
    dependency = {}
    for name in ('quality_interval', 'cluster_mean', 'finite'):
        left = ast.dump(original[name], include_attributes=False)
        right = ast.dump(current[name], include_attributes=False)
        digest = hashlib.sha256(left.encode()).hexdigest()
        if left != right or (name in METHOD_AST and digest != METHOD_AST[name]):
            raise ValueError('copied original pure-method AST changed: ' + name)
        if name == 'finite':
            dependency[name] = digest
    return {'source_path': str(path), 'source_sha256': METHOD_SOURCE_SHA,
            'method_ast_sha256': METHOD_AST, 'dependency_ast_sha256': dependency,
            'cluster_mean_default_seed': 20261019, 'bootstrap_draws': BOOTSTRAPS,
            'source_imported_or_executed': False}


def closed_lifecycle(inputs, quiet_relative):
    record = inputs.json('formal_execution_v1/execution.json')
    stages = record.get('stages') or []
    if (record.get('status') != 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT'
            or not record.get('finished_utc') or record.get('supervised_child_observed_running') is True
            or [stage.get('name') for stage in stages] != ['main', 'native_remaining', 'external']
            or any(stage.get('status') != 'TERMINAL' or stage.get('exit_code') != 0
                   or not stage.get('finished_utc') for stage in stages)):
        raise ValueError('actual formal lifecycle is not fully closed')
    quiet_path = inputs.path(quiet_relative)
    if not quiet_path.is_relative_to(inputs.out / 'analysis') or quiet_path.suffix != '.json':
        raise ValueError('actual quiet-window receipt must be inside this study analysis')
    quiet = inputs.json(quiet_relative)
    if (quiet.get('study_id') != STUDY or quiet.get('status') != 'ACTUAL_NO_REAL_OR_RETRIEVAL_TIMING_RUNNING'
            or quiet.get('active_real_timing_processes') != [] or quiet.get('active_retrieval_timing_processes') != []):
        raise ValueError('actual real/retrieval quiescence has not been documented')
    finish = datetime.fromisoformat(record['finished_utc'].replace('Z', '+00:00'))
    verified = datetime.fromisoformat(quiet['verified_utc'].replace('Z', '+00:00'))
    if finish.tzinfo is None or verified.tzinfo is None or verified < finish:
        raise ValueError('quiet-window observation does not follow actual formal termination')
    return record


def frozen_inputs(inputs, args):
    freeze_path = inputs.path('manifests/formal-freeze.json')
    if sha(freeze_path) != FREEZE_SHA:
        raise ValueError('fullfreeze differs from its established pre-execution authority')
    freeze = inputs.json('manifests/formal-freeze.json')
    plan_path = inputs.path('manifests/formal_plan.json')
    if freeze.get('files', {}).get(str(plan_path)) != sha(plan_path):
        raise ValueError('formal plan is not the exact fullfreeze input')
    if sha(inputs.path('formal_snapshot_v1/drivers/statistical_gates.py')) != ENTRY_SHA:
        raise ValueError('sole primary entry differs from its frozen identity')
    plan = inputs.json('manifests/formal_plan.json')
    planned = unique(plan, 'run_id')
    if len(plan) != 6504 or any(unit.get('study_id') != STUDY for unit in plan):
        raise ValueError('frozen full6504 plan identity is incomplete')
    audit_path = inputs.path(args.metric_audit_dir + '/metric_audit.json')
    audit = inputs.json(args.metric_audit_dir + '/metric_audit.json')
    if (audit.get('study_id') != STUDY or audit.get('status') not in
            ('PASS_OBSERVED_FULL_RECORD_JOINS', 'INCONCLUSIVE_RETAINED')
            or audit.get('terminal_units') != 6504 or audit.get('finding_counts', {}).get('FAIL', 0)
            or audit.get('automatic_full_validation') is not True or audit.get('analyzer_sha256') != METRIC_AUDITOR_SHA):
        raise ValueError('completed reviewed global full audit is missing/FAIL; local0 never bypasses it')
    evidence = audit.get('evidence') or {}
    if evidence.get('manifests/formal-freeze.json', {}).get('sha256') != FREEZE_SHA or \
            evidence.get('manifests/formal_plan.json', {}).get('sha256') != sha(plan_path):
        raise ValueError('full audit used a different freeze or plan')
    projection_relative = args.metric_audit_dir + '/audited_observations.jsonl'
    checks_relative = args.metric_audit_dir + '/unit_checks.jsonl'
    for relative in (projection_relative, checks_relative):
        if sha(inputs.path(relative)) != audit.get('output_sha256', {}).get(Path(relative).name):
            raise ValueError('completed full-audit projection/check bytes changed')
    observations = inputs.rows(projection_relative)
    checks = unique(inputs.rows(checks_relative), 'run_id')
    actual = unique(observations, 'run_id')
    if set(actual) != set(planned) or set(checks) != set(planned):
        raise ValueError('full6504 planned/observed/per-unit audit identities differ')
    raw_lines = set()
    for rid, row in actual.items():
        unit = planned[rid]
        if (row.get('schema_version') != 'audited-observations-projection-v1'
                or any(row.get(key) != unit.get(key) for key in IDENTITY)
                or row.get('source_sha256') != freeze['source_sha256']
                or row.get('model_evidence') != 'real_provider' or not isinstance(row.get('success'), bool)
                or row.get('audit_failures') != 0 or checks[rid].get('audit_failures') != 0
                or row.get('raw_file') != 'task_runs.jsonl' or not integer(row.get('raw_line')) or row['raw_line'] < 1
                or row['raw_line'] in raw_lines or not re.fullmatch('[0-9a-f]{64}', row.get('raw_line_sha256', ''))
                or checks[rid].get('success') is not row['success']
                or checks[rid].get('provider_attempts') != row.get('provider_attempts')):
            raise ValueError('safe projection does not preserve its exact frozen/per-unit/source identity')
        raw_lines.add(row['raw_line'])
    # Restore original task_runs order: cluster insertion order is part of the
    # exact historical seeded method, although the audit emits UUID-sorted rows.
    observations.sort(key=lambda row: row['raw_line'])
    primary_path = inputs.path(args.primary)
    primary = inputs.json(args.primary)
    raw_hashes = primary.get('raw_input_sha256') or {}
    if (primary.get('study_id') != STUDY or primary.get('formal_freeze_sha256') != FREEZE_SHA
            or primary.get('collection_status') != 'COMPLETE' or set(raw_hashes) != RAW_INPUTS
            or any(evidence.get('raw/' + name, {}).get('sha256') != wanted for name, wanted in raw_hashes.items())):
        raise ValueError('sole saved primary and full audit used different or incomplete identities')
    secondary_path = inputs.path(args.secondary_dir + '/receipt.json')
    secondary = inputs.json(args.secondary_dir + '/receipt.json')
    if (secondary.get('study_id') != STUDY or secondary.get('status') != 'COMPLETE_DESCRIPTIVE_ANALYSIS'
            or secondary.get('original_statistical_entry_sha256') != ENTRY_SHA
            or secondary.get('original_formal_freeze_sha256') != FREEZE_SHA
            or secondary.get('raw_input_sha256') != raw_hashes):
        raise ValueError('saved secondary receipt differs from the same completed new cohort')
    headers = {'study_id': STUDY, 'formal_freeze_sha256': FREEZE_SHA, 'raw_input_sha256': raw_hashes,
        'source_primary_sha256': sha(primary_path), 'source_secondary_receipt_sha256': sha(secondary_path),
        'source_metric_audit_sha256': sha(audit_path), 'source_projection_sha256': sha(inputs.path(projection_relative)),
        'source_unit_checks_sha256': sha(inputs.path(checks_relative)), 'formal_plan_sha256': sha(plan_path)}
    return plan, observations, checks, audit, headers


def population(plan, observations, phase, arm=None, keys=None, domain=None):
    selected = [unit for unit in plan if unit['phase'] == phase and (arm is None or unit['arm'] == arm)
                and (keys is None or (unit['task_id'], unit['repeat']) in keys)
                and (domain is None or unit['domain'] == domain)]
    ids = {unit['run_id'] for unit in selected}
    rows = [row for row in observations if row['run_id'] in ids]
    if len(rows) != len(selected) or {row['run_id'] for row in rows} != ids:
        raise ValueError('planned population is not fully observed; never select only successes')
    return rows


def counts(rows):
    return {'planned_units': len(rows), 'terminal_units': len(rows),
            'unique_tasks': len({row['task_id'] for row in rows}),
            'chain_count': len({(row['domain'], row['chain_id']) for row in rows}),
            'failed_terminal_units': sum(row['success'] is False for row in rows)}


def absolute_rows(plan, observations):
    result = []
    for dimension, level in [('overall', 'all')] + [('domain', domain) for domain in DOMAINS]:
        for arm in ARMS:
            rows = population(plan, observations, 'main', arm, domain=level if dimension == 'domain' else None)
            expected = (360, 120, 20) if dimension == 'overall' else (90, 30, 5)
            size = counts(rows)
            if (size['planned_units'], size['unique_tasks'], size['chain_count']) != expected:
                raise ValueError('main overall/domain original quality population changed')
            value, low, high = quality_interval(rows)
            if not all(number(item) for item in (value, low, high)) or not 0 <= low < high <= 1:
                raise ValueError('original absolute-quality method produced an invalid interval')
            result.append({'phase': 'main', 'dimension': dimension, 'level': level, 'arm': arm,
                'metric': 'quality_success', 'value': value, 'ci95_low': low, 'ci95_high': high, **size,
                'status': 'COMPLETE_ORIGINAL_CONSERVATIVE_ABSOLUTE_QUALITY_INTERVAL',
                'method': 'original quality_interval; conservative domain-stratified exact chain-event absolute-rate CI',
                'paired_safety_bound': False, 'quality_noninferiority': 'WITHHELD_PROSPECTIVELY'})
    return result


def matched_rows(plan, observations, checks):
    cohorts = []
    for phase in ('external_autogen', 'external_langgraph'):
        units = [unit for unit in plan if unit['phase'] == phase]
        keys = {(unit['task_id'], unit['repeat']) for unit in units}
        if len(units) != len(keys) or any(unit['arm'] != 'T0' or unit['model'] != 'main'
                or unit['processes'] is not False or unit.get('intervention') is not None for unit in units):
            raise ValueError('external frozen plan is not its fixed main-model T0 reference')
        cohorts.append(keys)
    tasks = {task for task, repeat in cohorts[0]}
    if len(tasks) != 60 or cohorts[0] != cohorts[1] or cohorts[0] != {(task, repeat) for task in tasks for repeat in (0, 1)}:
        raise ValueError('external cohorts are not the exact same60 preselected tasks/repeats0,1')
    result = []
    selected_units = []
    for system, phase in (('native_T0', 'main'), ('autogen', 'external_autogen'), ('langgraph', 'external_langgraph')):
        rows = population(plan, observations, phase, 'T0', keys=cohorts[0])
        size = counts(rows)
        if (size['planned_units'], size['unique_tasks'], size['chain_count']) != (120, 60, 20):
            raise ValueError('matched reference must retain120 units/60tasks/20chains per system')
        no_attempt = sum(row.get('provider_attempts') == 0 for row in rows)
        calibrated = sum(checks[row['run_id']].get('real_provider_usage_complete') is True for row in rows)
        for metric in ('quality_success', 'provider_input_tokens', 'wall_sec'):
            value, low, high = quality_interval(rows) if metric == 'quality_success' else cluster_mean(rows, metric)
            known = all(number(item) for item in (value, low, high))
            status = 'COMPLETE_ORIGINAL_METHOD_DESCRIPTIVE' if known else 'UNKNOWN_METRIC_RETAINED_ALL_PLANNED_UNITS'
            if metric == 'provider_input_tokens' and known and calibrated != len(rows):
                status = 'OBSERVED_TASK_COST_WITH_UNCALIBRATED_ZERO_CALL_UNITS_NOT_PROVIDER_USAGE_CERTIFICATION'
            result.append({'system': system, 'metric': metric, 'value': value, 'ci95_low': low, 'ci95_high': high,
                **size, 'observed_units': len(rows), 'chains': size['chain_count'], 'status': status,
                'method': 'original quality_interval' if metric == 'quality_success' else 'original cluster_mean(default seed20261019,10000draws)',
                'provider_attempt_zero_units': no_attempt, 'independently_usage_calibrated_units': calibrated,
                'comparison_scope': 'SYSTEM_LEVEL_REFERENCE',
                'comparability_status': 'NOT_ASSESSED_BY_DESCRIPTIVE_PRODUCER',
                'success_only_filter': False, 'null_metric_policy': 'original cluster_mean returns all-null if any planned terminal value is absent/null/nonfinite; no imputation'})
        selected_units.extend({'system': system, 'run_id': row['run_id'], 'phase': row['phase'],
            'arm': row['arm'], 'task_id': row['task_id'], 'repeat': row['repeat'],
            'raw_line': row['raw_line'], 'raw_line_sha256': row['raw_line_sha256'],
            'audit_unobserved_items': checks[row['run_id']].get('unobserved_items'),
            'real_provider_usage_complete': checks[row['run_id']].get('real_provider_usage_complete'),
            'provider_attempts': row.get('provider_attempts')} for row in rows)
    return result, sorted(tasks), selected_units


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-out', type=Path, required=True)
    parser.add_argument('--quiescence-receipt', required=True)
    parser.add_argument('--metric-audit-dir', default='analysis/full_metric_audit_v3')
    parser.add_argument('--primary', default='analysis/stats/formal_primary_v1.json')
    parser.add_argument('--secondary-dir', default='analysis/postrun/secondary_heterogeneity_v1')
    parser.add_argument('--method-source', type=Path, default=Path(__file__).resolve().parents[2] / 'formal_report.py')
    parser.add_argument('--version', default='original_reference_descriptives_v1')
    args = parser.parse_args()
    out = args.study_out.resolve()
    if out.name != STUDY or re.fullmatch('original_reference_descriptives_v[1-9][0-9]*', args.version) is None:
        parser.error('exact new-study root and a fresh original_reference_descriptives_vN version required')
    destination = out / 'analysis/postrun' / args.version
    if destination.exists():
        parser.error('retain the existing successful/failed version and choose a fresh version')
    inputs = Inputs(out)
    closed_lifecycle(inputs, args.quiescence_receipt)
    import fcntl
    with ExitStack() as stack:
        for name in ('formal_sequence_execution.lock', 'real_provider_execution.lock'):
            handle = stack.enter_context((out / 'manifests' / name).open('r'))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        methods = source_methods(args.method_source.resolve())
        plan, observations, checks, audit, headers = frozen_inputs(inputs, args)
        destination.mkdir(parents=True)
        try:
            global np, beta
            import numpy as np
            from scipy.stats import beta
            dependencies = {'numpy_version': importlib.metadata.version('numpy'),
                'scipy_version': importlib.metadata.version('scipy'),
                'numpy_module_source_sha256': sha(Path(np.__file__)),
                'scipy_beta_definition_source_sha256': sha(Path(inspect.getsourcefile(type(beta)))),
                'boundary': 'loaded module/definition files and package versions; not a complete wheel hash lock'}
            absolute = absolute_rows(plan, observations)
            reference, task_ids, matched_units = matched_rows(plan, observations, checks)
            summary = {**headers, 'status': 'SAVED_ORIGINAL_METHOD_DESCRIPTIVES_AWAIT_SEPARATE_EQUIVALENCE',
                'task_ids': task_ids, 'task_ids_sha256': hashlib.sha256(canonical(task_ids).encode()).hexdigest(),
                'repeat_ids': [0, 1], 'native_reference_phase': 'main', 'native_reference_arm': 'T0',
                'comparison_scope': 'SYSTEM_LEVEL_REFERENCE', 'rows': reference,
                'semantic_equivalence_audit': 'SEPARATE_REQUIRED_AUTHORITY_NOT_EXECUTED_OR_CERTIFIED_HERE'}
            write_json(destination / 'absolute_quality.json', {**headers, 'rows': absolute})
            write_json(destination / 'matched_external_summary.json', summary)
            write_json(destination / 'matched_observation_index.json', {'rows': matched_units,
                'scope': 'metadata-only original projection IDs and per-unit UNKNOWN/usage coverage; no raw bodies'})
            inputs.stable()
            if sha(args.method_source.resolve()) != METHOD_SOURCE_SHA:
                raise ValueError('original pure-method source changed during execution')
            files = {path.name: sha(path) for path in sorted(destination.iterdir()) if path.is_file()}
            receipt = {**headers, 'status': 'COMPLETE_SAVED_ORIGINAL_METHOD_DESCRIPTIVES',
                'created_utc': datetime.now(timezone.utc).isoformat(), 'helper_sha256': sha(Path(__file__)),
                'method_ast_sha256': METHOD_AST, 'original_method': methods, 'numeric_dependencies': dependencies,
                'files': files, 'input_sha256': inputs.hashes,
                'full_audit_status': audit['status'], 'full_audit_finding_counts': audit.get('finding_counts'),
                'full_audit_claim_scope': audit.get('claim_scope'), 'full_audit_limits': audit.get('limits'),
                'absolute_quality_rows': 30, 'matched_reference_rows': 9,
                'source_row_order': 'original task_runs raw_line order restored from hash-bound safe projection',
                'new_primary_estimates': False, 'new_p_values': False, 'new_holm_families': 0,
                'old_Report_imported': False, 'old_real_or_retrieval_read': False, 'formal_raw_read': False,
                'scope': 'original absolute-rate intervals and original system-reference descriptions only; no primary replacement or equivalence PASS',
                'limits': ['All planned failures remain in each population; genuine unknown metric values are not filled or success-filtered.',
                    'No observed provider attempts do not calibrate reported usage. Original recorded task-cost0 may remain0 without becoming billed0.',
                    'Overall full-audit UNKNOWN is retained with its original claim boundaries, not promoted to all-indicator PASS.',
                    'External figure9 requires the separate actual equivalence audit and its endpoint-specific matched evidence before rendering.',
                    'Quality intervals are conservative absolute-rate bounds, not paired safety bounds or a quality-noninferiority claim.']}
            write_json(destination / 'receipt.json', receipt)
        except BaseException as exc:
            write_json(destination / 'failure.json', {'status': 'FAILED_RETAINED_NO_COMPLETE_RECEIPT',
                'error_type': type(exc).__name__, 'created_utc': datetime.now(timezone.utc).isoformat(),
                'helper_sha256': sha(Path(__file__)), 'completed_receipt_written': False,
                'error_detail': 'source/projected content deliberately omitted; retain this version and inspect the actual exception locally'})
            raise
    print(json.dumps({'status': 'COMPLETE_SAVED_ORIGINAL_METHOD_DESCRIPTIVES',
                      'directory': str(destination), 'absolute_quality_rows': 30, 'matched_reference_rows': 9}))


if __name__ == '__main__':
    main()
