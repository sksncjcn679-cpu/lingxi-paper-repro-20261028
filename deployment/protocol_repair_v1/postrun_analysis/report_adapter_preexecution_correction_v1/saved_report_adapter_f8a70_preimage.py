"""Report adaptation from SAVED new-study authorities; never rerun statistics.

Prepared source only until postrun schema/numeric/visual review. No runtime,
provider, old Report, statistics kernel, bootstrap or Holm imports are allowed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

STUDY = 'protocol_repair_20261020_v1'
ENTRY_SHA = 'd87bb161630560f4fe7c62e061a167e36ba5d6c93a9c7a5dbd2e4d12c5240251'
FREEZE_SHA = '2c654ff7b961a8e7d882634d820910d7e77b8729d20a01d875b00ee55c17e42e'
BOUNDARIES_SHA = 'f1e190ab50be5cc1b616b692bebb3f3e5b9a43b851f5554aab158e0da15a5cd4'
PAIRS = (('T0', 'S0'), ('S0', 'SS0'), ('T0', 'T1'), ('S0', 'S1'), ('SS0', 'SS1'))
PRIMARY = ('provider_input_tokens', 'observable_ipc_bytes')
ARMS = ('T0', 'S0', 'SS0', 'T1', 'S1', 'SS1')
OFFLINE_TOPICS = {'protocol', 'state', 'quantization', 'reuse', 'stress', 'continuous', 'fault', 'state_stage_supplement'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def write_json(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row)) or ['status']
    with path.open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def write_jsonl(path, rows):
    with path.open('x', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')


def nested(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else default


class Authorities:
    def __init__(self, out):
        self.out, self.sources = out, {}

    def path(self, value):
        p = Path(value)
        p = p.resolve() if p.is_absolute() else (self.out/p).resolve()
        if not p.is_relative_to(self.out):
            raise ValueError('new-study authority must be inside the exact new root')
        if '/raw/' in p.as_posix() or '/formal_20261019/' in p.as_posix():
            raise ValueError('report adapter never reads real raw/historical outcome streams')
        if not p.is_file():
            raise FileNotFoundError(p)
        self.sources[p.relative_to(self.out).as_posix()] = digest(p)
        return p

    def json(self, value):
        return json.loads(self.path(value).read_text(encoding='utf-8'))

    def lines(self, value):
        path = self.path(value)
        with path.open(encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                if line.strip():
                    yield line_no, json.loads(line)

    def csv(self, value):
        with self.path(value).open(encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))

    def copy(self, value, target):
        source = self.path(value)
        if target.exists():
            raise RuntimeError('copy destination exists')
        shutil.copyfile(source, target)
        if digest(target) != digest(source):
            raise RuntimeError('authority byte copy changed')

    def stable(self):
        for name, wanted in self.sources.items():
            if digest(self.out/name) != wanted:
                raise ValueError('saved authority changed during report: '+name)


def primary_rows(saved):
    """Only top-level sole-family results; archive/descriptive Holm never traversed."""
    if saved.get('active_confirmatory_families') != 1 or saved.get('primary_family_test_count') != 10:
        raise ValueError('saved main result must have exactly its original sole ten-test family')
    if saved.get('analysis_status') != 'sole_primary_family':
        raise ValueError('archive or exploratory result is not the primary authority')
    result = []
    for si, stratum in enumerate(saved.get('strata', [])):
        comparisons = stratum.get('comparisons', [])
        if [(r.get('baseline'), r.get('target')) for r in comparisons] != list(PAIRS):
            raise ValueError('primary comparison order/population changed')
        for ci, comparison in enumerate(comparisons):
            expected_n = 108 if (comparison['baseline'], comparison['target']) == ('S0', 'SS0') else 120
            if comparison.get('planned_unique_tasks') != expected_n or comparison.get('planned_chains') != 20:
                raise ValueError('saved primary population changed')
            for metric in PRIMARY:
                value = comparison['metrics'][metric]
                interval = value.get('difference_ci95')
                if value.get('holm_family_size') != 10 or value.get('significance_scope') != 'sole_protocol_aligned_primary_family':
                    raise ValueError('saved endpoint is outside sole family')
                result.append({'baseline': comparison['baseline'], 'target': comparison['target'], 'metric': metric,
                    'baseline_mean': value.get('baseline_mean'), 'target_mean': value.get('target_mean'),
                    'paired_difference': value.get('paired_difference'), 'relative_change': value.get('relative_change'),
                    'relative_change_ci95': value.get('relative_change_ci95'),
                    'ci95_low': interval[0] if interval else None, 'ci95_high': interval[1] if interval else None,
                    'p_raw_saved': value.get('p_raw'), 'p_holm_saved': value.get('p_holm'),
                    'unique_tasks': expected_n, 'chains': 20, 'status': value.get('status'),
                    'source_pointer': f'/strata/{si}/comparisons/{ci}/metrics/{metric}',
                    'statistics_recomputed': False})
    if len(result) != 10:
        raise ValueError('new report must not introduce another model/topology family')
    return result


def figure_effect_rows(saved, primary):
    """Original three cost panels; tertiary endpoint stays exploratory."""
    rows = [{'endpoint_scope': 'sole_primary_ten_test_family', **row} for row in primary]
    metric = 'local_state_materialized_bytes'
    for si, stratum in enumerate(saved['strata']):
        for ci, comparison in enumerate(stratum['comparisons']):
            value = comparison.get('metrics', {}).get(metric, {})
            interval = value.get('difference_ci95')
            rows.append({'baseline': comparison['baseline'], 'target': comparison['target'], 'metric': metric,
                'baseline_mean': value.get('baseline_mean'), 'target_mean': value.get('target_mean'),
                'paired_difference': value.get('paired_difference'), 'relative_change': value.get('relative_change'),
                'relative_change_ci95': value.get('relative_change_ci95'),
                'ci95_low': interval[0] if interval else None, 'ci95_high': interval[1] if interval else None,
                'unique_tasks': comparison['planned_unique_tasks'], 'chains': comparison['planned_chains'],
                'status': value.get('status'), 'source_pointer': f'/strata/{si}/comparisons/{ci}/metrics/{metric}',
                'endpoint_scope': 'saved_secondary_exploratory_not_in_primary_Holm_family',
                'statistics_recomputed': False})
    return rows


def saved_quality_rows(saved):
    rows = []
    for si, stratum in enumerate(saved['strata']):
        for ci, comparison in enumerate(stratum['comparisons']):
            quality = comparison.get('quality') or {}
            rows.append({'baseline': comparison['baseline'], 'target': comparison['target'],
                         'unique_tasks': comparison['planned_unique_tasks'], 'chains': comparison['planned_chains'],
                         'paired_difference': quality.get('paired_difference'),
                         'quality_conservative_ci95': quality.get('quality_conservative_ci95'),
                         'quality_one_sided_lower95': quality.get('quality_difference_one_sided_lower95'),
                         'complete_saved_quality_object': quality,
                         'source_pointer': f'/strata/{si}/comparisons/{ci}/quality',
                         'noninferiority_claim': 'WITHHELD_PROSPECTIVELY', 'statistics_recomputed': False})
    return rows


def secondary_rows(authority, relative):
    rows = []
    for line, item in authority.lines(relative):
        for metric, estimate in item.get('metrics', {}).items():
            ci = estimate.get('ci95'); quantiles = estimate.get('observed_unit_percentiles') or {}
            rows.append({k: item.get(k) for k in ('phase', 'model', 'model_id', 'topology', 'dimension', 'level',
                'population_facet', 'arm', 'planned_units', 'terminal_units', 'missing_units', 'failed_terminal_units',
                'unique_tasks', 'chain_count')} | {
                'metric': metric, 'value': estimate.get('task_mean'),
                'ci95_low': ci[0] if ci else None, 'ci95_high': ci[1] if ci else None,
                'p50': quantiles.get('p50'), 'p95': quantiles.get('p95'), 'p99': quantiles.get('p99'),
                'known_units': estimate.get('known_units'), 'unknown_units': estimate.get('unknown_units'),
                'applicable_planned_units': estimate.get('applicable_planned_units'),
                'status': estimate.get('status'), 'source_pointer': f'{relative}:line{line}/metrics/{metric}',
                'inference_scope': 'saved descriptive values; no p/second Holm'})
    return rows


def audited_descriptive_facts(observations, audit_sha):
    """Deterministic reporting counts/partition mass only; no CI/test/quality repair."""
    if len(observations) != 6504 or len({r['run_id'] for r in observations}) != 6504:
        raise ValueError('automatic full projection must retain all6504 actual terminal identities')
    if any(r.get('schema_version') != 'audited-observations-projection-v1' for r in observations):
        raise ValueError('unknown audited-observation projection schema')
    main = [r for r in observations if r.get('phase') == 'main']
    public = [r for r in observations if r.get('phase') == 'public_calibration']
    if len(main) != 2160 or len(public) != 144:
        raise ValueError('public/main projection split changed')
    failures = Counter((r['phase'], r['arm'], r.get('error_type') or 'UNKNOWN_RECORDED_ERROR_TYPE')
                       for r in observations if r.get('success') is False)
    failure_rows = [{'phase': k[0], 'arm': k[1], 'error_type': k[2], 'failed_terminal_units': n}
                    for k, n in sorted(failures.items())]
    categories = sorted({k for r in main for k in (r.get('exclusive_spans_sec') or {})})
    spans = []; memory = []
    for arm in ARMS:
        group = [r for r in main if r['arm'] == arm]
        valid_partition = len(group) == 360 and all(isinstance(r.get('exclusive_spans_sec'), dict) and
            finite(r.get('wall_sec')) and all(finite(v) and v >= 0 for v in r['exclusive_spans_sec'].values()) and
            abs(sum(r['exclusive_spans_sec'].values())-r['wall_sec']) < 1e-6 for r in group)
        for category in categories:
            spans.append({'arm': arm, 'category': category, 'planned_units': 360,
                'value_sec': sum(r['exclusive_spans_sec'].get(category, 0.) for r in group)/360 if valid_partition else None,
                'status': 'COMPLETE_RECORDED_EXCLUSIVE_PARTITION_MASS' if valid_partition else 'UNKNOWN_PARTITION_RETAINED',
                'zero_scope': 'absent category in complete partition means no attributed span mass; never unsupported path latency0'})
        if arm.endswith('1'):
            known = len(group) == 360 and all(finite(r.get('memory_count_before')) and
                r['memory_count_before'] >= 0 and 'memory_adopted_id' in r and
                (r['memory_adopted_id'] is None or isinstance(r['memory_adopted_id'], str) and bool(r['memory_adopted_id'])) and
                isinstance(r.get('success'), bool) for r in group)
            eligible = [r for r in group if finite(r.get('memory_count_before')) and r['memory_count_before'] > 0]
            adopted = [r for r in group if r.get('memory_adopted_id')]
            memory.append({'arm': arm, 'planned_units': 360, 'scope': 'main only; observed adoption is not causal benefit',
                'eligible_nonempty_store_units': len(eligible) if known else None,
                'actually_adopted_units': len(adopted) if known else None,
                'adoption_rate_nonempty_denominator': len(adopted)/len(eligible) if known and eligible and
                    all(r in eligible for r in adopted) else None,
                'success_after_adoption': sum(r['success'] is True for r in adopted)/len(adopted) if known and adopted else None,
                'status': 'OBSERVED_DESCRIPTIVE_COUNTS' if known else 'UNKNOWN_FIELDS_RETAINED'})
    return {'status': 'SAVED_DESCRIPTIVE_COUNTS_FROM_AUDITED_PROJECTION_NO_INFERENCE',
            'authority_metric_audit_sha256': audit_sha, 'observed_terminal_units': 6504,
            'human_review_performed': False, 'human_raters': 0, 'preselected_spot30_claimed': False,
            'quality_rescored': False, 'errors': failure_rows, 'exclusive_spans': spans, 'memory': memory,
            'method': 'integer counts and mean recorded exclusive partition mass only; primary/secondary CI copied elsewhere, never recomputed'}


def audited_candidate_facts(authority, audit, observations):
    """Narrow post-audit projection: memory retrieve metadata, never query text.

    This is the sole raw-reader exception. It is reachable only after the global
    full-audit gate, and reads one hash/byte-bound non-provider event stream.
    Candidate hits use observed retrieve events, not adoption or causal benefit.
    """
    relative = 'raw/memory_events.jsonl'
    path = (authority.out/relative).resolve()
    expected = nested(audit.get('evidence'), relative, {})
    if not path.is_relative_to(authority.out) or not path.is_file() or path.stat().st_size != expected.get('bytes'):
        raise ValueError('audited memory event identity/byte length is unavailable')
    actual_sha = digest(path)
    if actual_sha != expected.get('sha256'):
        raise ValueError('memory event bytes differ from the completed full audit')
    authority.sources[relative] = actual_sha
    by_id = {r['run_id']: r for r in observations}
    groups = {arm: {'candidate_queries': 0, 'queries_with_any_candidate': 0,
        'automatic_related_candidate_queries': 0, 'candidate_rows': 0,
        'query_units': set(), 'related_units': set()} for arm in ('T1', 'S1', 'SS1')}
    projected = []
    fields = ('memory_id', 'source_task_id', 'source_chain_id',
              'related_under_automatic_causal_rule', 'label_policy_version')
    with path.open('rb') as stream:
        for line_no, raw_line in enumerate(stream, 1):
            if not raw_line.strip():
                continue
            event = json.loads(raw_line)
            if event.get('event') != 'retrieve':
                continue
            identity = by_id.get(event.get('run_id'))
            if identity is None or any(key in event and event[key] != identity.get(key) for key in ('phase', 'arm', 'task_id')):
                raise ValueError('memory retrieve identity differs from the audited terminal projection')
            candidates = event.get('candidates')
            if not isinstance(candidates, list):
                raise ValueError('recorded retrieve event has no explicit candidates list')
            safe = []
            for candidate in candidates:
                if not isinstance(candidate, dict) or any(key not in candidate for key in fields):
                    raise ValueError('audited candidate metadata is incomplete')
                if (not isinstance(candidate['related_under_automatic_causal_rule'], bool) or
                    candidate['label_policy_version'] != 'causal_same_chain_any_prior_v1' or
                    any(not isinstance(candidate[key], str) or not candidate[key] for key in fields[:3])):
                    raise ValueError('candidate relation/policy/source identity is not explicitly observed')
                safe.append({key: candidate[key] for key in fields})
            projected.append({'run_id': identity['run_id'], 'phase': identity['phase'], 'arm': identity['arm'],
                'task_id': identity['task_id'], 'event': 'retrieve', 'candidates': safe,
                'raw_file': relative, 'raw_line': line_no, 'raw_line_sha256': hashlib.sha256(raw_line).hexdigest(),
                'source_semantics': 'recorded candidate metadata only; no query, score, record, RPC or authorization body'})
            if identity['phase'] != 'main':
                continue
            arm = identity['arm']
            if arm not in groups:
                raise ValueError('memory-disabled main arm unexpectedly has a retrieve event')
            group = groups[arm]; related = any(c['related_under_automatic_causal_rule'] for c in safe)
            group['candidate_queries'] += 1
            group['queries_with_any_candidate'] += bool(safe)
            group['automatic_related_candidate_queries'] += related
            group['candidate_rows'] += len(safe)
            group['query_units'].add(identity['run_id'])
            if related:
                group['related_units'].add(identity['run_id'])
    summaries = []
    for arm, group in groups.items():
        planned = sum(r['phase'] == 'main' and r['arm'] == arm for r in observations)
        queries = group['candidate_queries']
        summaries.append({key: group[key] for key in ('candidate_queries', 'queries_with_any_candidate',
            'automatic_related_candidate_queries', 'candidate_rows')} | {
            'arm': arm, 'planned_task_units': planned, 'observed_retrieve_task_units': len(group['query_units']),
            'task_units_without_recorded_retrieve': planned-len(group['query_units']),
            'task_units_with_related_candidate': len(group['related_units']),
            'candidate_related_rate_query_denominator': group['automatic_related_candidate_queries']/queries if queries else None,
            'status': 'OBSERVED_RETRIEVE_EVENT_COUNTS_NOT_ADOPTION', 'source_file': relative,
            'source_sha256': actual_sha, 'source_bytes': expected['bytes'],
            'scope': 'same-chain any-prior automatic rule; event denominator; no retrieve is not a false candidate or failed adoption; not causal benefit'})
    return {'summaries': summaries, 'projected_events': projected}


def offline_assets(manifest, destination):
    """Copy only explicitly approved offline assets; never parse old real/retrieval."""
    if manifest.get('status') != 'APPROVED_OFFLINE_BYTE_ASSET_MANIFEST':
        raise ValueError('offline template is not approved evidence')
    copied = []
    for batch in manifest.get('batches', []):
        if batch.get('evidence_class') != 'OFFLINE_MECHANISM_BATCH':
            raise ValueError('offline manifest cannot authorize old real or retrieval batches')
        root = Path(batch['source_root']).resolve(); batch_id = batch['batch_id']
        if re.fullmatch(r'[A-Za-z0-9_-]+', batch_id) is None:
            raise ValueError('unsafe batch ID')
        for asset in batch.get('assets', []):
            relative = asset['relative_path']; topic = asset.get('topic')
            if topic not in OFFLINE_TOPICS or any(x in relative.lower() for x in ('retrieval', 'provider_calls', 'task_runs', 'table_06.csv', 'statistics_main')):
                raise ValueError('asset is outside offline topic whitelist')
            safe_name = Path(relative).name
            dedicated = (re.fullmatch(r'figure_0[568](?:_(?:single|double))?\.(?:csv|pdf|svg|png)', safe_name) or
                safe_name in {'table_04.csv', 'state_paths.csv', 'quantization.csv', 'stress_chain_concurrency.csv',
                    'stress_growth_slopes.csv', 'stress_fault_recovery.csv', 'stress_fault_semantic_audit.json',
                    'state_matrix_plan.json', 'state_matrix_status.json', 'quantization_plan.json', 'quantization_status.json',
                    'state_reuse_plan.json', 'state_reuse_status.json', 'stress_matrix_plan.json', 'stress_matrix_audit.json',
                    'stress_fault_plan.json', 'stress_continuous_100_status.json', 'protocol_v3_cells.csv',
                    'protocol_v3_audit.json', 'protocol_v3_plan.json', 'protocol_v3_status.json'} or
                re.fullmatch(r'(?:state_batch_[123]|protocol_v3_batch_[123]|stress_v3_L\d+_K\d+_(?:status|process)|stress_fault_[a-z_]+_(?:status|process))\.json', safe_name) or
                (relative.startswith('state_measurement_correction_v3/') and safe_name in {
                    'stage_cells.csv', 'matrix_audit.json', 'stage_join_audit.json', 'manifest.json',
                    'prospective_registration.json', 'execution.json'}))
            if not dedicated:
                raise ValueError('old asset is not a dedicated offline figure/table/audit; mixed report metadata is hash-reference only')
            source = (root/relative).resolve()
            if not source.is_relative_to(root) or source.suffix.lower() not in ('.csv', '.json', '.pdf', '.svg', '.png', '.txt') or '/raw/' in source.as_posix():
                raise ValueError('old raw bodies are referenced by hash only, never copied/read here')
            if digest(source) != asset['sha256']:
                raise ValueError('approved offline asset changed')
            target = destination/'offline_batches'/batch_id/relative; target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError('offline asset target exists')
            shutil.copyfile(source, target)
            if digest(target) != asset['sha256']:
                raise ValueError('offline byte copy mismatch')
            figure_target = None
            if asset.get('target_figure') in (5, 6, 8):
                figure_target = destination/'figures'/safe_name
                if figure_target.exists():
                    raise ValueError('dedicated offline figure mirror already exists')
                shutil.copyfile(target, figure_target)
                if digest(figure_target) != asset['sha256']:
                    raise ValueError('offline main-figure byte mirror mismatch')
            copied.append({**asset, 'batch_id': batch_id, 'evidence_class': 'OFFLINE_MECHANISM_BATCH',
                           'copied_relative_path': target.relative_to(destination).as_posix(),
                           'main_figure_relative_path': figure_target.relative_to(destination).as_posix() if figure_target else None,
                           'measurement_scope': batch['measurement_scope'], 'limitations': batch['limitations'],
                           'raw_hash_references': batch.get('raw_hash_references', []),
                           'is_new_real_evidence': False})
    return copied


def numeric_csv(rows):
    result = []
    for row in rows:
        converted = {}
        for key, value in row.items():
            if value == '':
                converted[key] = None
            elif key in ('pool_size', 'build', 'planned_query_units', 'recorded_query_units'):
                converted[key] = int(value) if value not in (None, 'ALL3') else value
            elif isinstance(value, str) and (key in ('p_at_1', 'recall_at_3', 'recall_at_10', 'mrr', 'ndcg_at_10', 'false_retrieval', 'refusal') or
                                           key.startswith('latency_') or '_ci95_' in key):
                converted[key] = float(value)
            else:
                converted[key] = value
        result.append(converted)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study-out', required=True, type=Path)
    p.add_argument('--primary', default='analysis/stats/formal_primary_v1.json')
    p.add_argument('--secondary-dir', default='analysis/postrun/secondary_heterogeneity_v1')
    p.add_argument('--metric-audit-dir', default='analysis/full_metric_audit_v1')
    p.add_argument('--retrieval-dir', default='analysis/retrieval_postrun_analysis_v1')
    p.add_argument('--offline-manifest', type=Path)
    p.add_argument('--external-equivalence', help='new saved equivalence plus already-estimated matched summary; optional')
    p.add_argument('--version', default='report_adapter_v1')
    p.add_argument('--render', action='store_true', help='future rendering only after numerical bundle review')
    args = p.parse_args(); out = args.study_out.resolve()
    if out.name != STUDY or re.fullmatch(r'report_adapter_v[1-9][0-9]*', args.version) is None:
        p.error('exact new-study root/new version required')
    destination = out/args.version
    if destination.exists():
        raise RuntimeError('report destination exists; never replace an earlier report')
    a = Authorities(out)
    lifecycle = a.json('formal_execution_v1/execution.json')
    stages = lifecycle.get('stages') or []
    if (lifecycle.get('status') != 'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT' or
        not lifecycle.get('finished_utc') or lifecycle.get('supervised_child_observed_running') is True or
        [stage.get('name') for stage in stages] != ['main', 'native_remaining', 'external'] or
        any(stage.get('status') != 'TERMINAL' or stage.get('exit_code') != 0 or not stage.get('finished_utc') for stage in stages)):
        raise RuntimeError('real timing still live or unresolved')
    if digest(a.path('manifests/formal-freeze.json')) != FREEZE_SHA or digest(a.path('formal_snapshot_v1/drivers/statistical_gates.py')) != ENTRY_SHA:
        raise ValueError('new formal freeze/statistical entry differs from integrated authority')
    saved = a.json(args.primary); audit = a.json(args.metric_audit_dir+'/metric_audit.json')
    if saved.get('formal_freeze_sha256') != FREEZE_SHA or saved.get('collection_status') != 'COMPLETE' or saved.get('study_id') != STUDY:
        raise ValueError('saved primary incomplete/wrong identity')
    if audit.get('status') not in ('PASS_OBSERVED_FULL_RECORD_JOINS', 'INCONCLUSIVE_RETAINED') or audit.get('study_id') != STUDY or audit.get('terminal_units') != 6504 or audit.get('finding_counts', {}).get('FAIL', 0) or not audit.get('automatic_full_validation'):
        raise ValueError('full automatic audit is incomplete/FAIL; local0 findings never bypass globalFAIL')
    contract_path = a.path('analysis/stats/preformal_gate_v1/statistical_contract.json')
    if saved.get('statistical_contract_sha256') != digest(contract_path):
        raise ValueError('saved primary is not bound to the actual prospective statistical contract')
    for filename, wanted in saved.get('raw_input_sha256', {}).items():
        if nested(audit.get('evidence'), 'raw/'+filename, {}).get('sha256') != wanted:
            raise ValueError('saved primary and completed full audit used different raw bytes')
    if set(saved.get('raw_input_sha256', {})) != {'task_runs.jsonl', 'provider_calls.jsonl', 'provider_attempt_starts.jsonl', 'task_unit_starts.jsonl'}:
        raise ValueError('saved primary has incomplete raw identity headers')
    projection = args.metric_audit_dir+'/audited_observations.jsonl'
    if digest(a.path(projection)) != audit.get('output_sha256', {}).get('audited_observations.jsonl'):
        raise ValueError('audited projection changed')
    secondary_receipt = a.json(args.secondary_dir+'/receipt.json')
    if (secondary_receipt.get('study_id') != STUDY or secondary_receipt.get('original_statistical_entry_sha256') != ENTRY_SHA or
        secondary_receipt.get('original_formal_freeze_sha256') != FREEZE_SHA or
        secondary_receipt.get('frozen_boundaries_sha256') != BOUNDARIES_SHA or
        secondary_receipt.get('status') != 'COMPLETE_DESCRIPTIVE_ANALYSIS'):
        raise ValueError('secondary boundaries/authority differ or collection incomplete')
    if secondary_receipt.get('raw_input_sha256') != saved['raw_input_sha256']:
        raise ValueError('secondary and sole primary used different real ledgers')
    for filename, wanted in secondary_receipt.get('trace_input_sha256', {}).items():
        if wanted is not None and nested(audit.get('evidence'), 'raw/'+filename, {}).get('sha256') != wanted:
            raise ValueError('secondary traces and completed full audit used different bytes')
    for name, wanted in secondary_receipt.get('files', {}).items():
        if digest(a.path(args.secondary_dir+'/'+name)) != wanted:
            raise ValueError('saved secondary output changed')
    retrieval_audit = a.json(args.retrieval_dir+'/audit.json')
    if retrieval_audit.get('study_id') != STUDY or retrieval_audit.get('observed_query_units') != 14400 or retrieval_audit.get('findings'):
        raise ValueError('new retrieval does not have intact complete identities')
    for name, wanted in retrieval_audit.get('output_sha256', {}).items():
        if digest(a.path(args.retrieval_dir+'/'+name)) != wanted:
            raise ValueError('saved new retrieval output changed')
    primary = primary_rows(saved); quality = saved_quality_rows(saved)
    secondary = secondary_rows(a, args.secondary_dir+'/heterogeneity.jsonl')
    overall = [r for r in secondary if r['phase'] == 'main' and r['dimension'] == 'overall' and r['population_facet'] == 'all_formal']
    domain = [r for r in secondary if r['phase'] == 'main' and r['dimension'] == 'domain' and
              r['population_facet'] == 'all_formal' and r['metric'] == 'quality_success']
    observations = [r for _, r in a.lines(projection)]
    facts = audited_descriptive_facts(observations, digest(a.path(args.metric_audit_dir+'/metric_audit.json')))
    candidates = audited_candidate_facts(a, audit, observations)
    facts['candidate_queries'] = candidates['summaries']
    retrieval = numeric_csv(a.csv(args.retrieval_dir+'/retrieval_quality_scale.csv'))
    if len(retrieval) != 20:
        raise ValueError('saved pooled-build retrieval table must have20 cells')
    for name in ('analysis', 'figures', 'tables', 'appendix'):
        (destination/name).mkdir(parents=True)
    # Save exact sole-authority bytes, then project values to report tables.
    a.copy(args.primary, destination/'analysis/primary_authority.json')
    a.copy(args.metric_audit_dir+'/metric_audit.json', destination/'analysis/full_audit_authority.json')
    a.copy(args.retrieval_dir+'/audit.json', destination/'analysis/retrieval_authority.json')
    write_json(destination/'analysis/saved_descriptive_facts.json', facts)
    write_jsonl(destination/'appendix/audited_memory_retrieve_candidates.jsonl', candidates['projected_events'])
    write_json(destination/'appendix/all_formal_phase_statistics.json', saved.get('formal_phase_statistics'))
    write_json(destination/'appendix/all14_matched_phase_effects.json', saved.get('matched_phase_effects'))
    for name in ('heterogeneity.jsonl', 'paired_heterogeneity.jsonl', 'measurement_events.jsonl', 'coverage.json'):
        a.copy(args.secondary_dir+'/'+name, destination/'appendix'/name)
    for name in ('retrieval_quality_scale_by_build.csv', 'cell_coverage.csv', 'query_observations.jsonl'):
        a.copy(args.retrieval_dir+'/'+name, destination/'appendix'/('new_'+name))
    a.copy(projection, destination/'appendix/automatic6504_observations.jsonl')
    offline_manifest_sha = digest(args.offline_manifest) if args.offline_manifest else None
    offline = offline_assets(json.loads(args.offline_manifest.read_text(encoding='utf-8')), destination) if args.offline_manifest else []
    if args.offline_manifest:
        shutil.copyfile(args.offline_manifest, destination/'analysis/approved_offline_manifest.json')
    write_json(destination/'analysis/offline_batch_index.json', offline)
    environment = a.json('public_pilot_v1/environment.json')
    write_csv(destination/'tables/table_01.csv', [{'section': 'environment', 'field': k, 'value': v} for k, v in environment.items()])
    write_csv(destination/'tables/table_02.csv', [{'arm': arm, 'representation': 'lean full-payload text' if arm.startswith('T') else 'native structured',
        'state_enabled': arm.startswith('SS'), 'memory_enabled': arm.endswith('1'),
        'quality_contract': 'fixed domain canonical diagnosis + authored readonly fixture action',
        'validation': 'actual automatic6504 scoped audit; no human or preselected30 claim'} for arm in ARMS])
    write_csv(destination/'tables/table_03.csv', overall)
    write_csv(destination/'tables/table_04.csv', [r for r in secondary if r['phase'] == 'main' and r['dimension'] == 'overall' and r['metric'] not in
        ('quality_success', 'provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens', 'wall_sec', 'provider_wait_sec')] +
        [{'section': 'separate_old_offline_batch_asset', **r} for r in offline if r.get('target_table') == 4])
    write_csv(destination/'tables/table_05.csv', [{'section': 'new_offline_retrieval', **r} for r in retrieval] +
        [{'section': 'new_main_recorded_candidate_queries', **r} for r in facts['candidate_queries']] +
        [{'section': 'new_main_recorded_adoption', **r} for r in facts['memory']] +
        [{'section': 'new_main_paired_memory_effect', **r} for r in primary if (r['baseline'], r['target']) in PAIRS[2:]] +
        [{'section': 'new_main_paired_memory_quality', **r} for r in quality if (r['baseline'], r['target']) in PAIRS[2:]])
    write_csv(destination/'tables/table_06.csv', [r for r in secondary if r['phase'].startswith('external_') and r['dimension'] == 'overall' and r['population_facet'] == 'all_formal'] +
        [{'section': 'separate_old_offline_fault_asset', **r} for r in offline if r.get('target_table') == 6])
    write_csv(destination/'appendix/primary_ten_saved_endpoints.csv', primary)
    write_csv(destination/'appendix/five_saved_quality_bounds.csv', quality)
    write_csv(destination/'appendix/all_heterogeneity_saved_values.csv', secondary)
    write_csv(destination/'appendix/automatic_failure_type_counts.csv', facts['errors'])
    write_csv(destination/'appendix/recorded_exclusive_partition_mass.csv', facts['exclusive_spans'])
    edges = [{'from': x, 'to': y, 'meaning': meaning} for x, y, meaning in (
        ('planner', 'retriever', 'logical role control'), ('retriever', 'executor', 'resolved evidence'),
        ('executor', 'reviewer', 'canonical action/verifier'), ('runtime', 'UDS_router', 'framed control and ACK'),
        ('planner', 'state_exchange', 'produce native state reference'),
        ('state_exchange', 'retriever', 'read/decode/rank operands'),
        ('retriever', 'memory', 'candidate retrieval'), ('executor', 'memory', 'recorded actual adoption'),
        ('reviewer', 'memory', 'feedback/write'))]
    figure_rows = {1: edges, 2: figure_effect_rows(saved, primary),
        3: domain + [{'kind': 'recorded_failure_type_count', **r} for r in facts['errors'] if r['phase'] == 'main'],
        4: [r for r in overall if r['metric'] in ('wall_sec', 'provider_wait_fraction')] +
            [{'kind': 'recorded_exclusive_partition_mass', **r} for r in facts['exclusive_spans']], 7: retrieval}
    captions = {
        1: 'Conceptual measured paths; no numerical performance claim.',
        2: 'Saved d87: input-token/observable-IPC endpoints alone form the sole ten-test family; local materialized bytes are a saved exploratory panel. S0→SS0 uses108, other pairs120; no second Holm or seed rerun.',
        3: 'Saved domain descriptive quality; missing/constant-quality CI remains absent; all planned failures retained. Failure counts are separately indexed.',
        4: 'Recorded exclusive partition mass plus saved SDK wait share/task quantiles in distinct panels. SDK wait is neither queue time nor RTT; inclusive traces are never added to wall.',
        7: 'New240-query/20-chain retrieval only; failures/nulls preserved; old retrieval never selected.',
    }
    availability = []
    for number, rows in figure_rows.items():
        write_csv(destination/'figures'/f'figure_{number:02}.csv', rows)
        availability.append({'figure': number, 'status': 'NUMERIC_SOURCE_PREPARED_REQUIRES_RENDER_QA', 'caption': captions[number]})
    for number in (5, 6, 8):
        assets = [r for r in offline if r.get('target_figure') == number]
        required = {f'figure_{number:02}.csv'} | {f'figure_{number:02}_{width}.{ext}' for width in ('single', 'double') for ext in ('pdf', 'svg', 'png')}
        present = {Path(r['relative_path']).name for r in assets}
        availability.append({'figure': number,
            'status': 'HISTORICAL_OFFLINE_BYTES_INDEXED_REQUIRES_ACTUAL_QA' if required <= present else 'INCOMPLETE_APPROVED_OFFLINE_BATCH_ASSETS',
            'missing_required_assets': sorted(required-present),
            'batch_assets': [r['copied_relative_path'] for r in assets], 'is_new_real_evidence': False})
    external = a.json(args.external_equivalence) if args.external_equivalence else None
    external_rows = None
    if external and external.get('status') == 'PASS' and external.get('comparison_scope') == 'SYSTEM_LEVEL_REFERENCE':
        summary_path = external.get('saved_matched_summary_path')
        if summary_path and digest(a.path(summary_path)) == external.get('saved_matched_summary_sha256'):
            external_rows = a.json(summary_path).get('rows')
            keys = {(r.get('system'), r.get('metric')) for r in external_rows or []}
            expected = {(s, m) for s in ('native_T0', 'autogen', 'langgraph') for m in ('quality_success', 'provider_input_tokens', 'wall_sec')}
            if len(external_rows or []) != 9 or keys != expected or any(r.get('planned_units') != 120 or r.get('unique_tasks') != 60 for r in external_rows):
                raise ValueError('saved external reference is not the same60 tasks/two repeats; do not recalculate or broaden')
            write_csv(destination/'figures/figure_09.csv', external_rows)
    availability.append({'figure': 9, 'status': 'SAVED_MATCHED_REFERENCE_REQUIRES_VISUAL_QA' if external_rows else 'OPTIONAL_NOT_RENDERED_PENDING_EQUIVALENCE_AND_SAVED_MATCHED_SUMMARY',
                         'equivalence_status': nested(external, 'status'), 'scope': 'system-level reference only; never single-mechanism attribution'})
    if args.render:
        import pure_report_plots as plots
        for number, draw in ((1, plots.overview), (2, plots.paired_effects), (3, plots.domain_quality), (4, plots.timing_saved), (7, plots.retrieval_curves)):
            plots.export(draw, figure_rows[number], destination/'figures', f'figure_{number:02}')
        if external_rows:
            plots.export(plots.external_reference, external_rows, destination/'figures', 'figure_09')
    # Keep every predeclared effect in abstract facts; do not cherry-pick a better
    # archive/secondary estimate or manufacture a T0→SS1 effect absent upstream.
    write_json(destination/'analysis/abstract_metrics.json', {'status': 'AUDITED_SAVED_VALUES_REQUIRES_CLAIM_AND_VISUAL_QA',
        'primary_endpoints': primary, 'saved_quality_bounds': quality, 'quality_noninferiority': 'WITHHELD_PROSPECTIVELY',
        'quality_scope': 'canonical domain diagnosis/action fixture, not general Linux accuracy',
        'public_result': 'retained separately; never formal effect population', 'human_review': 'NOT_COLLECTED_AUTOMATIC_FULL_AUDIT',
        'no_statistics_recomputed': True})
    a.stable()
    if args.offline_manifest and digest(args.offline_manifest) != offline_manifest_sha:
        raise ValueError('approved offline manifest changed during report')
    write_json(destination/'analysis/report_availability.json', {'status': 'PREPARED_REQUIRES_POSTRUN_SCHEMA_NUMERIC_AND_ACTUAL_VISUAL_QA',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'figures': availability,
        'tables': [1, 2, 3, 4, 5, 6], 'automatic_validation': {'observed_units': 6504, 'human_raters': 0,
        'preselected30_claimed': False, 'claim_scope': audit.get('claim_scope'), 'limits': audit.get('limits')},
        'primary_authority': args.primary, 'primary_entry_sha256': ENTRY_SHA,
        'new_confirmatory_families': 0, 'statistical_kernels_called': 0, 'old_real_or_retrieval_pooled': False,
        'approved_offline_manifest_sha256': offline_manifest_sha,
        'source_sha256': a.sources, 'adapter_sha256': digest(Path(__file__)),
        'outputs_sha256': {p.relative_to(destination).as_posix(): digest(p) for p in destination.rglob('*') if p.is_file()}})
    print(json.dumps({'status': 'REPORT_BUNDLE_PREPARED_REQUIRES_NUMERIC_VISUAL_QA', 'output': str(destination), 'rendered': args.render}))


if __name__ == '__main__':
    main()
