"""Post-collection descriptive heterogeneity and secondary measurement analysis.

Prepared without formal-outcome access. This does not replace statistical_gates:
it emits no p-values, Holm adjustment, noninferiority claim or matched-phase test.
Run outside provider/performance timing after the collection is quiescent.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path

STUDY = 'protocol_repair_20261020_v1'
OUT = Path('/evidence') / STUDY
HERE = Path(__file__).resolve().parent
BOUNDARIES = OUT / 'analysis/stats/preformal_validation_v1/frozen_analysis_boundaries.json'
BOUNDARIES_SHA256 = 'f1e190ab50be5cc1b616b692bebb3f3e5b9a43b851f5554aab158e0da15a5cd4'
GATES_SHA256 = 'd87bb161630560f4fe7c62e061a167e36ba5d6c93a9c7a5dbd2e4d12c5240251'
BOOTSTRAPS = 10000
PAIRS = (('T0', 'S0'), ('S0', 'SS0'), ('T0', 'T1'), ('S0', 'S1'), ('SS0', 'SS1'))
TASK_METRICS = ('provider_input_tokens', 'provider_output_tokens', 'provider_total_tokens',
    'provider_cached_input_tokens', 'provider_attempts', 'observable_ipc_bytes', 'local_state_materialized_bytes',
    'logical_state_bytes', 'wall_sec', 'provider_wait_sec', 'runtime_cpu_sec',
    'sampled_peak_process_group_rss', 'sampled_peak_process_group_fds',
    'terminal_record_flush_sec_excluded_from_wall', 'durable_io_sec_included_in_wall')
# Each is a separate observed scope; their sums are never a task-wall partition.
TRACE_METRICS = {
    'state_prepare_ns': ('state_produce', 'prepare_ns', 'state'),
    'state_materialize_ns': ('state_buffer_materialize', 'materialize_ns', 'state'),
    'state_os_mapping_ns': ('state_os_mapping', 'mapping_ns', 'state'),
    'state_view_construct_ns': ('state_view_construct', 'view_construct_ns', 'state'),
    'state_resolve_ns': ('state_resolve', 'resolve_ns', 'state'),
    'state_fused_read_similarity_ns': ('state_consumer_compute', 'read_similarity_compute_ns', 'state'),
    'state_stable_sort_ns': ('state_consumer_compute', 'stable_sort_ns', 'state'),
    'state_reorder_ns': ('state_consumer_compute', 'reorder_ns', 'state'),
    'state_rpc_parent_service_reply_ns': ('state_rpc_handoff', 'handoff_parent_service_and_reply_ns', 'process_state'),
    'state_rpc_reply_send_ns': ('state_rpc_handoff', 'reply_send_ns', 'process_state'),
    'vector_index_search_ns': ('index_operation', 'index_latency_ns', 'memory'),
    'vector_index_add_ns': ('index_operation', 'index_latency_ns', 'memory'),
    'vector_index_remove_ns': ('index_operation', 'index_latency_ns', 'memory'),
    'state_individual_delete_ns': ('state_buffer_release', 'release_ns', 'state'),
    'state_shared_close_ns': ('state_buffer_release', 'shared_group_release_ns', 'state'),
}
METRICS = ('quality_success', *TASK_METRICS, 'provider_wait_fraction', *TRACE_METRICS)
SAFE_STATE_FIELDS = ('event', 'uri', 'buffer_key', 'start_ns', 'end_ns', 'error_type',
    'prepare_ns', 'materialize_ns', 'local_copied_bytes', 'mapping_event_count', 'mapping_status',
    'mapping_ns', 'mapped_virtual_bytes', 'mapping_owner_pid', 'view_construct_ns', 'new_os_mapping',
    'resolve_ns', 'consumer_service_pid', 'native_integrity_status', 'consumer_pid', 'consumer_role',
    'read_similarity_compute_ns', 'stable_sort_ns', 'reorder_ns', 'read_and_compute_fused',
    'read_boundary', 'handoff_parent_service_and_reply_ns', 'reply_send_ns', 'reply_payload_bytes',
    'reply_framed_socket_bytes', 'service_start_ns', 'service_end_ns', 'send_start_ns',
    'release_ns', 'shared_release_group', 'shared_group_release_ns', 'individual_uri_release_ns',
    'buffer_key_absent', 'boundary', 'bytes_policy')
SAFE_INDEX_FIELDS = ('event', 'operation', 'parent_memory_operation', 'start_ns', 'end_ns',
    'index_latency_ns', 'cpu_ns', 'pid', 'error_type', 'index_class', 'boundary')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def read_rows(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def emit(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')


def stable_seed(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return (20261020 + int(hashlib.sha256(encoded).hexdigest()[:8], 16)) % (2**32)


def load_gates():
    path = HERE.parent / 'statistical_gates.py'
    if digest(path) != GATES_SHA256:
        raise ValueError('registered statistical entry changed')
    spec = importlib.util.spec_from_file_location('_repair_descriptive_gates', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixed_inputs(gates):
    if digest(BOUNDARIES) != BOUNDARIES_SHA256:
        raise ValueError('prospective analysis boundaries changed')
    boundaries = load(BOUNDARIES)
    context = gates.checked_inputs()
    freeze_path = OUT / 'manifests/formal-freeze.json'
    freeze = load(freeze_path)
    contract_path = gates.STATS / 'statistical_contract.json'
    contract = load(contract_path)
    required = [BOUNDARIES, contract_path, HERE.parent / 'statistical_gates.py',
                OUT / 'public_pilot_v1/registration.json']
    required += [OUT / name for name in gates.INPUTS]
    for path in required:
        if freeze['files'].get(str(path)) != digest(path):
            raise ValueError('unbound/changed original frozen input: ' + str(path))
    if (freeze['source_sha256'] != gates.SOURCE or contract['study_id'] != STUDY
            or contract['input_sha256'] != context['input_sha256']
            or contract['public_registration_sha256'] != context['public_registration_sha256']):
        raise ValueError('new-study freeze/statistical identity mismatch')
    for name, expected in contract['files'].items():
        path = Path(name)
        if not path.resolve().is_relative_to(gates.STATS.resolve()) or digest(path) != expected:
            raise ValueError('changed prospective analysis evidence')
    # Use only the already fixed task-to-length-bin map, never model messages.
    frozen_lengths = {r['task_id']: r for r in boundaries['coverage_rule']['frozen_task_input_bytes_and_bins']}
    cases = {r['task_id']: r for r in context['tasks'] if r['split'] == 'formal'}
    if set(cases) != set(frozen_lengths):
        raise ValueError('frozen length population differs from formal corpus')
    for task_id, row in cases.items():
        length = len(json.dumps({'observations': row['observations'], 'procedure': row['procedure']},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))
        if frozen_lengths[task_id]['canonical_fact_input_utf8_bytes'] != length:
            raise ValueError('pre-treatment canonical length changed')
    return context, boundaries, freeze, contract, cases, frozen_lengths


def trace_tables(context, actual_ids):
    traces, hashes = defaultdict(list), {}
    for name in ('states.jsonl', 'memory_events.jsonl', 'observer_summary.jsonl'):
        path = OUT / 'raw' / name
        if not path.exists():
            hashes[name] = None
            continue
        hashes[name] = digest(path)
        for number, row in enumerate(read_rows(path), start=1):
            unit = context['planned'].get(row.get('run_id'))
            if unit is None or any(row.get(key) != unit[key] for key in ('study_id', 'phase', 'task_id',
                    'chain_id', 'domain', 'arm', 'repeat', 'model', 'topology', 'task_sha256', 'effective_contract_sha256')):
                raise ValueError('foreign trace identity: ' + name)
            if unit['phase'] == 'public_calibration':
                continue
            if unit['run_id'] not in actual_ids:
                raise ValueError('trace belongs to task without terminal outcome; collection is not quiescent')
            if row.get('source_sha256') != context['protocol']['source_sha256'] or row.get('model_evidence') != 'real_provider':
                raise ValueError('non-provider/wrong-source trace')
            if name == 'states.jsonl' or row.get('event') == 'index_operation' or name == 'observer_summary.jsonl':
                traces[unit['run_id']].append((name, number, row))
    return traces, hashes


def is_applicable(unit, case, scope):
    state = unit['arm'] in ('SS0', 'SS1') and case['state_spec']['applicable']
    return state if scope == 'state' else state and unit['processes'] if scope == 'process_state' else unit['arm'].endswith('1')


def trace_value(unit, case, items, metric):
    event, field, scope = TRACE_METRICS[metric]
    if not is_applicable(unit, case, scope):
        return {'value': None, 'status': 'NOT_APPLICABLE_PREDECLARED_PATH', 'event_count': 0}
    selected = [row for _, _, row in items if row.get('event') == event]
    if metric.startswith('vector_index_'):
        operation = metric.removeprefix('vector_index_').removesuffix('_ns')
        selected = [row for row in selected if row.get('operation') == operation]
    if metric == 'state_individual_delete_ns':
        selected = [row for row in selected if not row.get('shared_release_group')]
    if metric == 'state_shared_close_ns':
        groups = {}
        for row in selected:
            group = row.get('shared_release_group')
            if not group:
                continue
            signature = tuple(row.get(k) for k in ('start_ns', 'end_ns', field))
            if group in groups and groups[group][0] != signature:
                raise ValueError('conflicting shared release group measurements')
            groups[group] = (signature, row)
        selected = [item[1] for item in groups.values()]
    values = [row.get(field) for row in selected]
    known = [value for value in values if finite(value)]
    complete = bool(values) and len(known) == len(values) and not any(row.get('error_type') for row in selected)
    return {'value': sum(known) if complete else None,
        'status': 'OBSERVED_SCOPE_SUM_NOT_WALL_PARTITION' if complete else 'NOT_OBSERVED' if not values else 'INCOMPLETE_TRACE',
        'event_count': len(values), 'known_event_count': len(known),
        'partial_observed_subtotal': sum(known) if known else None}


def unit_record(unit, case, actual, items, length):
    metrics = {}
    for metric in TASK_METRICS:
        value = actual.get(metric) if actual else None
        metrics[metric] = {'value': value if finite(value) else None,
            'status': 'RECORDED' if finite(value) else 'UNKNOWN_OR_UNSUPPORTED'}
    metrics['quality_success'] = {'value': int(bool(actual and actual.get('success'))),
        'status': 'TERMINAL' if actual else 'MISSING_PLANNED_QUALITY_COUNTS_AS_FAILURE'}
    wall, wait = metrics['wall_sec']['value'], metrics['provider_wait_sec']['value']
    metrics['provider_wait_fraction'] = {'value': wait / wall if wall is not None and wall > 0 and wait is not None else None,
        'status': 'OBSERVED_TASK_SDK_WAIT_SHARE_NOT_PROVIDER_QUEUE_OR_RTT'}
    for name in TRACE_METRICS:
        metrics[name] = trace_value(unit, case, items, name)
    return {**{key: unit[key] for key in ('run_id', 'phase', 'task_id', 'chain_id', 'domain', 'arm',
            'repeat', 'model', 'model_id', 'topology')}, 'difficulty': case['difficulty'],
        'state_class': case['state_spec']['size_class'], 'chain_position': case['observations']['checkpoint'],
        'state_applicability': 'state_applicable' if case['state_spec']['applicable'] else 'state_not_applicable',
        'input_length': length['length_quartile'], 'canonical_fact_input_utf8_bytes': length['canonical_fact_input_utf8_bytes'],
        'terminal_observed': actual is not None, 'success': bool(actual and actual.get('success')),
        'terminal_status': actual.get('status') if actual else 'MISSING_PLANNED_UNIT',
        'metrics': metrics, 'trace_event_counts': dict(Counter(row.get('event', 'observer_summary') for _, _, row in items)),
        'efficiency_interpretation': 'failure/early-stop measured costs retained; low cost alone is not efficiency',
        'interval_accounting': 'state/index/parent scopes may overlap; never add them into task wall or IPC totals'}


def chain_resample_ci(task_values, paired, seed):
    """Frozen domain/whole-chain resampling and task weighting, without signflip.

    task_values already average repeats within a task. This is the CI part of
    the verified algorithm only; no hypothesis test is computed or emitted.
    """
    import numpy as np
    chains = defaultdict(list)
    for row in task_values:
        chains[(row['domain'], row['chain_id'])].append(row)
    domains = defaultdict(list)
    for (domain, chain), values in sorted(chains.items()):
        domains[domain].append(values)
    counts = {domain: len(values) for domain, values in domains.items()}
    if not domains or any(count < 2 for count in counts.values()):
        return {'ci95': None, 'status': 'SPARSE_NOT_ESTIMABLE_AT_LEAST_ONE_DOMAIN_HAS_FEWER_THAN_TWO_CHAINS',
                'chains_per_domain': counts}
    rng = np.random.default_rng(seed)
    numerator = np.zeros(BOOTSTRAPS)
    denominator = np.zeros(BOOTSTRAPS)
    baseline = np.zeros(BOOTSTRAPS) if paired else None
    for _, chain_lists in sorted(domains.items()):
        lengths = np.asarray([len(values) for values in chain_lists], dtype=float)
        means = np.asarray([np.mean([row['difference' if paired else 'value'] for row in values]) for values in chain_lists])
        selected = rng.integers(0, len(chain_lists), size=(BOOTSTRAPS, len(chain_lists)))
        numerator += (means[selected] * lengths[selected]).sum(axis=1)
        denominator += lengths[selected].sum(axis=1)
        if paired:
            aa = np.asarray([np.mean([row['a'] for row in values]) for values in chain_lists])
            baseline += (aa[selected] * lengths[selected]).sum(axis=1)
    result = {'ci95': np.quantile(numerator / denominator, [.025, .975]).tolist(),
        'status': 'EXPLORATORY_DOMAIN_STRATIFIED_CHAIN_BOOTSTRAP', 'draws': BOOTSTRAPS,
        'chains_per_domain': counts, 'unit': 'task means; whole chains resampled independently within domain'}
    if paired:
        with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
            relative = np.divide(numerator, baseline, out=np.full(BOOTSTRAPS, np.nan), where=baseline != 0)
        defined = np.isfinite(relative)
        result['relative_change_defined_draw_count'] = int(np.count_nonzero(defined))
        result['relative_change_undefined_draw_count'] = int(relative.size - np.count_nonzero(defined))
        result['relative_change_ci95_status'] = 'COMPLETE_ALL_DRAWS_DEFINED' if np.all(defined) else 'UNDEFINED_DRAWS_RETAINED'
        result['relative_change_ci95'] = np.quantile(relative, [.025, .975]).tolist() if np.all(defined) else None
    return result


def distribution(records, metric, seed):
    eligible = [r for r in records if not r['metrics'][metric]['status'].startswith('NOT_APPLICABLE')]
    values = [{'run_id': r['run_id'], 'task_id': r['task_id'], 'chain_id': r['chain_id'],
               'domain': r['domain'], 'repeat': r['repeat'], 'value': r['metrics'][metric]['value']} for r in eligible]
    known = [r for r in values if finite(r['value'])]
    result = {'planned_units': len(records), 'applicable_planned_units': len(eligible),
        'structurally_not_applicable_units': len(records) - len(eligible), 'known_units': len(known),
        'unknown_units': len(eligible) - len(known), 'unit_distribution': values,
        'task_mean': None, 'ci95': None, 'status': 'EMPTY_PLANNED_CELL' if not records else 'NOT_APPLICABLE' if not eligible else 'INCOMPLETE_UNKNOWN_RETAINED'}
    if not known or len(known) != len(values):
        return result
    import numpy as np
    bytask = defaultdict(list)
    for row in values:
        bytask[(row['domain'], row['chain_id'], row['task_id'])].append(row['value'])
    tasks = [{'domain': k[0], 'chain_id': k[1], 'task_id': k[2], 'value': float(np.mean(v)),
              'repeat_count': len(v)} for k, v in sorted(bytask.items())]
    result.update({'task_mean': float(np.mean([r['value'] for r in tasks])), 'unique_tasks': len(tasks),
        'chain_count': len({(r['domain'], r['chain_id']) for r in tasks}), 'task_distribution': tasks,
        'observed_unit_percentiles': {name: float(np.quantile([r['value'] for r in known], q))
            for name, q in (('p50', .5), ('p95', .95), ('p99', .99))}})
    interval = chain_resample_ci(tasks, False, seed)
    result.update(interval)
    if metric == 'quality_success' and len({r['value'] for r in tasks}) == 1:
        result.update(ci95=None, status='CONSTANT_QUALITY_BOOTSTRAP_IS_NOT_A_SAFETY_INTERVAL',
            limitation='observed identical task quality cannot establish zero uncertainty; paired exact bounds are reported separately')
    return result


def group_levels(boundaries):
    dimensions = boundaries['heterogeneity_dimensions']
    levels = [('overall', 'all')]
    for dimension in ('domain', 'difficulty', 'state_class', 'chain_position'):
        levels.extend((dimension, level) for level in dimensions[dimension]['levels'])
    levels.extend(('input_length', 'Q' + str(i)) for i in range(1, 5))
    levels.extend(('model', (key, model_id)) for key, values in dimensions['model']['levels'].items() for model_id in values)
    return levels


def select(records, dimension, level, facet):
    def matches(row):
        if facet != 'all_formal' and row['state_applicability'] != facet:
            return False
        if dimension == 'overall':
            return True
        if dimension == 'model':
            return (row['model'], row['model_id']) == tuple(level)
        return row[dimension] == level
    return [row for row in records if matches(row)]


def paired_distribution(kernel, records, metric, seed):
    """Original five within-phase pairs only, for descriptive subgroup effects."""
    pairs, unknown = [], 0
    for a, b in records:
        aa, bb = a['metrics'][metric], b['metrics'][metric]
        if aa['status'].startswith('NOT_APPLICABLE') or bb['status'].startswith('NOT_APPLICABLE'):
            # Symmetric process/state paths are not invented in disabled arms.
            unknown += 1
            continue
        if not finite(aa['value']) or not finite(bb['value']):
            unknown += 1
            continue
        pairs.append({'task_id': a['task_id'], 'chain_id': a['chain_id'], 'domain': a['domain'],
            'repeat': a['repeat'], 'a': {'value': aa['value'], 'success': a['success']},
            'b': {'value': bb['value'], 'success': b['success']}})
    result = {'planned_paired_units': len(records), 'known_paired_units': len(pairs), 'unknown_or_inapplicable_pairs': unknown,
        'baseline_mean': None, 'target_mean': None, 'paired_difference': None, 'relative_change': None,
        'ci95': None, 'status': 'EMPTY_PLANNED_CELL' if not records else 'INCOMPLETE_OR_ASYMMETRIC_APPLICABILITY',
        'both_success_paired_units': sum(a['success'] and b['success'] for a, b in records)}
    if not pairs or unknown:
        return result
    grouped = defaultdict(list)
    for pair in pairs:
        grouped[(pair['domain'], pair['chain_id'], pair['task_id'])].append(pair)
    tasks = []
    for (domain, chain, task), values in sorted(grouped.items()):
        a = sum(r['a']['value'] for r in values) / len(values)
        b = sum(r['b']['value'] for r in values) / len(values)
        tasks.append({'domain': domain, 'chain_id': chain, 'task_id': task, 'a': a, 'b': b,
            'difference': b - a, 'repeat_count': len(values)})
    a = sum(r['a'] for r in tasks) / len(tasks)
    b = sum(r['b'] for r in tasks) / len(tasks)
    result.update({'baseline_mean': a, 'target_mean': b, 'paired_difference': b - a,
        'relative_change': (b - a) / a if a else None, 'unique_tasks': len(tasks),
        'task_pair_distribution': tasks, 'early_failure_warning': result['both_success_paired_units'] < len(records)})
    result.update(chain_resample_ci(tasks, True, seed))
    if metric == 'quality_success':
        quality_pairs = [{**p, 'a': {'success': p['a']['success']}, 'b': {'success': p['b']['success']}} for p in pairs]
        safety = kernel.quality_safety_bound(quality_pairs)
        result['conservative_quality_difference_one_sided_lower95'] = safety['quality_difference_one_sided_lower95']
        result['quality_conservative_ci95'] = safety['quality_conservative_ci95']
        result['quality_uncertainty_method'] = safety['method']
        if len({r['difference'] for r in tasks}) == 1:
            result['ci95'] = None
            result['relative_change_ci95'] = None
            result['relative_change_ci95_status'] = 'CONSTANT_QUALITY_BOOTSTRAP_IS_NOT_A_SAFETY_INTERVAL'
            result['bootstrap_quality_interval_status'] = 'CONSTANT_BOOTSTRAP_NOT_A_VALID_SAFETY_INTERVAL; use conservative bound'
    return result


def write_analysis(destination, context, boundaries, data, kernel, cases, lengths, traces):
    actual = {r['run_id']: r for r in data['task_runs.jsonl'] if r['phase'] != 'public_calibration'}
    records = []
    with (destination / 'secondary_units.jsonl').open('x', encoding='utf-8') as units_file, \
            (destination / 'measurement_events.jsonl').open('x', encoding='utf-8') as events_file:
        for unit in context['plan']:
            if unit['phase'] == 'public_calibration':
                continue
            items = traces.get(unit['run_id'], [])
            record = unit_record(unit, cases[unit['task_id']], actual.get(unit['run_id']), items, lengths[unit['task_id']])
            records.append(record)
            emit(units_file, record)
            for name, number, event in items:
                if name == 'observer_summary.jsonl':
                    continue
                fields = SAFE_STATE_FIELDS if name == 'states.jsonl' else SAFE_INDEX_FIELDS
                emit(events_file, {'run_id': unit['run_id'], 'source_table': name, 'source_line': number,
                    'measurement': {key: event[key] for key in fields if key in event}})
    strata = defaultdict(list)
    for record in records:
        strata[(record['phase'], record['model'], record['model_id'], record['topology'])].append(record)
    coverage = []
    with (destination / 'heterogeneity.jsonl').open('x', encoding='utf-8') as arm_file, \
            (destination / 'paired_heterogeneity.jsonl').open('x', encoding='utf-8') as pair_file:
        for key, stratum in sorted(strata.items()):
            phase, model, model_id, topology = key
            planned_arms = sorted({r['arm'] for r in stratum})
            base = {'phase': phase, 'model': model, 'model_id': model_id, 'topology': topology,
                'inference_scope': 'EXPLORATORY_DESCRIPTIVE_NO_HYPOTHESIS_TEST', 'quality_noninferiority_wording': 'WITHHELD_PROSPECTIVELY'}
            for dimension, level in group_levels(boundaries):
                for facet in ('all_formal', 'state_applicable', 'state_not_applicable'):
                    chosen = select(stratum, dimension, level, facet)
                    descriptor = {**base, 'dimension': dimension, 'level': level, 'population_facet': facet}
                    for arm in planned_arms:
                        rr = [r for r in chosen if r['arm'] == arm]
                        header = {**descriptor, 'arm': arm, 'planned_units': len(rr),
                            'terminal_units': sum(r['terminal_observed'] for r in rr),
                            'missing_units': sum(not r['terminal_observed'] for r in rr),
                            'failed_terminal_units': sum(r['terminal_observed'] and not r['success'] for r in rr),
                            'unique_tasks': len({r['task_id'] for r in rr}),
                            'chain_count': len({(r['domain'], r['chain_id']) for r in rr})}
                        coverage.append(header)
                        emit(arm_file, {**header, 'metrics': {metric: distribution(rr, metric,
                            stable_seed([descriptor, arm, metric])) for metric in METRICS}})
                    # The14 cross-phase matched effects remain solely in d87.
                    if phase not in ('main', 'cross_model', 'multiprocess'):
                        continue
                    for a, b in PAIRS:
                        if a not in planned_arms or b not in planned_arms:
                            continue
                        pair_header = {**descriptor, 'baseline': a, 'target': b}
                        if (a, b) == ('S0', 'SS0') and facet != 'state_applicable':
                            emit(pair_file, {**pair_header, 'status': 'NOT_ESTIMATED_STATE_INCREMENT_REQUIRES_FROZEN_APPLICABLE_FACET',
                                'planned_candidate_pairs': sum(r['arm'] == a for r in chosen)})
                            continue
                        aa = {(r['task_id'], r['repeat']): r for r in chosen if r['arm'] == a}
                        bb = {(r['task_id'], r['repeat']): r for r in chosen if r['arm'] == b}
                        if set(aa) != set(bb):
                            raise ValueError('unmatched preplanned subgroup pair')
                        paired = [(aa[k], bb[k]) for k in sorted(aa)]
                        # State/index scopes with an absent path in either arm
                        # stay in per-arm descriptive output, not a made-up0 pair.
                        pair_metrics = ('quality_success', *TASK_METRICS, 'provider_wait_fraction')
                        emit(pair_file, {**pair_header, 'metrics': {metric: paired_distribution(kernel, paired, metric,
                            stable_seed([pair_header, metric])) for metric in pair_metrics}})
    write_json(destination / 'coverage.json', {'cells': coverage,
        'rule': 'all frozen marginal levels retained, including planned-empty cells; phase/model/topology separate; all_formal descriptions do not replace108 state effects',
        'joint_cells': 'not estimated: mandatory complete marginal coverage only; no adaptive joint-cell mining'})
    return {'planned_formal_units': len(records), 'terminal_formal_units': len(actual),
        'missing_formal_units': len(records) - len(actual), 'strata': len(strata), 'coverage_cells': len(coverage)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='secondary_heterogeneity_v1')
    parser.add_argument('--allow-incomplete', action='store_true',
        help='produce explicitly incomplete planned-denominator descriptions after collection stops; never bypass unmatched-start checks')
    args = parser.parse_args()
    if not args.version or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.version):
        raise ValueError('version must be a simple new directory name')
    # This lock guards provider timing when the native driver uses the registered
    # lock. The complete/unmatched-start checks independently guard raw lifecycle.
    import fcntl
    with (OUT / 'manifests/real_provider_execution.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The provider lock can be briefly free between controller stages. Even
        # --allow-incomplete requires a genuinely closed collection lifecycle.
        lifecycle_path = OUT / 'formal_execution_v1/execution.json'
        lifecycle_sha256 = digest(lifecycle_path)
        lifecycle = load(lifecycle_path)
        if (not lifecycle.get('finished_utc')
                or lifecycle.get('supervised_child_observed_running') is True
                or lifecycle.get('status') not in (
                    'REAL_MATRIX_TERMINAL_AWAIT_INDEPENDENT_AUDIT',
                    'ERROR_RETAINED_NO_AUTOMATIC_REPLAY')):
            raise ValueError('formal collection lifecycle is not terminal; no secondary analysis during real timing')
        gates = load_gates()
        context, boundaries, freeze, contract, cases, lengths = fixed_inputs(gates)
        data = gates.checked_raw(context, public_only=False)
        formal_starts = [r for r in data['task_unit_starts.jsonl'] if r['phase'] != 'public_calibration']
        if formal_starts:
            stamp = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
            first_start = min(stamp(r['start_utc']) for r in formal_starts)
            freeze_stamp = freeze.get('frozen_at_utc', freeze.get('created_utc'))
            if freeze_stamp is None or stamp(freeze_stamp) >= first_start or stamp(contract['created_utc']) >= first_start:
                raise ValueError('preformal freeze/statistical chronology invalid')
        public_ids = context['public_ids']
        actual = {r['run_id'] for r in data['task_runs.jsonl']} - public_ids
        planned = {u['run_id'] for u in context['plan'] if u['phase'] != 'public_calibration'}
        if actual != planned and not args.allow_incomplete:
            raise ValueError('formal collection incomplete; no analysis run during collection')
        for name in gates.RAW:
            if [r for r in data[name] if r['phase'] == 'public_calibration'] != gates.rows(gates.STATS / ('public_' + name)):
                raise ValueError('registered public evidence changed')
        traces, trace_hashes = trace_tables(context, actual)
        kernel = gates.load_verified_v3(contract).frozen
        destination = OUT / 'analysis/postrun' / args.version
        destination.mkdir(parents=True, exist_ok=False)
        summary = write_analysis(destination, context, boundaries, data, kernel, cases, lengths, traces)
        receipt = {'created_utc': datetime.now(timezone.utc).isoformat(), 'study_id': STUDY,
            'status': 'COMPLETE_DESCRIPTIVE_ANALYSIS' if actual == planned else 'INCOMPLETE_DESCRIPTIVE_ANALYSIS',
            'helper_sha256': digest(Path(__file__)), 'frozen_boundaries_sha256': BOUNDARIES_SHA256,
            'original_statistical_entry_sha256': GATES_SHA256,
            'original_formal_freeze_sha256': digest(OUT / 'manifests/formal-freeze.json'),
            'formal_lifecycle_sha256': lifecycle_sha256,
            'helper_development': 'post-freeze helper implementing already frozen operational boundaries; not a frozen collection/execution driver',
            'scope': 'new formal data only; descriptive/exploratory, no p-values, Holm, added conditions or replacement for original10/14 analyses',
            'raw_input_sha256': {name: digest(OUT / 'raw' / name) for name in gates.RAW},
            'trace_input_sha256': trace_hashes, 'summary': summary,
            'metric_definitions': {'task_metrics': TASK_METRICS, 'trace_metrics_event_field_scope': TRACE_METRICS,
                'provider_wait_fraction': 'recorded SDK wait/task wall; not queue or RTT',
                'trace_sums': 'observed per-scope event sums; nested/parallel scopes are not additive task-wall totals',
                'shared_close': 'deduplicate(run_id,shared_release_group); no per-URI time allocation',
                'inapplicable_or_unobserved': 'null with explicit status, never fabricated0',
                'missing_quality': 'original planned-denominator failure rule; quality constant bootstrap CI withheld in favor of paired conservative bound'},
            'files': {p.name: digest(p) for p in sorted(destination.iterdir()) if p.is_file()}}
        write_json(destination / 'receipt.json', receipt)
        print(json.dumps({'status': receipt['status'], 'output': str(destination), **summary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
